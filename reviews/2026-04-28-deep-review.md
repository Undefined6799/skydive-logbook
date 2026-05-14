# Deep Review — 2026-04-28

**Scope.** End-to-end audit of the skydive-logbook codebase, backend and
frontend, against `DECISIONS.md` D1–D45, `CLAUDE.md` invariants, RFC
9457, OWASP XXE, WCAG 2.1 AA, and the existing review record in
`reviews/`. Conducted as nine parallel review tracks (invariants,
decisions drift, security, test coverage, tech debt, API surface,
frontend UX, accessibility, UX copy), each with its own subagent and
primary-source citations, then synthesized here.

**Bar applied to every finding:** is this a real concern with a
file:line + D-entry/RFC/WCAG citation, or is it a nitpick? Nitpicks
were dropped.

**What this review is NOT.** It's not a re-statement of the
2026-04-23 forward review or 2026-04-27 design critique — those stand
on their own. Each carryover item is explicitly marked as "still
open" / "fixed since" / "evolved" so the agents acting on this report
don't duplicate prior work.

---

## 1. TL;DR

**Overall posture.** Backend invariants discipline is intact —
including the previously-CRITICAL D16 catch-all, which is now wired
(`backend/api/rest.py:100–171`). The codebase has continued to
evolve since 2026-04-23: D44 dropzones and D45 environment fields are
shipped end-to-end (XSD, models, services, routes, frontend); D41–D43
attachment-edit endpoints are live. The frontend has been ported from
the HTML mockup to a React + Vite + Tailwind SPA that responds
correctly to most of the 2026-04-27 critique.

**Three things that need a decision before any further code lands.**

1. **D33–D39 Rig Manager scope.** D33 commits the Rig Manager as v0.1
   scope (six per-kind entities, frozen rig snapshots, repack events,
   AAD rules matrix). The XSD, the per-kind Pydantic models, and
   `backend/services/aad_rules.py` **do not exist**. The frontend
   already mocks the post-D33 shape (`frontend/src/mock.js`). Either
   begin Phase R.0 immediately or supersede D33 with a new D-entry
   that pushes it to v0.2 — the contradiction between
   "decided as v0.1" and "no implementation" is the largest single
   piece of drift in the project.

2. **D24 `_hints` channel.** Still completely unimplemented across
   models, services, OpenAPI, and tests. Same call as 2026-04-23
   forward-review §A1: ship the one v0.1 hint code
   (`non_sequential_jump_number`) or supersede D24.

3. **`python-multipart>=0.0.9` floor admits CVE versions.** `uv.lock`
   resolves safely to 0.0.26 today, but the floor in `pyproject.toml`
   permits a future resolver to pick a 0.0.9–0.0.17 version vulnerable
   to CVE-2024-24762, CVE-2024-53981, CVE-2026-40347. One-line tighten
   before ship.

**Eleven findings are agent-fixable in under an hour each.** They are
listed in §2 with file:line, fix sketch, and suggested subagent.

---

## 2. Top priorities (agent-fixable chunks)

Ordered by severity then ease. Each is self-contained — pick the
chunk, hand it to the named subagent with the file:line, and ship as
its own slice per `feedback_small_increments`.

| # | Severity | Subagent | File:line | Effort | What |
|---|----------|----------|-----------|--------|------|
| **P1** | major | backend-engineer | `pyproject.toml:23` | 5 min | Tighten `python-multipart` floor to `>=0.0.26` (CVE-2026-40347 fix) |
| **P2** | major | backend-engineer | `frontend/src/index.css:25` + global rule | 10 min | Replace `outline:none` regression with `:focus-visible` ring (WCAG 2.4.7) |
| **P3** | major | backend-engineer | `frontend/src/index.css` token | 5 min | Bump `#737373` → `#6b7280` for inactive nav text (WCAG 1.4.3 — current ratio 3.24:1) |
| **P4** | major | code-reviewer + frontend-engineer | `frontend/src/modals/JumpDetailModal.jsx` Body | 30 min | Verify D36 — historical jumps must display fields from `rig-snapshot.xml`, NOT live rig. If currently showing live rig, this is a silent-data-rewrite bug |
| **P5** | major | api-contract-steward | `DECISIONS.md` (new D-entry) | 1 hr | Either wire D24 `_hints` (`non_sequential_jump_number`) or supersede D24 with explicit deferral |
| **P6** | major | backend-engineer | `backend/main.py:67–92` | 30 min | Wire `reindex_from_xml` on `schema_was_rebuilt=True` (function already exists at `services/reindex_service.py:157`). Update the stale "does not exist yet" staging comment |
| **P7** | major | api-contract-steward | `backend/api/jumps.py`, `backend/api/dropzones.py` (14 routes) | 2 hr | Wire `responses={...}` kwargs referencing the existing `ProblemDetails` components |
| **P8** | major | api-contract-steward | `DECISIONS.md` (new D-entry) | 30 min | Document Bearer-auth enforcement story OR drop `bearerAuth` security scheme from `backend/api/openapi.py:117–124` until middleware lands |
| **P9** | major | architect (Alex + agent) | `DECISIONS.md` (D33 successor) | 1 hr | Resolve Rig Manager scope: begin Phase R.0 or supersede D33–D39 with deferred D-entries |
| **P10** | major | backend-engineer | `backend/tests/test_crash_recovery.py` + `_crash_child.py` | 4 hr | Add crash-path tests for `update_jump` (rename + manifest + index), `delete_jump` (trash move + index) — CLAUDE.md §7 mandates this |
| **P11** | major | frontend-engineer | All form modals (LogJumpModal, ComponentModal, DropzoneModal) | 2 hr | Add `htmlFor=`/`id=` pairs on every form input (WCAG 1.3.1, 3.3.2) |
| **P12** | major | backend-engineer | new `.github/workflows/ci.yml` | 1 hr | Add CI workflow running `pytest` + `ruff check backend` on push and PR. HANDOFF.md flagged this; still missing (see §9 F-CI-1) |

The remaining ~25 findings (minor / nit / minor-but-defensible) are
catalogued in §3 and §8–§9.

---

## 3. Findings by area

Each finding has: **severity** (blocker / major / minor / nit), **file:line**,
**why it matters** (cite D-entry / RFC / WCAG), **fix sketch**, **suggested
subagent**, and **test to add** if applicable.

### 3.1 Invariants compliance

**All seven CLAUDE.md §5 invariants are upheld in current code.** The
2026-04-23 CRITICAL finding (D16 catch-all missing from middleware
chain) is now resolved: `backend/api/rest.py:100–171` registers an
`@app.exception_handler(Exception)` that wraps unhandled exceptions
as `InternalServerError` and routes through `error_response()`, so
every error crosses the boundary as `application/problem+json`.

| Invariant | Status | Evidence |
|-----------|--------|----------|
| 1. atomic_write on every persisted write (D10) | ✅ | `filesystem.py:173–300`; no raw `open(..., "wb")` outside the helpers |
| 2. XSD validation before write (D2) | ✅ | All write sites: serialize → validate → atomic_write (`jump_service.py:326–331`, `dropzone_service.py:116–120`) |
| 3. Hardened parser on every read (D2) | ✅ | `xml/validator.py:87–113` sets `resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False` |
| 4. safe_join + sanitizers on user-input paths (D4) | ✅ | `filesystem.py:81–170`; verified zip-slip resistant |
| 5. SQLite never authoritative (D3) | ✅ | Every index column derivable from XML; `reindex_from_xml` exists at `services/reindex_service.py:157` |
| 6. RFC 9457 errors only (D16) | ✅ FIXED | `rest.py:100–171` (catch-all) + `errors.py:200–212` (problem+json content-type) |
| 7. NFC normalization on writes (D4) | ✅ | `filesystem.py:42–50,96,119`; `unicodedata.normalize("NFC", ...)` |

**One finding worth a docstring:**

- **F-INV-1 (minor).** `backend/storage/filesystem.py:atomic_write` —
  no docstring acknowledgment of A3 (Darwin `fsync(2)` flushes to
  cache, not platter — `F_FULLFSYNC` would; SQLite's fullfsync pragma
  cites this) or A4 (no parent-directory fsync). Both are real
  trade-offs and worth a paragraph in the docstring naming them, with
  citations to the [Apple fsync(2)
  manual](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/fsync.2.html)
  and [LWN ext4 / data
  loss](https://lwn.net/Articles/322823/). Subagent: backend-engineer.

### 3.2 DECISIONS drift

The audit re-examined every D-entry against current code. Most are
verified; the live drifts are concentrated in three areas.

#### 3.2.1 D33–D39 Rig Manager — committed as v0.1, not implemented

**Evidence (verified 2026-04-28):**

- `backend/models/` contains only `common.py`, `dropzone.py`,
  `equipment.py`, `jump.py`. The per-kind files D33 §Consequences
  names — `main.py`, `reserve.py`, `aad.py`, `container.py`,
  `rig.py`, `jumper.py` — **do not exist**.
- `backend/services/aad_rules.py` (D39's pure-function lookup
  module) **does not exist**.
- `SCHEMA.v1.xsd` still defines `EquipmentRefsType` with
  `container_id / canopy_id / reserve_id / aad_id` (the pre-D33 shape);
  no per-kind component types (`<main>`, `<reserve>`, `<aad>`,
  `<container>`, `<rig>`, `<jumper>`) are present.
- No `rig-snapshot.xml` write path in `jump_service.create_jump`
  (D36 mandates one per jump folder).
- The frontend, however, **has** moved to the post-D33 shape: `mock.js`
  models per-kind components with discipline-specific fields, lineset
  on Main, repacks/rides on Reserve, mode on AAD; `MyRig.jsx` and
  `Inventory.jsx` consume that shape correctly. So the frontend
  expects a backend that does not exist.

**Severity: blocker** for any further Rig Manager UX work, because the
React layer will start hardcoding mock shapes until the backend
catches up.

**Fix:** This is a scope decision, not an agent task. Two paths:

- *Path A — proceed with R.0.* Begin Phase R.0 per the 2026-04-24
  rig-manager-integration plan (§7 of that doc): static per-kind
  entities, read-only, no jump integration. One PR per kind. Land R.0
  before R.1.
- *Path B — defer.* Draft a new D-entry that supersedes D33's "in
  v0.1" claim with an explicit "post-v0.1" position, and roll the
  frontend mock back to the pre-D33 Equipment shape. Keep `mock.js`
  as a forward-looking sketch only if it's commented as such.

Suggested subagent for path A: backend-engineer for R.0 model + XSD
extension; api-contract-steward to author the D-entries; code-reviewer
to second the per-kind XSD shape. For path B: api-contract-steward
authors the deferral.

#### 3.2.2 D26 reindex on schema rebuild — function exists, not wired

- **D-entry promise:** `backend/main.py` should run `reindex_from_xml`
  synchronously when `IndexOpenResult.schema_was_rebuilt == True`,
  and refuse to start if reindex fails.
- **Code reality:** `main.py:84–92` logs a WARNING and continues
  to `uvicorn.run()`. The staging note at lines 67–74 says
  *"`reindex_from_xml` does not exist yet"* — but it **does**
  exist now at `backend/services/reindex_service.py:157`. The
  comment is stale; the wire-up was never done.
- **Severity:** major. After a v0.2 schema bump, the running app
  would serve queries against an empty index until a manual reindex.
- **Fix:** import `reindex_from_xml` in `main.py`, replace the
  WARNING branch with a synchronous call, and refuse to start on
  reindex failure (per D26 §Mechanics). Update the staging note.
  Add a test pinning the new behaviour and another asserting the
  app refuses to start on reindex failure.
- **Subagent:** backend-engineer.

#### 3.2.3 D24 `_hints` channel — still zero implementation

Confirmed identical to 2026-04-23 forward-review §A1. Grep across
`backend/` for `_hints`, `Hint`, `non_sequential_jump_number`,
`build_hint` returns zero matches. No `Hint` schema in OpenAPI; no
sequentiality check in `jump_service`; no response-wrapper field on
the Pydantic models; no tests.

**Severity:** major (documented v0.1 contract item) but **not load-bearing**
(clients ignore unknown fields).

**Fix:** see P5 in §2. One file each in `models/`, `services/`,
`api/errors.py`, `api/openapi.py`, plus one test. Roughly 4 hours.
Or supersede D24 with a 15-minute deferral D-entry. Subagent:
api-contract-steward (decision) → backend-engineer (impl).

#### 3.2.4 Verified per-D-entry status

D1 ✅ · D2 ✅ · D3 ✅ · D4 ✅ · D5 ✅ · D6 ✅ (reserved as designed) ·
D7 ✅ · D8 ✅ · D9 ✅ · D10 ✅ · D11 ✅ structure (packaging not yet
exercised) · D12 ✅ (m/ft toggle in `Settings.jsx:185–211`,
`useAltitudeUnit` hook, `units.js`) · D13 ✅ · D14 ⚠ scope superseded
by D33 · D15 ✅ · D16 ✅ FIXED · D17 ⚠ trash.py uses non-canonical
strftime form (see §3.5 F-DBT-1) · D18 ✅ · D19 ✅ · D20 ✅ · D21 ✅ ·
D22 ✅ enum closed · D23 ✅ uniqueness enforced both layers · D24 🔴
unimplemented · D25 ✅ for `create_jump` only · D26 🔴 reindex not
wired · D27 ✅ · D28 ✅ · D29 ✅ · D30 ✅ multipart · D31 ✅
metadata-only PUT · D32 ✅ audit timestamps · D33–D39 🔴 not in
backend code (frontend mocks) · D40 ⚠ N/A pending D33 · D41 ✅ track ·
D42 ✅ POST attachments · D43 ✅ DELETE attachments · D44 ✅ dropzones
end-to-end · D45 ⚠ environment field on jump.xsd shipped, but
Peelman/lb-budget formula not implemented (deferred to R.4).

### 3.3 Security

**Posture summary**

| Category | Status | Notes |
|----------|--------|-------|
| XXE / billion-laughs | ✅ | DOCTYPE byte-scan + lxml hardening; OWASP-compliant |
| Path traversal | ✅ | safe_join + sanitizers + `is_relative_to` guard |
| Lockfile races (local FS) | ✅ | filelock works; CIFS 5.5+ caveat noted |
| SQLite hardening | ✅ | WAL + synchronous=NORMAL + foreign_keys=ON |
| Multipart streaming | ✅ | `atomic_write_stream`, sanitization, dedup |
| Error envelope leakage | ✅ | catch-all wrapper now in place |
| Bearer-auth advertised vs enforced | 🔴 | scheme in OpenAPI, no middleware |
| Dependency floors | 🔴 | python-multipart admits CVE versions |
| Darwin fsync durability | ⚠ | docstring caveat worth adding |
| SQLite WAL on cloud-sync | ⚠ | deployment-position D-entry needed |

**Findings**

- **F-SEC-1 (major).** `pyproject.toml:23` — `python-multipart>=0.0.9`
  admits CVE-2024-24762 (Content-Type header ReDoS, fixed 0.0.7),
  CVE-2024-53981 (boundary parsing DoS, fixed 0.0.18), and
  CVE-2026-40347 (preamble/epilogue parsing stall, fixed 0.0.26).
  `uv.lock` resolves to 0.0.26 today, so users today are safe; a
  fresh `uv sync --upgrade` on a machine without an existing lockfile
  could land on a vulnerable version. Tighten to `>=0.0.26`. **One
  line.** Subagent: backend-engineer.
  References:
  [CVE-2024-24762](https://github.com/advisories/GHSA-2jv5-9r88-3w3p),
  [CVE-2024-53981](https://nvd.nist.gov/vuln/detail/CVE-2024-53981),
  [CVE-2026-40347](https://github.com/advisories/GHSA-mj87-hwqh-73pj).

- **F-SEC-2 (major, contract drift).** `backend/api/openapi.py:117–124`
  declares a `bearerAuth` security scheme with description *"Required
  only when the server binds to a non-loopback address."* No
  middleware enforces this; `backend/api/deps.py:46–53` returns
  `"default"` without consulting any header; `backend/config.py:76`
  defines `api_key: str | None = None` but it's unused.
  **Concrete failure:** user sets `bind_host = "0.0.0.0"`, opens the
  port, and every route is unauthenticated despite the spec's claim.
  v0.1 default is loopback-only so blast radius is bounded.
  **Fix:** either drop the security scheme from OpenAPI until the
  middleware is wired, or write a new D-entry pinning *"Bearer auth
  is enforced when bind_host is not loopback; implementation lands in
  Phase X"*. Subagent: api-contract-steward.

- **F-SEC-3 (minor, docstring).** `backend/storage/filesystem.py:atomic_write`
  fsyncs the temp file before `os.replace`. On Darwin, BSD `fsync(2)`
  *"does not cause the drive to flush its internal buffer to the
  disk platter"*; `F_FULLFSYNC` does. On all POSIX, parent-directory
  fsync is required for full rename durability per the [POSIX
  rename(2) spec](https://pubs.opengroup.org/onlinepubs/9699919799/functions/rename.html).
  Both are pedantic on modern hardware/filesystems but worth a
  docstring paragraph naming the trade-offs (the docstring already
  has the MoveFileExW Windows caveat). Subagent: backend-engineer.

- **F-SEC-4 (open question, not a vulnerability).** SQLite WAL mode
  (D3 + index.py:147) is incompatible with Dropbox / iCloud /
  OneDrive folders per [SQLite WAL §8](https://www.sqlite.org/wal.html).
  Worst observable outcome is a failed open → friendly error → user
  runs reindex. Worth a deployment position in a new D-entry — three
  options canvassed in 2026-04-23-forward-review §D1 (Cloud-Sync
  position).

- **F-SEC-5 (open question).** No total-request-body cap. Starlette's
  `max_part_size` defaults to 1 MiB per *part*, but the whole body
  can be arbitrarily large. D21 explicitly accepts unlimited
  *attachment* size for v0.1; D21 does not address request-level
  caps. Worth a sentence in D21 (or a successor) saying so.

### 3.4 Test coverage and crash-path

The suite is in good shape — 506 test functions across 29 files, real
tmpdirs throughout (no filesystem mocks), strong coverage of the
hardened parser, atomic_write, manifest, lockfile, RFC 9457 envelope,
and `create_jump`'s crash table. Gaps cluster in three areas.

- **F-TEST-1 (major, CLAUDE.md §7 mandate).** No crash-path tests for
  `update_jump` or `delete_jump`. `update_jump` is a 9-step write
  with at least three multi-file boundaries (jump.xml rewrite,
  SHA256SUMS rewrite, folder rename, index update); `delete_jump` is
  a 2-step trash move + index delete. CLAUDE.md §7 mandates a test
  for *"the half-written case whenever you add a multi-file write."*
  `_crash_child.py` and `test_crash_recovery.py` cover only
  `create_jump` (D25 rows A–D).
  **Fix:** extend `_crash_child.py` with crash points
  `update_after_xml_write`, `update_after_manifest`,
  `update_after_rename`, `delete_after_trash_move`. One test row
  each, asserting the post-crash folder state matches the documented
  invariant. Subagent: backend-engineer. *This is P10 in §2.*

- **F-TEST-2 (minor).** No test pinning the *"XML that fails XSD
  validation is never written"* guarantee (invariant 2). The hardened
  parser has thorough rejection tests, but write-path validation
  refusal has no behaviour test.
  **Fix:** in `test_create_jump.py`, monkey-patch the model to emit
  XML with a missing required element; assert that the create raises
  `ValidationFailedError` AND that the jump folder was not created.
  Subagent: backend-engineer.

- **F-TEST-3 (minor).** No NFC round-trip test. D4 mandates NFC on
  every write; nothing asserts that an input given as NFD round-trips
  as NFC on disk.
  **Fix:** create a jump with title `"Café"` in NFD form
  (`"Cafe\u0301"`), assert the folder name on disk is the NFC byte
  sequence (`"Café"` = `"Caf\u00e9"`). Subagent: backend-engineer.

- **F-TEST-4 (minor).** §A14 forward-review flagged the DOCTYPE
  byte-scan's documented CDATA false-positive with no pinning test.
  Still open. Add a test that documents intent (accept or reject) so
  a future parser refactor can't silently change the posture.
  Subagent: backend-engineer.

- **F-TEST-5 (nit).** `test_create_jump.py:245` has an unused
  `monkeypatch` parameter (dead). Drop it. Subagent: backend-engineer.

- **F-TEST-6 (minor).** D24 `_hints` has no test coverage (because
  the feature has no implementation). Tracked under §3.2.3 — the
  test follows the implementation decision.

### 3.5 Tech debt and code health

- **F-DBT-1 (minor).** `backend/storage/trash.py:30,67` uses
  `datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")` for trash folder
  names. D17 specifies ISO 8601 UTC with millisecond precision and a
  `Z` suffix; every other timestamp in the project uses
  `_now_utc_iso()` (or equivalent) producing
  `2026-04-28T19:03:17.412Z`. Trash is the only place using a
  compact non-ISO form. No runtime risk; consistency only.
  **Fix:** swap the strftime calls for the canonical helper.
  Subagent: backend-engineer.

- **F-DBT-2 (minor, A9 carryover).** `sanitize_folder_name` has no
  byte-length cap; `sanitize_filename` caps at 255. A 120-char title
  (`JumpTitle.max_length=120`) can exceed 255 bytes in pessimal
  UTF-8 (emoji-dense), causing an obscure `OSError` from
  `mkdir` instead of a clean 422.
  **Fix:** accept a `max_bytes: int = 255` parameter on
  `sanitize_folder_name`, encode-then-len check, raise `ValueError`
  with a clear message. Subagent: backend-engineer.

- **F-DBT-3 (minor, A10 carryover).** `_FOLDER_EXCLUDES` in
  `storage/verify.py:58–64` does not include OS-noise filenames
  (`.DS_Store`, `Thumbs.db`, `desktop.ini`, `.AppleDouble`,
  `._*` AppleDouble pairs). Every `verify` after the user has
  browsed the logbook in Finder/Explorer reports them as
  `orphan_file`. UX friction, not correctness.
  **Fix:** add to `_FOLDER_EXCLUDES`. Subagent: backend-engineer.

- **F-DBT-4 (B1 carryover, P7 in §2 — major).** `responses=` kwargs
  missing on all 14 jump + dropzone routes. The shared
  `ProblemDetails` schema and four response components
  (`NotFound` / `Conflict` / `ValidationFailed` / `IntegrityError`)
  are pre-defined in `backend/api/openapi.py:129–143` but never
  referenced from the routes. Runtime is correct; the published
  spec only documents the happy path.
  **Fix:** mechanical. Add `responses={...}` to each `@router.post/put/delete/get`.
  Subagent: api-contract-steward.

- **F-DBT-5 (B2 carryover, low).** Connection-per-request is fine
  today. If/when a stats endpoint runs many queries per request, a
  per-request connection dependency would save the PRAGMA-setting
  cost. Defer to a profile-driven slice.

- **F-DBT-6 (B5 carryover, low).** `PRAGMA busy_timeout` is unset
  (defaults to 0). Under the current single-process + lockfile
  pattern, SQLITE_BUSY is unreachable. Worth setting to 250ms now
  as cost-free insurance against future intra-process concurrency.
  Subagent: backend-engineer.

- **F-DBT-7 (open question, A7 carryover).** `update_jump` has a
  rename/index race window (`backend/services/jump_service.py:617–673`
  per the prior review's line numbers). FastAPI sync handlers run on
  a threadpool; D9 covers inter-process locking only. A polling
  client can hit a 500 if its read lands between the rename and the
  index update. v0.1 single-user makes this rare.
  **Fix:** add an in-process writer lock around multi-step mutations
  in jump_service, OR reorder so index update precedes rename.
  Either way, a new D-entry on intra-process concurrency posture.
  Subagent: code-reviewer (decision) + backend-engineer (impl).

### 3.6 API surface and OpenAPI

The API is well-architected and RFC 9457-conformant at runtime. All
routes follow D7 (≤5 lines of adapter logic). Status codes are
correct (404 for user-isolation per HANDOFF.md gotcha #7; 409 for
jump_number_conflict per D23; 422 for validation; 201/200/204 on
success). `D41/D42/D43/D44` endpoints are all live. Pydantic ↔ XSD
field alignment is verified.

The two open items are both already named:

- **F-API-1 (P5 in §2).** D24 `_hints` channel — see §3.2.3.
- **F-API-2 (P7 in §2).** `responses=` kwargs on routes — see F-DBT-4.

### 3.7 Frontend UX

The React port responds to most of the 2026-04-27 critique correctly.
Notable wins: `EmptyState` everywhere, two-click-confirm delete,
RFC 9457 error banners surfacing `type:` and `status:`, kind-specific
component rendering in `MyRig.jsx`, currency banding with USPA
60-day rule, units toggle with explicit "no data is rewritten"
reassurance, multipart vs metadata-only edit-mode discipline in
`LogJumpModal`. Sidebar nav uses `<button>` (the prior `<div>`
critique is fixed). Banners now have `role="alert"`.

Open items:

- **F-UX-1 (verified 2026-04-28 self-review — major risk, no current
  bug).** `JumpDetailModal.jsx` (778 lines) contains **zero references
  to `rig_snapshot`, `snapshot`, `rig`, `equipment`, `container_id`,
  or `canopy_id`** — the modal currently displays no rig/equipment
  data at all. So there is no current D36 violation. The risk
  activates the moment someone adds rig display to this modal: if
  they pull live rig state instead of a frozen snapshot, every
  component swap silently rewrites historical jumps' apparent
  composition.
  **Action:** before any rig-display work lands here, the engineer
  must confirm D36 backend support (rig-snapshot.xml on jump folder)
  exists OR explicitly stub a snapshot fallback. Add a code-comment
  watchpoint in this file naming D36, so the next contributor
  doesn't have to rediscover the trap. Subagent: frontend-engineer
  to add the comment now; code-reviewer to gate any future PR
  introducing rig display in this modal.

- **F-UX-2 (minor).** `Jumps.jsx:341` search input has no `Cmd+F` /
  `Ctrl+F` keyboard shortcut. With many jumps and the search as the
  primary filter, requiring a click is friction (Nielsen #7). Fix:
  `useEffect` listener, `preventDefault`, focus the input ref, Esc
  clears. ~15 min. Subagent: frontend-engineer.

- **F-UX-3 (minor).** `Jumps.jsx:302` table header is not
  `position: sticky`. Scrolling 100s of jumps loses column context.
  Fix: `position: sticky; top: 0; z-index: 10;` plus a solid
  background so it doesn't look transparent. ~5 min. Subagent:
  frontend-engineer.

- **F-UX-4 (minor).** `Inventory.jsx:76` search input has no
  `onChange` handler — input is purely cosmetic. Fix: add state,
  filter `filtered` by brand/model/serial substring (NFC-folded
  case-insensitive). ~10 min. Subagent: frontend-engineer.

- **F-UX-5 (minor).** No success toast / status feedback after
  save / update / delete. Standard pattern: 2–3 second auto-dismiss
  toast saying "Jump saved" / "Moved to trash". Subagent:
  frontend-engineer.

- **F-UX-6 (minor).** No unsaved-changes warning in `LogJumpModal`.
  Fix: track `isDirty` state, intercept close while dirty, confirm.
  ~20 min. Subagent: frontend-engineer.

- **F-UX-7 (minor).** No Escape-key handler on modals. Standard
  pattern; one `useEffect` per modal. Subagent: frontend-engineer.

- **F-UX-8 (minor).** Currency status (`Jumps.jsx:644`) shows
  "Active / Lapsing / Lapsed" with no tooltip explaining the rule.
  Add `title=` with "USPA 60-day currency rule met" / similar.
  Already flagged in 2026-04-27 finding 6.2 — still open. Subagent:
  frontend-engineer.

- **F-UX-9 (minor).** No verifiable hash-copy affordance on jump
  detail. 2026-04-27 finding 2.2 recommended a "Copy hash" button
  inside `.attach .hash`. Verify in the React port. Subagent:
  frontend-engineer.

- **F-UX-10 (minor).** No "Reveal folder" button on jump detail
  (the prior critique highlighted it as a strength). Verify it
  carried over. Settings has `Reveal` for logbook; jumps should
  too. Subagent: frontend-engineer.

### 3.8 Accessibility (WCAG 2.1 AA)

The React port fixes some prior issues (sidebar `<button>`,
`role="alert"` banners) but introduces a regression and carries
several mockup-era failures forward.

- **F-A11Y-1 (P2 in §2 — major regression, WCAG 2.4.7).**
  `frontend/src/index.css:25` strips focus indication globally:
  `input:focus, select:focus, textarea:focus { outline: none; }` —
  with no `:focus-visible` replacement. Affects every interactive
  element: keyboard-only users cannot see where focus is.
  **Fix:**
  ```css
  :focus-visible {
    outline: 2px solid var(--accent, #2563eb);
    outline-offset: 2px;
    border-radius: 4px;
  }
  input:focus-visible, select:focus-visible, textarea:focus-visible { outline: 2px solid var(--accent, #2563eb); outline-offset: 2px; }
  ```
  Subagent: frontend-engineer. Reference:
  [WCAG 2.4.7 Focus Visible](https://www.w3.org/WAI/WCAG21/Understanding/focus-visible.html).

- **F-A11Y-2 (P3 in §2 — major, WCAG 1.4.3).** `#737373` inactive
  nav text on `#0a0c0e` background measures 3.24:1 — below the 4.5:1
  AA-body threshold. Token tweak to `#6b7280` measures ≈4.83:1.
  **One token change.** Subagent: frontend-engineer.

- **F-A11Y-3 (P11 in §2 — major, WCAG 1.3.1, 2.5.3, 3.3.2).** 15+
  form inputs lack `htmlFor=`/`id=` association in `LogJumpModal`,
  `ComponentModal`, `DropzoneModal`, `AddRigModal`. Implicit
  label-input nesting works in some browsers but is fragile and
  breaks screen-reader announcement.
  **Fix:** every input gets a stable `id`; every label gets
  `htmlFor`. Use a tiny helper to generate ids if needed
  (`React.useId`). Subagent: frontend-engineer.

- **F-A11Y-4 (major, WCAG 2.1.1, 2.5.3).** Icon-only buttons (edit,
  delete, clear in Dropzones, Inventory, modals) lack `aria-label`.
  Screen readers announce them as "button" with no purpose.
  **Fix:** `aria-label={action}` on every icon-only button.
  Subagent: frontend-engineer.

- **F-A11Y-5 (minor, WCAG 1.1.1).** Decorative Lucide icons should
  be `aria-hidden="true"` so they don't double-announce alongside
  visible text labels. Subagent: frontend-engineer.

- **F-A11Y-6 (minor, WCAG 1.3.1).** Tables in `Jumps.jsx` and
  `Inventory.jsx` use a CSS grid for the header row instead of
  semantic `<th scope="col">`. Screen readers can't navigate by
  column header.
  **Fix:** convert to `<table>` with `<thead>/<tbody>/<th
  scope="col">` if a real table is acceptable; otherwise use
  `role="grid"` + `role="columnheader"` ARIA on the divs.
  Subagent: frontend-engineer.

- **F-A11Y-7 (minor, WCAG 4.1.2).** Modals likely lack focus trap.
  When a modal opens, focus should move into the modal; Tab should
  cycle within it; Esc should close + return focus to the trigger.
  **Fix:** introduce a `useFocusTrap` hook (lightweight homegrown,
  or pull `focus-trap-react` if comfortable). Subagent:
  frontend-engineer.

- **F-A11Y-8 (minor, WCAG 1.4.3).** `.tag.ok` (status green) is
  flagged borderline at 4.00:1 by the prior critique. Re-verify
  against current tokens; if still <4.5:1, deepen to `#0f7344`.
  Subagent: frontend-engineer.

### 3.9 UX copy

The current voice — precise, calm, technical, action-oriented — is
strong and should be preserved. Twelve specific edits worth making:

- **F-COPY-1 (minor).** Rename destructive actions per D19:
  `"Delete"` → `"Move to trash"`, and the armed state
  `"Click again to confirm"` → `"Confirm to move to trash"`.
  Affects jump rows, dropzone cards, inventory rows. Subagent:
  frontend-engineer.

- **F-COPY-2 (minor).** Replace the sparkle-emoji placeholder in
  `LogJumpModal.jsx:1242` (`"≈ {estimate}s — click ✨ to fill"`)
  with a render-stable form: *"≈ {estimate}s — click Estimate to
  fill"*. Emoji rendering varies across pywebview platforms.
  Subagent: frontend-engineer.

- **F-COPY-3 (minor).** Add success toasts after save / update /
  delete. Drives F-UX-5 above.

- **F-COPY-4 (minor).** Wrap dev-facing hint *"Is uvicorn running on
  localhost:8000? Try `python -m uvicorn …`"* (`Jumps.jsx:555`) in
  a smaller-type collapsible "Debug info" disclosure. Keep the
  technical detail; reduce visual weight for non-dev users.

- **F-COPY-5 (minor).** Standardise empty-state grammar across
  views: *"No \[resource\] yet."* for empty;
  *"No \[resource\] match your filters."* for filtered-zero. Most
  views already follow this — verify Inventory uses *"this filter"*
  vs *"your filters"* consistently.

- **F-COPY-6 (minor).** Verify all backend RFC 9457 `detail` strings
  answer "what failed + why + what to try". Spot-check the integrity
  errors in particular (they tend to be terse).

- **F-COPY-7 through F-COPY-12.** See the full UX-copy track table
  for the rest; all are minor polish, none block ship.

---

## 4. What's already strong — keep these

So that fixes don't accidentally regress strengths:

- The RFC 9457 error banner that surfaces `type:` and `status:` to
  the user (`Jumps.jsx:551–576`) is gold-standard and should be
  reused everywhere errors are shown.
- The "Will validate against SCHEMA.v1.xsd before write" copy in
  `LogJumpModal` sets correct expectations about a strict-validation
  backend; many apps don't explain *why* their forms bounce.
- The trust footer with schema version + last-verified time + index
  status is rare in consumer software; expand it (e.g. amber when
  verify shows issues), don't drop it.
- Mono font for IDs / hashes / timestamps is the right typographic
  signal for "system data" vs "user data."
- The `[<jump#>] <title>` folder naming with NFC normalization and
  Windows-reserved-name rejection is correct and load-bearing — D4
  is one of the most carefully-thought-out decisions in the project.
- The crash harness in `_crash_child.py` is exemplary; extending it
  (F-TEST-1) is the right next step, not rewriting it.
- The two-click-confirm delete pattern (no modal, just an armed
  state) is a clean middle ground between "click and gone" and
  "modal interruption" — keep it, just rename "Delete" → "Move to
  trash."

---

## 5. Deferred / out-of-scope

These were flagged but do not need work this pass. Keeping them
listed prevents an agent from accidentally pulling them into a
v0.1 slice.

- FlySight CSV parsing (D14 §Deferred).
- Digital signing (D6 §Reserved; ready for `<signature>` element).
- Multi-user accounts (D8 §Deferred).
- Imports from other logbook apps.
- Video thumbnails / preview rendering.
- Mobile / headless-server / auto-update.
- LAN exposure with non-loopback bind (gated by F-SEC-2 decision).
- Equipment retire/reactivate UI affordance (Inventory `⋯` menu) —
  defer to Rig Manager Phase R.x.
- `.btn.sm min-width: 24px` (WCAG 2.2 SC 2.5.8) — only required
  if the project commits to WCAG 2.2.
- Skip-to-content link — single-window pywebview app, marginal.
- Long-path support (`\\?\` prefix on Windows) — rare in practice;
  byte-cap from F-DBT-2 covers the user-facing failure mode.

---

## 6. Suggested execution sequence

If Alex wants to ship most of this without thinking about ordering:

1. **First commit** (P1 + P2 + P3 — security and a11y critical wins,
   ~30 min total). One PR. Subagent: backend-engineer for the
   pyproject bump, frontend-engineer for the two CSS changes.
2. **Second commit** (P5 decision — D24 ship or defer; whichever
   path, write the D-entry first, then the code). One PR.
3. **Third commit** (P6 — wire `reindex_from_xml` into `main.py`).
   One PR with tests pinning the new behaviour and the refusal-on-
   reindex-failure path.
4. **Fourth commit** (P7 — `responses=` wiring on routes). One PR;
   pure documentation, no logic change.
5. **Fifth commit** (P8 decision — Bearer auth scope). Either drop
   the OpenAPI declaration (1 line) or write the D-entry pinning
   enforcement.
6. **Sixth commit** (P10 — `update_jump`/`delete_jump` crash tests).
   One PR. May surface real bugs; if so, separate fix slices.
7. **Seventh commit** (P11 + F-A11Y-4 + F-A11Y-5 — form labels and
   icon ARIA labels). One PR per modal would also be reasonable.
8. **D33 decision (P9).** Out-of-band. If Path A (proceed), Phase
   R.0 is its own multi-PR rollout and should not block earlier
   commits.

The chunks above are all independent of each other. Mix and match.

---

## 7. References

### Primary sources cited

- [RFC 9457 — Problem Details for HTTP APIs](https://www.rfc-editor.org/rfc/rfc9457)
- [RFC 6901 — JSON Pointer](https://www.rfc-editor.org/rfc/rfc6901)
- [RFC 7578 — multipart/form-data](https://www.rfc-editor.org/rfc/rfc7578)
- [WCAG 2.1 Recommendation](https://www.w3.org/TR/WCAG21/)
- [WCAG 2.4.7 Focus Visible — Understanding](https://www.w3.org/WAI/WCAG21/Understanding/focus-visible.html)
- [WCAG 1.4.3 Contrast — Understanding](https://www.w3.org/WAI/WCAG21/Understanding/contrast-minimum.html)
- [OWASP XML External Entity Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/XML_External_Entity_Prevention_Cheat_Sheet.html)
- [Apple fsync(2) manual](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/fsync.2.html)
- [SQLite WAL §8 (incompatible filesystems)](https://www.sqlite.org/wal.html)
- [SQLite PRAGMA fullfsync](https://www.sqlite.org/pragma.html#pragma_fullfsync)
- [Linux flock(2) — CIFS 5.5 details](https://man7.org/linux/man-pages/man2/flock.2.html)
- [POSIX rename(2)](https://pubs.opengroup.org/onlinepubs/9699919799/functions/rename.html)
- [LWN — ext4 and data loss](https://lwn.net/Articles/322823/)
- [Microsoft — File naming, reserved names](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file)
- [WebAIM Contrast Checker](https://webaim.org/resources/contrastchecker)
- [ARIA Authoring Practices Guide](https://www.w3.org/WAI/ARIA/apg/)
- Apple HIG / Material Design 3 / 18F Content Guide (UX copy
  references in §3.9)

### CVEs

- [CVE-2024-24762 (python-multipart Content-Type ReDoS)](https://github.com/advisories/GHSA-2jv5-9r88-3w3p)
- [CVE-2024-53981 (python-multipart boundary DoS)](https://nvd.nist.gov/vuln/detail/CVE-2024-53981)
- [CVE-2026-40347 (python-multipart preamble/epilogue DoS)](https://github.com/advisories/GHSA-mj87-hwqh-73pj)

### Project sources

- `CLAUDE.md` (root + `skydive-logbook/`)
- `DECISIONS.md` D1–D45
- `ARCHITECTURE.md`, `HANDOFF.md` (HANDOFF is stale at 32 D-entries
  vs the live 45; worth a refresh post-D33-decision)
- `reviews/2026-04-23-invariants.md`
- `reviews/2026-04-23-decisions-drift.md`
- `reviews/2026-04-23-tests.md`
- `reviews/2026-04-23-api.md`
- `reviews/2026-04-23-research-platform.md`
- `reviews/2026-04-23-research-sqlite.md`
- `reviews/2026-04-23-research-multipart.md`
- `reviews/2026-04-23-forward-review.md`
- `reviews/2026-04-24-rig-manager-integration.md`
- `reviews/2026-04-27-design-critique-mockup.md`

---

## 8. Self-review additions (2026-04-28 pass 1)

Findings the parallel tracks didn't raise but a self-review surfaced:

- **F-SELF-1 (clarifying, not a bug).** `backend/services/equipment_service.py`
  is a 6-line stub explicitly labelled "Scaffold only — implementation
  lands after the first jump operation is end-to-end (task #4)." No
  equipment routes are wired in `backend/api/rest.py`. The frontend's
  Inventory.jsx and ComponentModal.jsx therefore consume `mock.js`
  with no backend integration. This matches §3.2.1's drift framing
  and is not new drift, but worth pinning so future PRs don't
  accidentally start using `equipment_service` as if it existed.
  Recommendation: when D33's scope decision lands, this stub is
  either replaced (Path A) or deleted (Path B). Don't leave it
  half-alive.

- **F-SELF-2 (minor, D5 deferral).** Per D5, every jump folder may
  carry a derived `summary.md`. Code search shows the file is
  comment-referenced in `jump_service.py:12,207` and excluded from
  manifests (`storage/manifest.py:41`), but `create_jump` does not
  write it (the docstring says "5. summary.md — deferred (D5)").
  Reading paths regenerate it lazily? — no, no such code exists.
  D5 is silent on whether absence is acceptable in v0.1; the
  consequences paragraph says *"failure to write it does not fail
  the jump save"*, implying writes are attempted. Currently no
  attempt is made. Worth either writing the summary on create
  (small) or adding a D-entry that pins "summary.md is post-v0.1".
  Subagent: backend-engineer.

- **F-SELF-3 (open question, may be a real gap).** No
  `GET /api/v1/jumps/{id}/attachments/{filename}` endpoint exists.
  The route table is: POST create, GET list, GET single,
  PUT update, GET `/files` listing, POST `/attachments`, POST
  `/attachments/track`, DELETE `/attachments/{filename}`, DELETE
  jump. There is **no individual attachment download endpoint.**
  In a desktop deployment, `backend/scripts/launch_desktop.py`
  appears to serve the React static bundle on the same uvicorn
  origin — but that's the SPA, not user attachment files.
  How does the user view a jump video they uploaded? Either the
  React app reads them via `Reveal folder` (OS-native) or they're
  inaccessible inside the app. **Action:** verify intent. If
  in-app preview is desired, a streaming GET endpoint is needed
  with appropriate Content-Type and Content-Disposition. If
  out-of-scope, document that decision. Subagent: api-contract-
  steward to write a D-entry pinning the intent; backend-engineer
  to implement if needed.

- **F-SELF-4 (minor, test gap).** `backend/scripts/launch_desktop.py`
  is 605 lines (npm install/build, pywebview window, folder picker,
  uvicorn daemon thread, JS bridge for Settings → folder pick →
  restart). Zero tests. Cross-platform behaviour (macOS / Windows /
  Linux) is the riskiest surface in the project from a "won't
  package cleanly" standpoint. CLAUDE.md §7 mandates tests for
  multi-step flows. Even a smoke test (boots, serves a known
  endpoint, exits) would help. Subagent: backend-engineer.

- **F-SELF-5 (test gap, extends F-TEST-1).** Crash-path tests for
  D44 dropzone CRUD are also missing. Dropzone create and update
  go: serialize → validate → atomic_write → index upsert. Two
  multi-step writes that, if interrupted, leave the index out of
  sync with disk XML — same crash class as `update_jump`. Should
  be added to the same `_crash_child.py` extension as F-TEST-1.

- **F-SELF-6 (minor, RFC 6901).** Worth verifying that error
  `pointer` strings in `errors[]` properly escape `~` → `~0` and
  `/` → `~1` per RFC 6901 §3. Field names in this project don't
  contain those characters today (`exit_altitude_m`, `jump_number`,
  etc.), so the bug is latent. If a future field contains a slash
  or tilde the pointer encoder will emit a malformed pointer that
  RFC-9457-strict clients reject. Subagent: backend-engineer to
  audit `backend/api/errors.py` and any service that builds
  pointers; add a unit test fixing a pointer for a field name
  containing `/` and `~`.

- **F-SELF-7 (minor, dependency posture).** Beyond
  `python-multipart` (§F-SEC-1), the rest of the dependency floors
  in `pyproject.toml` were not audited for CVEs in this pass.
  Worth a follow-up `pip-audit` or `uv pip audit` run. Notable
  candidates: `lxml` (XXE history), `fastapi`, `pydantic`,
  `filelock`. Cost: 5 minutes. Subagent: backend-engineer.

These additions don't change any of the §2 priorities; they extend
§3.4 (test coverage) and §3.5 (tech debt).

---

## 9. Pass-2 self-review additions

A second adversarial pass — verifying specific claims and looking for
project-level gaps that the per-track audits would not naturally
catch — found five items.

**Verifications that held:**

- `backend/api/rest.py:100–171` — D16 catch-all is real, well-commented,
  and even cites D20's loopback-only assumption to justify echoing the
  exception type in `detail` for v0.1. Confirmed FIXED.
- `backend/services/reindex_service.py:157` — `reindex_from_xml`
  exists and is properly hardened (uses the hardened parser, only
  writes to SQLite, idempotent). Confirms F-DRIFT-2 is wiring drift,
  not implementation drift.
- `pyproject.toml:7` — `requires-python = ">=3.11"` matches D15.
- `LICENSE` — MIT, matches D13.
- `README.md` (110 lines) — reasonable user-facing summary.

**New findings:**

- **F-CI-1 (major).** `.github/` does not exist. There is no CI of
  any kind — no test runner, no ruff check, no matrix for Python
  3.11/3.12/3.13 × Linux/macOS/Windows. HANDOFF.md flagged this in
  its "deployment prerequisites" list but it remained unaddressed.
  For a project shipping as a native bundle on three OSes, this is
  the #1 piece of release infrastructure missing. Cost-free for now;
  will become urgent the moment a contributor opens a PR.
  **Fix:** a single `.github/workflows/ci.yml` running `pytest` and
  `ruff check backend` on push and PR. Matrix optional. Subagent:
  backend-engineer; or treat as Alex's call.

- **F-CI-2 (minor).** `frontend/vite.config.js.timestamp-*.mjs`
  files (12 of them, dating from Vite's HMR-driven config recompile)
  are present in the repo and not ignored. They're transient and
  should never be committed. **Fix:** add
  `frontend/vite.config.js.timestamp-*.mjs` to `.gitignore`; remove
  the existing ones with `git rm`. Subagent: frontend-engineer.

- **F-CI-3 (minor).** `.gitignore` does not exclude `frontend/node_modules`,
  `frontend/dist`, or any frontend build artifacts. The current
  exclude list (`__pycache__/`, `.venv/`, `build/`, `dist/`, etc.)
  is Python-only, written before the frontend was scaffolded.
  **Fix:** add `frontend/node_modules/`, `frontend/dist/`,
  `frontend/.vite/`, and the timestamp pattern from F-CI-2.
  Subagent: frontend-engineer.

- **F-SQL-1 (minor, defensible).** Two SQL constructions in
  `backend/storage/index.py` use f-string interpolation rather than
  `?` placeholders:
  - Line 159: `conn.execute(f"DROP TABLE IF EXISTS {row['name']}")`
    — D26 calls this out explicitly and the value comes from
    `sqlite_master` (a system table, not user input), so the
    interpolation is safe by source-trust. Worth a one-line
    comment naming the trust dependency for the next reader.
  - Line 232: `conn.execute(f"PRAGMA user_version = {int(version)}")`
    — SQLite does not allow `?` placeholders inside `PRAGMA`
    statements, and the `int(version)` cast is the safety guard.
    Idiomatic; worth a one-line comment naming both constraints.
  
  Neither is a vulnerability. Both deserve an inline `# Per D26: safe
  because ...` comment so the next greppable scan ("hey, why is this
  using f-string SQL?") doesn't have to re-derive the rationale.
  Subagent: backend-engineer.

- **F-TYPE-1 (minor, code-health).** `pyproject.toml` configures
  `ruff` but not `mypy` (or `pyright`). Type-checking isn't enforced
  beyond ruff's lint rules, which means `: Any` overuse and missing
  annotations are not surfaced. For a service-layer-heavy codebase
  with strict invariants this is a real gap.
  **Fix:** add `[tool.mypy]` (or `[tool.pyright]`) to `pyproject.toml`
  with `strict = true` (or equivalent). Run, fix the inevitable
  diagnostics, and add to F-CI-1's CI workflow. Larger effort —
  several hours — but high-leverage. Subagent: backend-engineer.

These additions take the priority count to 12 (P12 = F-CI-1 / CI
setup). The rest are minor and ride alongside other slices.

---

## 10. Methodology and limits

This review was produced by:

1. **Phase 1 — orient.** Read all of CLAUDE.md, DECISIONS.md (4000+
   lines, D1–D45), ARCHITECTURE.md, HANDOFF.md, and every prior
   review in `reviews/`. Listed the full backend Python tree and the
   React frontend tree. Calibrated against what was already known.

2. **Phase 2 — nine parallel review tracks** (one subagent each, all
   authorized to take unrushed time and required to cite primary
   sources): invariants compliance, DECISIONS drift, security &
   hardened parser, test coverage & crash-path, tech debt &
   code-health, API surface & OpenAPI, frontend UX critique,
   frontend WCAG 2.1 AA accessibility, UX copy.

3. **Phase 3 — synthesis.** This document. Cross-checks done before
   writing: verified `reindex_from_xml` does exist (drift agent's
   "doesn't exist" claim was wrong); verified per-kind component
   models do not exist; verified the python-multipart pin; verified
   the `:focus-visible` regression in current `index.css`; verified
   the XSD has D44/D45 fields but not D33–D39 types.

4. **Phase 4 — pass 1 self-review** (§8 above) added six items the
   parallel tracks missed (equipment stub status, summary.md
   deferral, missing GET attachment endpoint, no launch_desktop
   tests, dropzone crash-test gap, RFC 6901 pointer escaping).

5. **Phase 4 — pass 2 self-review** (§9 above) verified specific
   load-bearing claims against current code (D16 catch-all,
   `reindex_from_xml` location, multipart pin, focus-visible
   regression, Python pin, LICENSE) and surfaced project-level
   gaps (no CI, vite timestamp files committed, frontend exclusions
   missing from `.gitignore`, two defensible f-string SQL sites
   worth a comment, no mypy/pyright type checking).

   **Pass 2 originally planned a fresh-eyes subagent** that had not
   seen the parallel tracks; that agent timed out before producing
   output. The verification was completed manually instead. A
   future review could redo the fresh-eyes pass with a tighter
   brief (15–20 minute timebox, single section to challenge) if
   independent challenge is needed before any P1–P12 chunk lands.

**Known limits of this review.**

- Frontend tracks (UX, a11y, copy) read source but did not run the
  app in a browser; behaviour-only checks (focus order during
  actual keyboard navigation, screen-reader announcement, color
  appearance under macOS Dark Mode vs forced-colors) need a real
  manual pass before AA can be claimed. Treat the §3.7–§3.9
  findings as a *targeted* list, not a complete one.
- Crash-path findings are derived from reading the test file and
  D25's table; no fault injection was performed. The
  `_crash_child.py` extension proposed in F-TEST-1 / F-SELF-5 may
  uncover a real bug the static review missed.
- No `pip-audit` / `uv pip audit` was run for dependencies beyond
  `python-multipart` (F-SELF-7). A clean audit would tighten the
  posture statement.
- The frontend agent's "rig snapshot" question was self-resolved
  in §3.7 F-UX-1 (verified: zero rig display in JumpDetailModal
  today), but the broader question of how the React layer will
  consume D36 backend support, when D36 lands, is still open.
- D33's "in v0.1" claim needs an executive decision that no review
  agent can make. §3.2.1's Path A vs Path B framing is the cleanest
  way to surface it.
