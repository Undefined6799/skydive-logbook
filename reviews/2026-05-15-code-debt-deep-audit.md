# Code Debt Deep Audit — 2026-05-15

**Scope:** Full repo (backend, frontend, tests, scripts, CI, docs).
**Lenses:** correctness/invariants, algorithmic complexity, concurrency,
API contract, code health, test gaps, security, repo hygiene.
**Posture:** report-only. Every finding cites code on disk per CLAUDE.md §2.
**Companion to:** `reviews/2026-05-14-tech-debt-audit.md` (which this
re-verifies and extends).

This audit assumes the reader has read CLAUDE.md and skimmed the
2026-05-14 audit. Items the prior audit covered and that I confirm are
*still open* are marked **OPEN-FROM-PRIOR**; items the prior audit
flagged that have since shipped or no longer apply are marked
**CLOSED-SINCE-PRIOR**. Everything else is new.

---

## TL;DR

Verified baseline (run from this audit's machine):

| Tool | Result |
|---|---|
| `ruff check backend` | **clean** |
| `pyright backend` | **0 errors, 0 warnings, 0 informations** |
| `pytest backend/tests` | **1737 passed, 1 skipped (Darwin-only)** in 153s |
| frontend `npm test` | **65 passed (4 files)** in 3.75s |

The codebase remains in genuinely strong shape on the load-bearing
invariants (D2 XML+XSD, D3 SQLite-not-authoritative, D4 path safety,
D10 atomic writes, D16 RFC 9457, D27 correlation, hardened parser).
Every cleanup item flagged P1 in the 2026-05-14 audit (`.bak*` files,
vitest/vite timestamps, `.DS_Store`, `.venv*` clutter, `_write_lock.py`
docstring, `__import__("sys")` smell) has been resolved on disk.

The **new** debt this audit surfaces clusters in five areas:

1. **OpenAPI contract is thinner than it looks.** Zero of 82 routes
   declare `responses=` so 4xx/5xx envelopes aren't part of the
   published spec. No `operation_id` overrides, so generated SDK method
   names get the `_route` suffix from handler names. Schema for
   `ProblemDetails` is hand-authored and unguarded by alignment tests.
2. **Cross-entity write atomicity for rigs (D37) is not implemented**
   end-to-end. `create_rig` writes `rig.xml` referencing four
   components, then in a second pass back-links each component to the
   rig. A crash between the two leaves the logbook in an inconsistent
   state that's documented as "reconcile heals it" — but the rig
   reconcile pass is "a future slice" that hasn't shipped. There is no
   test for crash recovery in this flow.
3. **Index schema-version mismatch silently downgrades.** `open_index`
   drops user tables and re-installs the local code's schema on *any*
   `user_version` mismatch — including the case where the on-disk
   version is *newer* than the running app's version. The app boots
   without warning the user that they may be running an older binary
   against a newer logbook.
4. **No request-level size cap; no idempotency on multipart creates.**
   `python-multipart` floor is patched against known CVEs, but the
   FastAPI app sets no total-request size limit, and `POST /jumps`
   (multipart create with attachments) is not idempotency-keyed — a
   network retry on a half-uploaded jump creates duplicates.
5. **Frontend complexity has structural, not just LOC, debt.** Beyond
   the 2353-LOC `LogJumpModal.jsx` that the prior audit already named,
   the modal carries 10+ independent `useState` flags whose
   load/failure states can drift; several `useEffect` deps are
   incomplete around edit-mode hydration; multiple GETs silently
   `.catch(() => {})` without surfacing failure to the user.

None of these are *invariants-broken* today. They are the kind of debt
that holds up multi-user / network exposure (D48), painful schema
bumps, or any client that does its own retry policy.

---

## Findings — by lens

### 1. API contract, OpenAPI, and HTTP semantics

#### 1.1 [P1] **Zero routes declare `responses=` — 4xx/5xx envelopes missing from OpenAPI**

`backend/api/openapi.py:42-104` defines `PROBLEM_DETAILS_SCHEMA` and
`backend/api/openapi.py:131` even has a comment showing how to wire it
into a route (`responses={"404": {"$ref": "#/components/responses/NotFound"}}`).
But:

```
$ grep -nE "responses\s*=\s*\{" backend/api/*.py
backend/api/openapi.py:131:    # line (`responses={"404": {"$ref": ...
```

That single hit is **inside a comment**. Across the eight entity
routers (44 routes total in `rigs.py`/`jumps.py`/`jumpers.py`/`dropzones.py`/
`containers.py`/`aads.py`/`mains.py`/`reserves.py`/`people.py`/`onboarding.py`/`ops.py`),
zero routes declare their non-200 response shapes. Consequence:

- Every route's OpenAPI entry advertises 200 (or 201) + FastAPI's
  default 422 validation envelope. The RFC 9457 problem+json bodies
  that 404/409/422/500/503 actually return are **invisible to
  consumers** of `/openapi.json`.
- The `#/components/responses/NotFound`, `Conflict`, `ValidationFailed`,
  `Internal` reusable refs in `openapi.py:107-149` are dead code.
- An SDK generator (`openapi-typescript`, `orval`, openapi-generator)
  will produce client code with no error-shape type — so the SPA's
  hand-rolled `ApiError` parsing in `frontend/src/api.js` is the only
  guard against an undocumented field appearing.

**Fix:** add a single helper:

```python
ERROR_RESPONSES = {
    "404": {"$ref": "#/components/responses/NotFound"},
    "409": {"$ref": "#/components/responses/Conflict"},
    "422": {"$ref": "#/components/responses/ValidationFailed"},
    "500": {"$ref": "#/components/responses/Internal"},
}
```

and attach `responses=ERROR_RESPONSES` to every route. ~2 hours.

#### 1.2 [P2] **Hand-authored `ProblemDetails` schema is not guarded by an alignment test**

`backend/api/openapi.py:42-104` is a hand-written JSON Schema; the
Pydantic source of truth is `backend/api/errors.py:107-129`. The
2026-05-14 audit (§1.2) already flagged this — **OPEN-FROM-PRIOR**. The
fix is one regression test, ~30 minutes:

```python
# backend/tests/test_openapi_problem_details_alignment.py
def test_problem_details_schema_covers_pydantic_fields():
    pyd = ProblemDetails.model_json_schema()
    hand = PROBLEM_DETAILS_SCHEMA
    assert set(pyd["properties"]) <= set(hand["properties"])
```

#### 1.3 [P2] **No `operation_id` overrides — SDK method names carry `_route` suffix**

```
$ grep -nE "operation_id" backend/api/*.py
(no output)
```

FastAPI's default operationId is the handler's qualified name —
`create_jump_route`, `list_jumps_route`, etc. Every generated client
SDK method inherits the suffix:

```ts
api.createJumpRoute(...)  // not api.createJump(...)
```

Cheap one-token fix per route (`@router.post(..., operation_id="create_jump")`),
high readability payoff for any downstream codegen. Compounds with §1.1
above — if you fix one you should fix both in the same pass.

#### 1.4 [P1] **Multipart `POST /api/v1/jumps` is not idempotent and has no `Idempotency-Key` support**

`backend/api/jumps.py:131-189` — the create route. The service-layer
collision check (D23 prefix scan on `jumps/[<N>]`) is the only
duplicate guard. But the jump *number* is part of the payload — a
client retry that has already received the 201 but lost the response
will retry with a fresh `jump_number` (the SPA auto-increments) and
the server will happily create a second jump from the same uploaded
bytes.

For loopback v0.1 this is academic. For D48 LAN exposure (or any
HTTP-1.1 keep-alive across an iffy connection during a 1 GiB video
upload) this becomes a real "I uploaded the same video twice" footgun.

**Fix shape:** accept an `Idempotency-Key` header (RFC 9413 / Stripe
convention), store `(user_id, key) → jump_id` for a TTL (24h?), return
the prior `Jump` on retry within the window. ~1 day including tests.
Document as a D-entry.

#### 1.5 [P2] **No total-request-size cap; only per-file via Starlette's `SpooledTemporaryFile`**

```
$ grep -rE "MAX_(BODY|UPLOAD|REQUEST)|max_size|client_max" backend/
(no production hits — only the 1 MiB spool comment in jumps.py:72 and
 the 255-char filename cap in filesystem.py)
```

`POST /api/v1/jumps` takes `list[UploadFile]` with no declared total
cap. A malicious or buggy client can POST 100 GiB of attachments —
they will stream to disk via `atomic_write_stream` (memory-bounded,
correct on that axis) but will fill the user's disk before any
content-length sanity check fires. Also true of `POST /jumps/{id}/attachments`
(D42).

**Fix:** add a Starlette middleware that enforces a configurable
`MAX_REQUEST_BYTES` (default 5 GiB; user-overridable for video-heavy
jumpers via `Settings`). ~1 hour. Cite as a hardening D-entry alongside
the CVE-floor work already done in `pyproject.toml`.

#### 1.6 [P2] **No `Cache-Control` headers on GETs — server side**

The 2026-05-14 audit (§2.3) flagged this from the *frontend* side
(`api.js` defensively sets `cache: 'no-store'`). The server-side fix is
still untaken — **OPEN-FROM-PRIOR**. Every `GET` returns the default
Starlette response with no `Cache-Control`, no `ETag`, no
`Last-Modified`. The WKWebView pywebview backend caches aggressively
in some configurations; the client's blanket `no-store` is a defensive
shim that works at the cost of every request paying full path.

Cheapest move: a middleware that adds `Cache-Control: no-store` to
every response. Better move: per-route policy (`stats` is more
cache-friendly than `jumps/{id}`; both could carry an `ETag` derived
from `updated_at`).

#### 1.7 [NIT] **`backend/api/jumps.py:287` still uses `f.__dict__`**

`return [FolderFileResponse(**f.__dict__) for f in files]` —
2026-05-14 §3.8 flagged this. **OPEN-FROM-PRIOR.** `f` is a frozen
dataclass; the idiomatic conversion is `dataclasses.asdict(f)`. Five
characters.

#### 1.8 **Note on async/sync route handlers — claim INVALID**

A sibling investigation flagged that every route handler is declared
`def` not `async def` and asserted this blocks the event loop. That is
**wrong for FastAPI**: per Starlette/FastAPI docs, a sync `def`
endpoint is dispatched onto an external threadpool
(`anyio.to_thread.run_sync`) so the event loop is not blocked. The
real trade-off is threadpool *size* (default 40 tokens) — under heavy
concurrent file I/O the threadpool could become the bottleneck — but
the v0.1 single-user posture makes this irrelevant. **No action.**

---

### 2. Storage and concurrency

#### 2.1 [P1] **`open_index` silently downgrades schema when on-disk version > code version**

`backend/storage/index.py:344-355`:

```python
# Branch 3: version mismatch in either direction. Drop every user
# table (indexes and triggers cascade), reinstall, restamp. The
# caller is now responsible for reindexing from XML.
_drop_user_tables(conn)
conn.executescript(_SCHEMA)
_set_user_version(conn, INDEX_SCHEMA_VERSION)
```

The "either direction" branch is the issue. If a user runs v0.2 of the
app (say `INDEX_SCHEMA_VERSION=12`), then opens the same logbook with
an older v0.1 binary (`INDEX_SCHEMA_VERSION=10`):

1. `previous_version = 12`, code says 10 — Branch 3 fires.
2. Tables are dropped (including the v12 columns), v10 schema is
   reinstalled, version restamped to 10.
3. The next time v0.2 boots against the same logbook, *it* sees v10 on
   disk, drops again, reinstalls v12, reindexes.

This is correct in the sense that XML is authoritative and the index
is rebuildable. But:

- It's **silent**. The user never learns they ran an old binary.
- It throws away the v12 index for nothing — the next v0.2 boot
  pays the reindex cost.
- It risks losing index state that v0.2 added but v0.1 doesn't know
  to repopulate (any column whose authoritative source has *also*
  moved — e.g., a v0.2 schema that adds a column derived from a
  v0.2-only XML element will leave v0.1's reindex producing rows
  without it).

**Fix:**

```python
if previous_version > INDEX_SCHEMA_VERSION:
    raise IndexVersionError(
        f"logbook index schema is v{previous_version}; this app "
        f"installs v{INDEX_SCHEMA_VERSION}. Upgrade the app, or "
        f"delete {path} to rebuild with the older schema."
    )
```

`main.py` catches and refuses to start. ~30 minutes including the test
that asserts a v_99 stamp triggers refusal.

#### 2.2 [P2] **D37 component-assignment is non-atomic; reconcile is "a future slice"**

`backend/services/rig_service.py:519-537`:

```python
# Rig.xml on disk references all four components ...
_write_rig_folder(folder, r)
...
# Each component gets back-linked
for field_name, _getter, assigner, _kind in _COMPONENT_REGISTRY:
    component_id = getattr(r, field_name)
    assigner(logbook_root, component_id, r.id)
```

Inline comment (line 530-534):

> A crash partway through leaves the rig referencing components that
> don't yet point back; reconcile (a future slice) detects the
> mismatch and either fixes the components or clears the rig. Per D37:
> "at worst, some components are re-marked-available on the next
> reconcile".

The reconcile pass for rig folders is not in the codebase
(`backend/storage/reconcile.py` is jump-folder-only; the rig_service
module docstring at line 53 says "future `folder_reconcile_rigs`
step"). A crash between iterations leaves:

- rig.xml — forward refs to all four components ✓
- Component #1 — `assigned_rig_id = rig.id` ✓
- Component #2 — still `assigned_rig_id = None` ✗
- Components #3, #4 — still `None` ✗

The retry path is also broken: a re-POST of the same `create_rig`
payload will hit `RigNicknameConflict` at `mkdir(exist_ok=False)`
(line 506-516) because the rig folder already exists. The user is
stuck — manual XML edit is the only recovery.

**Fix shape**: ship `folder_reconcile_rigs` (idempotent: walks
`rigs/*.xml`, ensures each referenced component's `assigned_rig_id`
matches the referring rig, and writes the corrections under
WRITER_LOCK). Call it from a v0.1 boot path (alongside the existing
`folder_reconcile` for jumps), and add a crash-recovery test. Until
then, document the manual recovery in `SECURITY.md` or a dedicated
"recovery" doc.

#### 2.3 [P2] **No crash test for `jumper_migration.py`**

`backend/storage/jumper_migration.py:59-87` documents three crash
points (`atomic_write(folder_xml)` → manifest → legacy unlink) and
asserts the migration is idempotent across each. But there's no test
that simulates a crash between (e.g.) folder XML write and manifest
write, then re-runs migrate_all_jumpers and asserts a clean final
state.

Coverage gap: `backend/tests/` has crash tests for jump folders
(`test_dropzone_crash_recovery.py`, the SIGKILL test) but nothing for
the legacy-jumper migration. This is single-shot upgrade code so a
bug here is one-and-done — but it's one-and-done **for a user's data**.

**Fix:** add `test_jumper_migration_crash_recovery.py` mirroring the
shape of `test_dropzone_crash_recovery.py`. ~2 hours.

#### 2.4 [P3] **WRITER_LOCK is intra-process only; multi-worker contract is documented but not enforced**

`backend/services/_write_lock.py` is one process-local `RLock`. The
file lock (`backend/storage/lockfile.py`) prevents *cross-process*
concurrent writes by refusing to acquire when another instance holds
the lock. So today's posture is correct: one app instance, one
RLock, no contention possible.

The hazard is forward compatibility: a future deployment that uses
multiple uvicorn workers (each is a separate process) gets one RLock
per worker; the lockfile only blocks *boot*, not per-request writes
— it's acquired once at startup, held for app lifetime. So two
worker processes can both pass the lockfile boot check (if the lock
is exclusive-but-acquired-by-one-of-them, second worker fails to
start; if the lock is upgraded to a more cooperative model, both
workers can write concurrently).

D50 already documents the single-process posture. **No code change
today.** Recommend adding a startup assertion that refuses to run
under `--workers > 1` until the lock is converted to a per-request
exclusive acquisition. ~1 hour.

#### 2.5 [P3] **`trash.py` collision counter is per-call, not bounded across rapid burst-deletes**

`backend/storage/trash.py:68-73`:

```python
stamp = _now_utc_basic_iso()
target = trash / f"{stamp}_{folder.name}"
counter = 1
while target.exists():
    target = trash / f"{stamp}_{folder.name}_{counter}"
    counter += 1
```

The counter is local to one call. Under WRITER_LOCK (every soft_delete
caller has `@with_writer_lock`) this is fine — two callers never race.
But:

- If the user manually deletes the same source folder twice within
  the same millisecond (impossible by hand; possible by automation),
  the second deletion enters this loop and increments past existing
  entries.
- The loop is *unbounded*. A `.trash` directory polluted with
  thousands of `<stamp>_foo_N` siblings would take O(N) per delete.

Not exploitable today. Document the bound expectation in
`soft_delete`'s docstring; consider switching to `uuid4().hex[:8]`
suffix if collisions are a future concern. ~15 minutes.

#### 2.6 **Note on cache-invalidation in `validator._load_schema` — risk is documented and unreachable**

The 2026-05-14 audit (§1.3) flagged that the `@lru_cache` on
`_load_schema` is keyed by `Path` not `(Path, mtime)`. I confirm: this
is **already documented** at `backend/xml/validator.py:127` ("invalidation
gap is unreachable for v0.1"). The XSDs are written once at bootstrap
and never modified at runtime. **No action** beyond the inline doc
that's already there.

---

### 3. Algorithmic complexity and hot paths

#### 3.1 [P2] **Every `list_rigs` / `set_star` / `reorder_rigs` walks every rig folder and parses every XML**

`backend/services/rig_service.py:_read_all_rigs` (line 272-296) is
called by:

- `list_rigs` (line 613)
- `_clear_all_stars` (line 378-399)
- `set_star` (transitively via `_clear_all_stars`)
- `create_rig` (line 463 — for the auto-star + display_order
  computation)
- `_elect_successor_star` is not called here but follows the same
  pattern

Each call parses every `rig.xml` from disk. For a single-user logbook
with 1-10 rigs this is invisible. The module's own docstring (line
44-47) acknowledges this with "R.3 will swap this for a
SQLite-indexed lookup once the rigs index table lands."

**OPEN-FROM-PRIOR (deferred).** Re-flag here because this hot path
intersects every other rig operation under the writer lock: a single
create_rig under contention serializes behind a full-XML re-parse of
every existing rig. The R.3 indexing slice is the right fix; until
then, `set_star` could be optimized to compute `auto_star` and
`next_display_order` from a single scan that's already happening,
rather than from two scans.

#### 3.2 [P3] **`list_jumps` ordering uses `idx_jumps_user_date` but tiebreaks on `jump_number` need a runtime sort**

`backend/services/jump_service.py:1000-1008` ORDER BY `date DESC,
jump_number DESC`. The schema index (line 148):

```sql
CREATE INDEX IF NOT EXISTS idx_jumps_user_date ON jumps(user_id, date);
```

This covers `(user_id, date)` but not the `jump_number` tiebreak. With
the index, SQLite can produce the rows in date-DESC order for free,
but ties on `date` require a runtime sort on `jump_number`. For a
typical logbook this is a few-row sort and invisible; for the new
`le=10000` cap (raised per recent commit), worst case is a 10k-row
re-sort.

**Fix:** add `idx_jumps_user_date_jump_number ON jumps(user_id, date
DESC, jump_number DESC)` — though SQLite uses single-column
collation order, so the DESC declaration matters less than the
column list. Bump `INDEX_SCHEMA_VERSION` per D26; the rebuild is free
(D3 reindexable). ~30 minutes including a perf-regression note in
the D-entry.

#### 3.3 [P3] **`stats_route` walks every jump folder via `compute_stats`**

`backend/services/stats_service.py` (165 LOC) — `compute_stats(logbook_root,
user_id)` is the source. The 2026-05-14 audit (second-opinion review)
flagged this as the known perf bug. I confirm the route is still wired
straight into the synchronous walk path; no caching, no Cache-Control,
no ETag. For a 10k-jump logbook on a slow SSD this is multi-second.

**OPEN-FROM-PRIOR.** Two-shot fix: (a) §1.6's Cache-Control, (b) move
stats to a derived projection in the index, recomputed on
create/update/delete. ~1 day.

---

### 4. Code health, structure, and duplication

#### 4.1 [P1] **`backend/services/file_service.py` is still a 6-line scaffold**

```
"""Attachment upload / download service ...
Scaffold only. ...
"""
```

No imports, no functions. 2026-05-14 audit (§3.6) flagged it.
**OPEN-FROM-PRIOR.** The actual file logic lives in `jump_service.py`
(`track_files`, `add_attachments`, `delete_attachment`). The stub is
either misleading future direction or dead code. **Delete or
repopulate.** Five-second decision.

#### 4.2 [P1] **`from pydantic import ValidationError` is still inline at `jump_service.py:275` and `:1068`**

`backend/services/jump_service.py:275` (inside `create_jump`),
`:1068` (inside `update_jump`). The 2026-05-14 audit (§2.5) flagged
these; the line-numbers shifted slightly but they're still
function-local. **OPEN-FROM-PRIOR.** Module-top is the idiomatic spot;
not circular per the prior audit's check. ~5 minutes.

`hashlib` and `mimetypes` at `jump_service.py:613-614` (inside
`track_files`) are the same pattern.

#### 4.3 [P2] **Triple-duplicated jump field declarations**

The 2026-05-14 audit (§3.1) flagged `Jump` / `JumpCreate` /
`JumpUpdate` carrying the same ~16 fields with identical validators.
**OPEN-FROM-PRIOR.** I verified:

```
$ grep -nE "max_length=120|max_length=255" backend/models/jump.py
backend/models/jump.py:64: filename: str = Field(min_length=1, max_length=255)
backend/models/jump.py:101: title: str | None = Field(default=None, max_length=120)
backend/models/jump.py:232: title: str | None = Field(default=None, max_length=120)
backend/models/jump.py:279: title: str | None = Field(default=None, max_length=120)
```

Three `title` declarations with the same `max_length=120`. The
fragility is real: a future change to one without the other two ships
silently.

#### 4.4 [P2] **`backend/xml/serialize.py` is 1396 LOC — still the single largest file**

**OPEN-FROM-PRIOR (§3.2).** The proposed split (per-entity submodules
under `backend/xml/serialize/`) is mechanical. ~1 day.

#### 4.5 [P3] **No structural test for round-trip identity across all entity types**

`element_to_<E>(<E>_to_element(e)) == e` is the implicit invariant for
every entity (Jump, Dropzone, Rig, Main, Reserve, AAD, Container,
Jumper, Person, RigSnapshot). Tests cover individual fields and
specific shapes (the `test_xml_roundtrip.py` suite is 528 LOC) but
there is no property-style "for any valid entity, round-trip is
identity" check.

**Fix shape:** Hypothesis-based or factory-based fuzz test per entity.
~1 day. Catches the next "I added a field to the model and forgot to
serialize it" bug at PR time.

#### 4.6 [P3] **Service-to-service coupling: `rig_service` imports `main`, `reserve`, `aad`, `container` services**

`backend/services/rig_service.py:83`:

```python
from . import aad_service, container_service, main_service, reserve_service
```

The four sibling services are pulled in via `_COMPONENT_REGISTRY` (line
115-142) to do D37 cross-entity validation + assignment. Functional
and correct, but it makes `rig_service` a hub for the entire
component graph. Any change to a component service's API ripples
into the registry tuple's signature.

This is the right architecture for v0.1's small surface, but should
be flagged for the moment the component count grows beyond four (a
fifth jumper-attached entity type, or per-jumper component visibility).
The fix would be a plugin registry that each component service
registers itself into at import time, inverting the dependency
direction.

#### 4.7 [NIT] **Logging may surface PII in folder names**

`backend/services/jump_service.py:433-451, 720-728, 858-866, 1192-1203,
1244-1253` all do `extra={"folder": rel_folder}` where `rel_folder`
contains the jump title (potentially a jumper's name in a tandem log
entry). `backend/services/rig_service.py:294, 540-547, 658-669` log
`rig_folder=str(folder)` which contains the rig nickname.

For a single-user local app this is fine. For any deployment that
ships logs to a third party (a future telemetry slice), it's a leak
vector. **Defer**: not a v0.1 concern; D-entry candidate before any
remote-logging slice.

---

### 5. Tests

#### 5.1 [P2] **No test for rig partial-create recovery (Item §2.2)**

Already covered above. Confirmed gap in `backend/tests/test_rest_rigs.py`
(840 LOC, focuses on REST surface) and `test_concurrent_writes.py`.

#### 5.2 [P2] **No test asserting `open_index` refuses a newer-on-disk schema (Item §2.1)**

Once §2.1 is fixed, the test is one fixture. Belongs in
`test_bootstrap.py` or a new `test_index_schema_versioning.py`.

#### 5.3 [P3] **`_jump_number_is_taken` prefix scan and `mkdir(exist_ok=False)` aren't tested together for race**

`backend/services/jump_service.py:131-155` is the D23 service-level
prefix scan; line 332 is the kernel-level `mkdir(exist_ok=False)`
backstop. The two are documented as belt-and-braces. But there's no
test that creates two threads racing through `create_jump` with the
same `jump_number` — the writer-lock makes this structurally
impossible, but a test that proves it would also document the
contract.

`test_concurrent_writes.py` (216 LOC) covers the lock semantics but
not this specific race shape.

#### 5.4 [NIT] **`backend/tests/conftest.py` sample_jump fixture uses fake SHA256**

2026-05-14 §4.5. **OPEN-FROM-PRIOR.** Cosmetic.

---

### 6. Frontend

#### 6.1 [P1] **`useEffect` deps in `LogJumpModal` likely miss `initialJump`**

`frontend/src/modals/LogJumpModal.jsx:408, 440, 471` — multiple
effects depend on `[visible, isEdit]` (or similar) without including
`initialJump`. In edit mode, if a parent re-renders with a *new*
`initialJump` while `visible` stays `true` and `isEdit` stays `true`,
the form stays bound to the *previous* jump.

This is a structural class of bug that's hard to spot in a 2353-LOC
file. The fix in isolation is to add `initialJump` to the dep array;
the fix in the whole is the decomposition the 2026-05-14 audit (§2.2)
already proposed.

#### 6.2 [P1] **Silent `.catch(() => {})` in fetch error paths**

`frontend/src/modals/JumpDetailModal.jsx:55` and similar in `Jumps.jsx`
silently swallow API errors. The user sees an empty list or a blank
panel with no indication that the server returned an error. Worse,
`ApiError` carries the RFC 9457 `code` — so we have machine-readable
problem identifiers that we're throwing away.

**Fix shape:** every `.catch(() => ...)` becomes a `.catch(err =>
setLoadError(err))` and the component shows a graceful empty state +
"Failed to load (code: <X>). Retry." link.

#### 6.3 [P2] **`api.js` has 74 exports in 930 LOC; no TypeScript or codegen from `/openapi.json`**

**OPEN-FROM-PRIOR (§3.3 / §3.4).** Once §1.1 (route `responses=`)
lands, `openapi-typescript` could generate a `frontend/src/api.types.ts`
in CI. Migrating `api.js → api.ts` becomes incremental.

#### 6.4 [P2] **Modal decomposition still pending**

**OPEN-FROM-PRIOR.** Top-5 unchanged: `LogJumpModal.jsx` 2353,
`ComponentDetailModal.jsx` 1472, `MyRig.jsx` 1259, `JumpDetailModal.jsx`
907, `AddComponentModal.jsx` 717. The split boundaries are conceptual
already in the design.

#### 6.5 [P3] **No code splitting / lazy modal imports**

`frontend/vite.config.js` has no `rollupOptions.output.manualChunks`
and no `React.lazy()` on the modals. Every load of the SPA pulls down
the whole bundle including modals the user may never open in this
session. For a desktop app this is invisible (everything is local);
for a future LAN-exposed deployment it matters.

#### 6.6 [P3] **a11y debt: `InlineField` doesn't wire `htmlFor` / `id`; Lucide icons missing `aria-hidden`**

The 2026-04-28 WCAG audit (per `docs/historical-reviews.md`) called
this out. The earlier audits suggest detail work landed; spot-check on
`LogJumpModal.jsx` confirms `InlineField` is still hand-rolled and
doesn't pass an `id` through to its `<input>`/`<label>` pair.
Lucide imports throughout still rely on the default icon role.

**Fix:** wire `htmlFor={id}` in `InlineField`; add `aria-hidden="true"`
to every decorative icon. ~2 hours, mechanical.

---

### 7. Strengths (re-confirmed) — protect these

These are still load-bearing wins. Don't let a refactor erode them:

- **`backend/storage/filesystem.py`** — `F_FULLFSYNC` on Darwin, parent-
  directory fsync, NFC normalization, 255-byte cap, `safe_join` —
  exemplary platform-layer hygiene.
- **`backend/xml/validator.py`** — DOCTYPE pre-scan + lxml-hardened
  parser + 10 MiB cap. Tests pin XXE, billion-laughs, external DTD,
  DOCTYPE-in-CDATA.
- **`backend/storage/manifest.py`** — the `generate()` vs
  `from_jump_xml()` distinction is the kind of design care that
  prevents corruption-blessing bugs.
- **`backend/services/_write_lock.py`** — RLock + the corrected
  docstring (which now matches the code) — exactly right for the
  cross-service re-entrancy in `rig_service.create_rig`.
- **`backend/api/errors.py`** — RFC 9457 + RFC 6901 done right; ordered
  escapes, `about:blank` rationale, extension-collision guard.
- **`backend/api/openapi.py`** — even unused, the `#/components/responses/...`
  shape is the right scaffolding; finding §1.1 is "use what's already
  there" not "rewrite from scratch."
- **CI matrix** — 3 OS × 3 Python = 9 backend cells, ruff + pyright +
  pytest, locked deps via `uv sync`. Frontend Vitest smoke on Node 20.
  This is what good CI looks like.
- **Test posture** — 1738 backend tests, integration-shaped, `tmp_path`
  over mocks. 1 file uses `Mock`/`MagicMock`. CLAUDE.md §7 is honored.

---

### 8. Items that the 2026-05-14 audit flagged but are now CLOSED-SINCE-PRIOR

Verified clean on this audit's pass:

| Prior finding | Status |
|---|---|
| 55 `.bak*` files in `frontend/src/` | **0 found** |
| 79 un-gitignored `vitest.config.js.timestamp-*.mjs` | **0 found** |
| 43 `vite.config.js.timestamp-*.mjs` | **0 found** |
| 5 `.venv*` directories | **only `.venv` remains** |
| 2 `.DS_Store` files | **0 found** |
| `_write_lock.py` docstring contradicts code | **fixed: lines 1-26 now match D50** |
| `rest.py` uses `__import__("sys")` | **fixed: `import sys` at line 10** |
| Six `it.skip` test stubs in `frontend/test/` | **gone** |

Clean cleanup pass. The 2026-05-14 audit's "P1 afternoon's work" landed.

---

## Suggested execution sequence

Order is **smallest verified-payoff first**. Every step lands behind a
green CI matrix and is reversible.

1. **(P1, 30 min)** §4.1 + §4.2 — delete the 6-line `file_service.py`
   (or repopulate); move the three inline imports in `jump_service.py`
   to module top. Pure code hygiene, no behavior change.
2. **(P1, 30 min)** §1.7 — switch `f.__dict__` → `dataclasses.asdict(f)`
   in `backend/api/jumps.py:287`. Five-character fix; idiomatic.
3. **(P2, 30 min)** §1.2 — add the OpenAPI ↔ Pydantic
   `ProblemDetails` alignment test. Cheap regression net for a
   contract bug class.
4. **(P2, 1 hr)** §5.1 + §5.2 — add the two missing tests (rig
   partial-create recovery, index version refusal). They expose the
   §2.1/§2.2 gaps and turn them from "documented" into "caught at PR
   time."
5. **(P1, 1 hr)** §2.1 — change `open_index` to refuse a newer-on-disk
   schema. Now §5.2 passes.
6. **(P1, 2 hr)** §1.1 + §1.3 — wire `responses=` and `operation_id`
   on every route. Single pass, all entity routers. Now every
   downstream SDK generator works correctly.
7. **(P2, 2 hr)** §2.2 — implement `folder_reconcile_rigs` and call
   it from boot. Now §5.1 passes; D37 cross-entity invariant is
   enforced end-to-end.
8. **(P2, 4 hr)** §1.6 — server-side `Cache-Control` middleware +
   `ETag` on the high-value endpoints (jumps list/detail, stats).
   Remove the defensive `cache: 'no-store'` from `frontend/src/api.js`.
9. **(P1, 4 hr)** §6.1 + §6.2 — frontend: add `initialJump` to the
   relevant useEffect deps in `LogJumpModal`; replace silent
   `.catch(() => {})` patterns with surfaced error states.
10. **(P2, 1 day)** §4.3 — extract `_JumpFields` mixin; same shape
    for the four component-model families.
11. **(P2, 1 day)** §3.2 + §3.3 — add the SQLite covering index for
    `(user_id, date, jump_number)`, bump `INDEX_SCHEMA_VERSION`; cache
    `compute_stats` behind ETag.
12. **(P2, 1-2 days)** §1.4 + §1.5 — body-size middleware +
    Idempotency-Key on `POST /jumps`. Both gate D48 LAN exposure.
13. **(P3, sliced)** §4.4 — split `backend/xml/serialize.py`. Land
    one entity submodule per PR; the rest stay as re-exports until
    the last one moves.
14. **(P3, sliced)** §6.3 — TypeScript adoption + OpenAPI codegen.
    Now §1.1's `responses=` work pays off in compile-time client
    safety.

Steps 1-7 are an afternoon. Steps 1-9 are a calendar week of focused
work. The rest gates v0.2 cleanly.

---

## Methodology and limits

- Re-read CLAUDE.md, `docs/architecture.md`, the 2026-05-14 audits in
  `reviews/`, `DECISIONS.md` (D-entry headers only — 347 KB body
  skimmed by D-number).
- Read in full: `backend/services/jump_service.py`,
  `services/rig_service.py` (300+ lines), `services/_write_lock.py`,
  `storage/filesystem.py`, `storage/index.py`, `storage/trash.py`,
  `storage/reconcile.py`, `api/errors.py`, `api/rest.py`, `api/openapi.py`,
  `api/deps.py`, `api/jumps.py` (first 320 lines), `api/ops.py` (verify
  + reindex routes), `xml/validator.py` (sampled). Sampled by grep:
  every other service, every other API router, every test file
  header.
- Dispatched four parallel investigation agents covering services,
  XML, frontend, and API+storage. Verified or corrected each
  finding against current code on disk before including in this
  report. The async/sync sub-claim in §1.8 is one of two agent
  claims I corrected (the other: a claim about missing `max_length`
  constraints, contradicted by `grep` on the models).
- Ran the full triple: `ruff check backend`, `pyright backend`,
  `pytest backend/tests` (1737 passed). Ran `npm test` in
  `frontend/` (65 passed across 4 files).
- Did NOT: profile `compute_stats` against a large logbook; run
  `verify` against a real corrupted folder; benchmark `open_index`
  per-call overhead (the 2026-04-30 76μs profile stands per the prior
  audit); inspect every `models/*.py` for the shared-field-mixin
  refactor surface beyond `models/jump.py`; deep-read every test file
  (sampled by file size and naming convention).
- All citations verified against the working tree at commit `48877f9`
  on branch `claude/code-debt-analysis-VgIBB`.

---

*— end —*
