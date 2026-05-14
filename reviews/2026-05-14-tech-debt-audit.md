# Tech-Debt & Efficiency Audit — 2026-05-14

**Scope:** Full repo (backend, frontend, tests, scripts, CI, repo hygiene).
**Lenses:** correctness/invariants, performance/efficiency, code health, test coverage.
**Posture:** report-only. No code changes. Every claim is grounded in a file:line citation against code-on-disk per CLAUDE.md §2.

This builds on (and does not relitigate) the closed items in
`reviews/2026-04-30-finish-open-items.md`. Where a finding overlaps a
prior audit, that prior audit's ID is cited; the new entries are
distinct from anything in the 2026-04-29 dashboard.

---

## TL;DR

The codebase is in **strong** shape on the invariants that matter
most: D2 (XML+XSD), D3 (SQLite-not-authoritative), D4 (sanitization),
D9 (lockfile), D10 (atomic writes), D16 (RFC 9457), D27 (correlation).
The hardened-parser security posture is exemplary; tests pin XXE,
billion-laughs, external DTD, and even DOCTYPE-in-CDATA. Storage
primitives correctly use `F_FULLFSYNC` on Darwin and parent-directory
fsync after rename.

The debt is concentrated in three places:

1. **Repo hygiene / sandbox cruft** — 55 zero-byte `.bak*` files, 79
   un-gitignored vitest timestamp files, 43 stale vite timestamps,
   5 `.venv*` directories, 2 `.DS_Store` files, 6 inert "superseded"
   frontend test stubs. None of this is in source control (most is
   gitignored), but all of it litters the working tree and creeps
   into IDE searches, ripgrep, and any new-developer's mental map.
   Cheapest, highest-readability win in the repo.

2. **Documented policy ↔ code drift on the writer lock** — the
   module docstring in `backend/services/_write_lock.py` says
   "Reads do not acquire the lock" but four read functions in
   `jump_service` and `rig_service` are `@with_writer_lock`
   decorated. Per CLAUDE.md §2 the code is authoritative; the
   docstring is stale. Either remove the decorator from reads or
   amend the docstring to match the actual policy ("reads acquire
   the lock because reconcile-on-read may write").

3. **Frontend health** — `LogJumpModal.jsx` is 2,353 LOC with 45
   React hooks in a single component; `api.js` is one 885-LOC file
   with 74 exports; there is no TypeScript layer. The CI safety net
   for the frontend is one Vitest smoke job; everything else
   depends on hand-tested, single-developer judgement.

Plus a small number of correctness nits (one un-gitignored XSD risk
vector, one schema drift risk between hand-authored OpenAPI and
Pydantic) and a thicker pile of structural code-health items (model
field duplication, oversized service modules, inline imports).

**Re-deferred items** from the prior audit are noted as such where
relevant — `CODE-1` (open_index per call) was profiled at 76μs and
re-deferred 2026-04-30; this audit does not re-litigate it but does
flag two specific call paths where the same connection could be
threaded through helpers to halve the per-request overhead.

---

## How findings are prioritised

| Priority | Meaning | Examples |
|---|---|---|
| **P1** | Trivial-to-fix, high readability/discipline payoff. Repo hygiene; stale docs that contradict code. | `.bak*` cleanup, vitest gitignore patch, `_write_lock` docstring fix |
| **P2** | Real structural debt with measurable cost (perf, maintainability, test fragility). Worth scheduling. | LogJumpModal decomposition, model field duplication, OpenAPI-schema drift test |
| **P3** | Long-term improvement. Not blocking v0.1 but informs the post-v0.1 roadmap. | TypeScript adoption, serialize.py split, services connection sharing |
| **NIT** | Style consistency. Won't change behaviour. | `__import__("sys")` in `rest.py`, `dataclasses.asdict()` vs `__dict__` |

Findings inside each category are listed in rough effort/impact order.

---

## Findings by lens

### 1. Correctness & invariants

The four CLAUDE.md "code-on-disk vs docs" diffs found in this pass.

#### 1.1 [P1] `_write_lock.py` docstring contradicts current code

`backend/services/_write_lock.py:1-7`:

> Every public service **write** function acquires `WRITER_LOCK` for its full duration via the `with_writer_lock` decorator. **Reads do not acquire the lock** — concurrent reads against a coherent index are safe (D2 hardened parser, D25 reconcile-on-read).

But:

- `backend/services/jump_service.py:423` — `get_jump` is `@with_writer_lock`.
- `backend/services/jump_service.py:500` — `list_jump_files` is `@with_writer_lock`.
- `backend/services/rig_service.py` — `get_rig` and `list_rigs` are `@with_writer_lock`.

The intent is defensible: `get_jump` calls `folder_reconcile` which CAN write `SHA256SUMS` on a stale-manifest heal. So a "read" function holds a writer-relevant lock because it might transitively become a writer. But that is the opposite of what the docstring says.

**Per CLAUDE.md §2 the code is authoritative.** Fix the docstring (or amend D50) so the policy reads: *every public service function that may transitively write — including reads that trigger reconcile-on-open — acquires the writer lock; pure reads do not, and there are none today.*

Tangential side-effect: this means concurrent `get_jump` calls serialise behind each other in a multi-threaded uvicorn config. For a single-user desktop app on loopback that's invisible; if/when LAN exposure ships (D48 successor), it becomes a contention point. A "read lock that promotes to write" via `threading.RLock`'s underlying primitives (or a `threading.Lock` guarded only around the reconcile branch) would let read traffic scale. **Not urgent — record this as a known shape for the multi-user successor.**

#### 1.2 [P2] OpenAPI `ProblemDetails` schema is hand-authored; can drift from Pydantic model

`backend/api/openapi.py:42-104` defines `PROBLEM_DETAILS_SCHEMA` as a hand-written `dict`.
`backend/api/errors.py:107-129` defines the same shape as a Pydantic `ProblemDetails` class.

Adding a new extension field (e.g. `trace_id`, `retry_after`) requires editing both files. Today there is no test that asserts they agree, so a one-sided change ships silently and breaks SDK consumers regenerating from `/openapi.json` after the next prod deploy.

**Fix:** add a `backend/tests/test_openapi_problem_details_alignment.py` that asserts the JSON Schema generated by `ProblemDetails.model_json_schema()` is a subset of (or matches the documented members of) `PROBLEM_DETAILS_SCHEMA`. Costs five minutes, defends a contract.

#### 1.3 [P3] `bootstrap.py` validates XSDs at install but `_load_schema` cache can outlive a bad file

`backend/storage/bootstrap.py:158-174` calls `validate_schema_file` on each freshly-written XSD — excellent. But `backend/xml/validator.py:127` caches compiled `XMLSchema` objects via `@lru_cache(maxsize=8)` keyed by `Path`. If the logbook root's `SCHEMA.v1.xsd` is rewritten between two requests (re-bootstrap, manual XSD edit), the cache returns the OLD compiled schema for the same path object.

In practice this is a non-issue for v0.1 (the app boots once, bootstrap runs once, XSD never changes mid-run). But the comment at `:127` doesn't acknowledge the invalidation gap. **Two-line fix:** document the lifetime assumption in the cache, or key on `(path, mtime)`.

#### 1.4 [NIT] `launch_desktop.py:249` writes config without `atomic_write`

`cfg.write_text(...)` is used for the desktop launcher config. The bytes are small and the file is recreatable from the picker, so it doesn't violate D10's "every write to persisted data goes through atomic_write" (settings are recreatable, not data). But for consistency, threading it through `atomic_write` (which would just write `<cfg>.tmp` → `os.replace`) costs nothing and keeps the discipline uniform.

---

### 2. Performance & efficiency

Most performance concerns are well-handled. The two structural ones below are about repeated SQLite connection setup and a single oversized React component.

#### 2.1 [P2] Service functions open multiple SQLite connections per request

`open_index()` is called **35 times across services** (`grep -rn 'open_index' backend/services/ | wc -l = 35`), each creating a fresh `sqlite3.Connection`, running 3 PRAGMA statements (`journal_mode`, `synchronous`, `busy_timeout`), and one `PRAGMA user_version` query.

Multi-connection-per-call paths I observed:

- `backend/services/jump_service.py:611-633` — `track_files` opens once for `get_jump` (which internally opens), once for the folder lookup, once for the `UPDATE`. **3 opens per request.**
- `backend/services/jump_service.py:783-799,886-894` — `add_attachments` follows the same pattern. **3 opens per request.**
- `backend/services/dropzone_service.py:_upsert_index_row`, `_delete_index_row`, `_count_dropzones`, `_read_starred_ids` — each helper opens+closes. `set_star` calls them in sequence: `_count` + `_read_starred_ids` + `_clear_all_stars`(N opens) + `_upsert_index_row`. **4+ opens per request, scaling with the number of stale stars.**

**Prior audit context.** `CODE-1` was profiled 2026-04-30:
> 1000 iterations, single-process, warm cache: `open_index(root) + SELECT 1 + close` ≈ 76μs/call. 100 service requests at 3 opens/request ≈ 22ms total. For a single-user desktop app at any plausible interactive rate, invisible.

That profile justifies the per-call pattern globally. **This audit does not propose reversing that decision.** It does narrowly suggest that the specific helpers in `dropzone_service.py` that already accept a `conn: sqlite3.Connection` (cf. `_reindex_dropzones` in `reindex_service.py:352`) could take the same shape — pass `conn` in to `_upsert_index_row` from the public functions, so a `set_star` transition acquires one connection per call instead of `1 + N + 1`. This is a refactor of ~80 LOC, no behaviour change, no decision to revisit.

#### 2.2 [P2] `LogJumpModal.jsx` is 2,353 LOC / 45 hooks in a single component

`frontend/src/modals/LogJumpModal.jsx` — 2,353 LOC, 45 occurrences of `useState`/`useEffect`/`useMemo`/`useRef`/`useCallback`. Every state change in this component re-evaluates 45 hooks and re-traverses the entire JSX tree. For a single-user desktop app the runtime cost is small (sub-frame at any plausible frequency), but the **maintenance** cost is real:

- Onboarding cost for a new contributor is steep — the form's data flow is spread across the file.
- A regression in any one field's binding requires reasoning about the whole component.
- Diffing one PR against another in this file is high-cognitive-load.

This is a candidate for extraction into per-section sub-components (LogJump-Header, LogJump-RigPicker, LogJump-DropzonePicker, LogJump-Notes, LogJump-Attachments, LogJump-Group). The boundaries already exist conceptually in the design; they're not factored.

**Same shape, smaller scale**: `ComponentDetailModal.jsx` 1,472 LOC, `MyRig.jsx` 1,169 LOC, `Profile.jsx` 949 LOC, `JumpDetailModal.jsx` 907 LOC.

#### 2.3 [P3] Frontend `api.js` always sends `cache: 'no-store'`

`frontend/src/api.js:46-51`:

```js
// `cache: 'no-store'` keeps WKWebView (macOS pywebview backend) and other
// WebView caches from serving a stale GET response after we POST a new
// resource. The backend doesn't emit Cache-Control headers, so without
// this hint a same-URL GET right after a successful POST can come back
// from disk cache with the pre-POST list.
const noStoreInit = { cache: 'no-store' };
```

The right server-side fix is `Cache-Control: no-store` on every GET, set by middleware. Once the server is explicit about caching, the client doesn't need to be defensive on every fetch and the discipline ("did I remember the no-store flag?") goes away. It's a ~5-line middleware that runs before `CorrelationIdMiddleware` and adds the header to every `http.response.start`.

#### 2.4 [P3] `folder_reconcile` runs on every `get_jump`, regardless of whether the manifest is stale

`backend/services/jump_service.py:464` — `get_jump` unconditionally calls `folder_reconcile(folder, logbook_root=logbook_root)`. The reconciler is documented as idempotent and cheap (no attachment rehash), but it does at minimum a `stat()` on `SHA256SUMS` and one `stat()` on `jump.xml`.

For interactive read traffic over a long-lived process this is fine. For a hypothetical bulk-list endpoint that resolved each list row into a full `get_jump` (not what `list_jumps` does today — it serves from the index — but a plausible future endpoint), the per-row reconcile dominates.

**Not urgent.** Note for the future: a sentinel file (`.reconciled`) or an mtime comparison would short-circuit healthy folders. Today's call pattern doesn't need it.

#### 2.5 [NIT] Four inline `import` statements in `jump_service.py`

- `backend/services/jump_service.py:230` — `from pydantic import ValidationError`
- `backend/services/jump_service.py:606` — `import hashlib`
- `backend/services/jump_service.py:607` — `import mimetypes`
- `backend/services/jump_service.py:1131` — `from pydantic import ValidationError`

All four are non-circular imports. Move to module top. Same applies to `bootstrap.py:195` (`from .jumper_migration import migrate_all_jumpers` — this one is genuinely there to avoid a circular import, so it's load-bearing; leave it and add a one-line comment saying so).

---

### 3. Code health & readability

#### 3.1 [P2] Triple-duplicated jump field declarations: `Jump` / `JumpCreate` / `JumpUpdate`

`backend/models/jump.py` declares the same ~16 fields three times across `Jump`, `JumpCreate`, `JumpUpdate`. Each model has its own `model_config = ConfigDict(extra="forbid")`. Adding a new field (per D53 / D57 / D60 patterns) requires touching all three. Field-validator drift between the three classes is a real risk — for instance, `Jump.title` has `max_length=120`, `JumpCreate.title` has `max_length=120`, `JumpUpdate.title` has `max_length=120`; a future PR that updates one and not the others would pass tests on the changed surface and fail in production.

**Fix shape (Pydantic v2):** declare a `_JumpFields` mixin with the shared fields; `Jump`/`JumpCreate`/`JumpUpdate` inherit from it and add only the fields that differ (`id` is server-assigned on Jump only; `attachments` is on Jump only; `created_at`/`updated_at` are on Jump only). Same surface-area collapse applies to `Dropzone`/`DropzoneCreate`/`DropzoneUpdate` and the four component model families.

Cost: medium. Risk: low (mostly mechanical). Payoff: every future field add touches one place, not three.

#### 3.2 [P2] `backend/xml/serialize.py` is 1,394 LOC — biggest single file in the repo

The file is a single module with ~60 functions covering jump, dropzone, person, container, AAD, reserve, main+lineset, rig+repack, jumper+credentials+attachments, rig-snapshot. Each entity's serialize/parse functions are grouped with section dividers, but the file as a whole exceeds any plausible cognitive working-set.

**Fix shape:** split into `backend/xml/serialize/__init__.py` re-exporting + per-entity modules (`jump.py`, `dropzone.py`, `rig.py`, `jumper.py`, `component.py` for the four rig-component shapes, `person.py`). Shared helpers `_qn`, `_sub`, `_find`, `_text`, `_emit_component_base`, `_parse_component_base` go in `_helpers.py`. Each new module is 100-300 LOC and matches a single mental model.

Same shape applies to `backend/services/rig_service.py` (1,362 LOC) and `backend/services/jump_service.py` (1,349 LOC). For services the obvious cleavage is per-operation-family: `jump_crud_service.py` (create/get/list/update/delete) + `jump_attachment_service.py` (track/add/delete attachments) + `jump_files_service.py` (list_jump_files).

#### 3.3 [P2] Frontend `api.js` is 885 LOC with 74 exports — no TypeScript, no codegen from OpenAPI

`grep -c '^export ' frontend/src/api.js` returns 74. Splitting by domain (`api/jumps.js`, `api/dropzones.js`, `api/rigs.js`, `api/jumpers.js`, `api/people.js`, `api/ops.js`, `api/index.js` re-export) makes new endpoint additions touch one file and brings IDE jump-to-definition behaviour in line with the backend's module shape.

**Stronger move (P3):** generate `api/*.ts` from `/openapi.json` at build time. The backend OpenAPI is well-shaped (RFC 9457 errors, hand-augmented schemas), so an `openapi-typescript` or `orval` step produces typed clients for free. The cost is one new dev dependency and one CI step; the payoff is that every backend field change surfaces as a TypeScript error at build time rather than as a runtime undefined-access in the WebView.

#### 3.4 [P3] No TypeScript in the frontend

`find frontend/src -name '*.ts' -o -name '*.tsx'` returns 0 files. Per `ARCHITECTURE.md:108`:
> The frontend is plain JavaScript today — no TypeScript layer, no codegen from OpenAPI yet. Forms are hand-rolled inside the modal components rather than using a library.

The `views.smoke.test.jsx` suite was added 2026-04-30 explicitly because:
> The static-typing safety net stops at the JS file boundary. JS's late binding lets a renamed default export, a dropped Lucide icon, or an undefined hook ride into production without a single warning until the user hits the affected view.

That's a direct cost the codebase already pays. Adopting TypeScript (incrementally — `.js` and `.ts` coexist via `allowJs`) would eliminate the class of bug the smoke test is patching around. **Cost is real (15.5K LOC of JS to gradually type)**, but every new component added now is a missed opportunity.

#### 3.5 [P3] `Jump.created_at` and `Jump.updated_at` typed as `str | None`, not `datetime`

`backend/models/jump.py:192-193`:

```python
created_at: str | None = None
updated_at: str | None = None
```

The strings are guaranteed-format ISO 8601 UTC ms with `'Z'` suffix (D17), and the canonical formatter is `_now_utc_iso` in `jump_service.py:77`. But the model accepts any string, so a manually-crafted XML with `created_at = "yesterday"` parses fine and produces a SQLite row with a malformed timestamp that breaks downstream `ORDER BY date` arithmetic.

**Fix:** `Annotated[str, Field(pattern=ISO_UTC_MS_PATTERN)]` — there's already an `IANA_TZ_PATTERN` and `SHA256_HEX_PATTERN` in `models/common.py:18-21`, so the convention is in place. Same shape applies to every `created_at`/`updated_at` in the other models.

#### 3.6 [P3] `backend/services/file_service.py` is a 6-line scaffold stub

```
"""Attachment upload / download service (D14 — FlySight CSVs, videos, photos).

Scaffold only. Files are stored as-is under the jump folder; metadata is
recorded in `<attachments>` in jump.xml and hashes flow into SHA256SUMS.
FlySight CSV *parsing* is explicitly out of scope for v0.1 (D14).
"""
```

No imports, no functions, no exports. It's a comment masquerading as a module. The actual file logic lives in `jump_service.py` (`track_files`, `add_attachments`, `delete_attachment`). **Delete or repopulate.** Leaving it confuses every new contributor running `tree backend/services` for the first time.

#### 3.7 [NIT] `rest.py:131,135` uses `__import__("sys").stderr` instead of `import sys`

`backend/api/rest.py:131,135` — the unhandled-exception handler does `__import__("sys").stderr` twice. `import sys` is already implicitly available (it's a stdlib name) and would be one line at module top. The current form is a tic from copy-paste that the linter doesn't catch.

#### 3.8 [NIT] `jumps.py:280` uses `FolderFileResponse(**f.__dict__)`

`backend/api/jumps.py:280` — `[FolderFileResponse(**f.__dict__) for f in files]`. `f` is a frozen dataclass. The idiomatic conversion is `dataclasses.asdict(f)`, which recursively handles nested dataclasses (not relevant here, but a guardrail) and is the one-true-canonical pattern. `__dict__` happens to work today.

---

### 4. Test coverage & quality

#### 4.1 [P1] Six inert `it.skip` test stubs in `frontend/test/`

```
frontend/test/sanity.test.js              (8 LOC, canary)
frontend/test/api-import.test.js          (3 LOC, "superseded — see views.smoke")
frontend/test/careerstats-import.test.js  (3 LOC, "superseded")
frontend/test/import-only.test.js         (3 LOC, "superseded")
frontend/test/lucide.test.js              (3 LOC, "superseded")
frontend/test/profile.test.jsx            (4 LOC, "Empty stub kept because the sandbox can't delete files.")
```

Per `HANDOFF.md`:
> 6 diagnostic test files in `frontend/test/` could not be deleted from the sandbox; overwritten with `it.skip` stubs that compile and produce 5 skipped tests. Inert; safe to delete locally.

**Action:** `rm` the five "superseded" stubs and `profile.test.jsx` locally. Keep `sanity.test.js` if you like the canary; otherwise drop it too — `views.smoke.test.jsx` is a stronger canary already.

#### 4.2 [P2] Frontend test coverage is light against 15.5K LOC of view logic

Real (non-stub) frontend tests:

```
frontend/test/views.smoke.test.jsx              (97 LOC,  7 tests — import-drift only)
frontend/test/identityEditOrchestrator.test.js  (305 LOC, real unit tests)
frontend/test/identityEditFull.test.jsx         (~770 LOC, integration)
frontend/test/d60-starred-dropzone.test.jsx     (~210 LOC, focused regression)
```

`identityEdit*` is well-tested. Everything else relies on hand-testing in the dev pywebview window. **Per-modal smoke tests** (an equivalent of `views.smoke.test.jsx` for the modal subtree the smoke currently mocks away with `vi.mock(...)`) would catch the same import-drift class for modals. Not blocking v0.1, but a sensible next slice.

#### 4.3 [P3] No frontend integration test for the create-jump multipart flow

The backend has rich coverage (`test_create_jump.py` 906 LOC, `test_crash_recovery.py` 780 LOC). The frontend has no test that mounts `LogJumpModal`, fills in fields, attaches a file, and asserts the request body matches the multipart shape from D30. A regression in the FormData composition would only surface at first user submit.

**Fix shape:** `frontend/test/log-jump-modal-submit.test.jsx` — `render(<LogJumpModal />)`, fill, `fireEvent.click(submit)`, assert on the recorded `fetch` calls' body. Vitest + Testing Library already in deps.

#### 4.4 [P3] One test file uses `Mock`/`MagicMock` patterns; the rest are integration-shaped

`grep -rl '@patch\|MagicMock\|Mock()' backend/tests/ | wc -l` → 1 file uses mocks. The other 70 use `tmp_path` against real filesystem. This matches CLAUDE.md §7's policy ("Mocking filesystem behaviour hides cross-platform bugs"). **Noting this as a strength**, not a finding — keep this discipline.

#### 4.5 [NIT] `conftest.py:sample_jump` fixture creates a `Jump` with empty `attachments` for the doctored example then a single attachment

Cosmetic. The fixture builds an `Attachment(filename="flysight.csv", sha256="a"*64, size=12345, content_type="text/csv")`. The hash is fake. For most tests this is fine because they only exercise the model layer. For tests that assert the manifest matches the file, the hash mismatch is a tripwire. **Note** — leave as-is, document the fixture's coverage in its docstring so a test author doesn't reach for it for a manifest-integration test.

---

### 5. Repo hygiene

Pure noise category. Each is one cleanup step.

#### 5.1 [P1] 55 zero-byte `.bak*` files in `frontend/src/`

```
frontend/src/views/MyRig.jsx                     (42,484 bytes, real)
frontend/src/views/MyRig.jsx.bak                 (0 bytes)
frontend/src/views/MyRig.jsx.bak2                (0 bytes)
frontend/src/views/MyRig.jsx.bak3                (0 bytes)
frontend/src/views/MyRig.jsx.bak5                (0 bytes)
frontend/src/views/MyRig.jsx.bak6                (0 bytes)
```

Same pattern for `LogJumpModal.jsx.bak{,2,3,4}`, `Dropzones.jsx.bak{,2,5,6}`, eleven others. Created May 14 (today). Likely automated-tool residue; the user has been making rapid edits and a "save backup" hook left empty stubs.

**Fix:** add `*.bak*` to `.gitignore`, `find frontend/src -name '*.bak*' -delete`. Both one-liners.

#### 5.2 [P1] 79 un-gitignored `vitest.config.js.timestamp-*.mjs` files

`.gitignore` has:
```
frontend/vite.config.*.timestamp-*.mjs
```
But not the vitest equivalent. `ls frontend/vitest.config.js.timestamp-*.mjs | wc -l` → 79.

**Fix:** add `frontend/vitest.config.*.timestamp-*.mjs` to `.gitignore` and delete the existing files. Vitest, like Vite, writes these on HMR config reloads.

#### 5.3 [P1] 43 stale `vite.config.js.timestamp-*.mjs` files

Already gitignored (so they can't be tracked), but `ls frontend/vite.config.js.timestamp-*.mjs | wc -l` returns 43 files on disk. Each shows up in IDE searches and `find`-based scripts.

**Fix:** `rm frontend/vite.config.js.timestamp-*.mjs`. Will reappear during dev sessions; that's fine — they're meant to be ephemeral.

#### 5.4 [P1] Five `.venv*` directories at project root

```
.venv/
.venv-fresh/
.venv-linux/
.venv-sandbox/
.venv-test/
```

`INFRA-8` (from the 2026-04-29 audit) is open and was halted in the prior session for sandbox-permission reasons. `HANDOFF.md` explicitly asks the user to run:

```
rm -rf .venv-fresh .venv-linux .venv-sandbox .venv-test
```

locally. The canonical name is `.venv`; everything else is leftover from CI / sandbox experimentation. **Action:** user task, one shell command.

#### 5.5 [P1] Two `.DS_Store` files present despite `.gitignore`

```
.DS_Store              (8,196 bytes)
backend/.DS_Store      (size unknown)
```

`.DS_Store` is in `.gitignore` (so they aren't tracked), but they exist on disk and Finder will keep recreating them. `find . -name .DS_Store -not -path '*/node_modules/*' -not -path '*/.venv*/*' -delete` clears them; the gitignore prevents accidental commits.

#### 5.6 [P3] Three HTML reports in `reviews/` total 102 KB

```
reviews/2026-04-29-progress.html         (32,039 bytes)
reviews/2026-04-29-tech-debt-audit.html  (39,776 bytes)
reviews/2026-04-30-progress.html         (36,276 bytes)
```

They're rendered snapshots, not the markdown source. The corresponding `.md` files cover the same content. If the HTML versions are for sharing-with-non-developers, they belong in a separate `dist/` or `published/` folder. If they're build artefacts from a Pandoc step that doesn't exist here, they're stale snapshots and should be `.gitignore`d.

**Action:** confirm with the user whether to keep, move, or git-ignore.

#### 5.7 [P3] `reviews/` has 18 entries totalling 428 KB

Useful historical record. No issue today. Mentioning so it's on the radar before the directory grows to thousands of dated snapshots — a `reviews/archive/<year>/` rollup at the year boundary would keep the working set scannable.

---

### 6. Strengths — keep these

Things the codebase gets right, worth naming so they don't regress:

- **`backend/storage/filesystem.py`** is exemplary. F_FULLFSYNC on Darwin (`_full_fsync`), parent-directory fsync after rename (`_fsync_dir`), every primitive cites the relevant POSIX/Win32/LWN reference inline. The 255-byte UTF-8 cap is the right cross-filesystem floor and the comment explains why.
- **`backend/xml/validator.py`** layers DOCTYPE byte-scan + lxml hardening + 10MB cap. Tests pin XXE, billion-laughs, external DTD, AND DOCTYPE-in-CDATA (a "deliberate false positive that erring on the side of rejecting is the correct posture"). This is what good security testing looks like.
- **`backend/storage/manifest.py:11-26`** documents the `generate()` vs `from_jump_xml()` distinction at the top of the file. This is exactly the kind of design pattern that prevents future contributors from blessing corruption by calling the wrong function on a recovery path.
- **`backend/observability/logging.py`** correctly chose pure-ASGI middleware over `BaseHTTPMiddleware` (cited as broken for contextvar propagation, with starlette issue numbers) and silences `uvicorn.access` belt-and-braces. The reserved-field collision check raises with a helpful message.
- **`backend/api/errors.py`** with RFC 6901 escape ordering (`~` first, then `/`), `about:blank` rationale, and the extension-collision raise. Every test, every doctest, every example shows the right shape.
- **CI**: 3 OS × 3 Python = 9 backend cells running ruff + pyright + pytest, plus a Node 20 Vitest job. `uv sync` for locked deps, `concurrency: cancel-in-progress` for stale-push cleanup. Setup-uv pinned to immutable patch tag. Solid.
- **Test posture**: 71 test files, 1,496 `def test_` functions, only 1 file uses `Mock`/`MagicMock`. Integration over unit, real `tmp_path` over fake filesystems. Per CLAUDE.md §7.
- **No `console.log`/`console.debug` left over** in the frontend. `grep -rE 'console\.(log|debug)' frontend/src/ -- not .bak` returns 0.
- **No `TODO`/`FIXME`/`XXX` markers** anywhere in `backend/` or `frontend/src/`. Every deferral is tracked through DECISIONS.md and the `reviews/` audits.

---

## Suggested execution sequence

If picking one slice this week, the brief-list in priority order:

1. **(P1, 10 min)** Add `*.bak*` and `frontend/vitest.config.*.timestamp-*.mjs` to `.gitignore`. Delete stale `.bak*`, vitest timestamps, vite timestamps, `.DS_Store`. Tell the user to `rm -rf .venv-fresh .venv-linux .venv-sandbox .venv-test` locally.
2. **(P1, 30 min)** Fix the `_write_lock.py` docstring to match current code (reads-that-may-write-via-reconcile DO acquire the lock). Optionally add a D-entry note clarifying the policy for future contributors. Per CLAUDE.md §2 the code is authoritative, so this is a doc fix not a behaviour change.
3. **(P1, 5 min)** Delete the six inert `it.skip` stubs in `frontend/test/`.
4. **(P2, 30 min)** Add `test_openapi_problem_details_alignment.py` asserting the hand-authored schema and the Pydantic model agree. Cheap regression net.
5. **(P2, ~1 day)** Extract `Jump`/`JumpCreate`/`JumpUpdate` shared fields into a `_JumpFields` mixin. Same shape for `Dropzone`, then per-component model families.
6. **(P2, ~2-3 days)** Decompose `LogJumpModal.jsx` (2,353 LOC → ~6 sub-components). Apply the same shape to the next-biggest modals later.
7. **(P3, ~1-2 weeks of incremental work)** Adopt TypeScript with `allowJs`. Migrate `api.js` first (split + type from `/openapi.json`), then per-view as touched.
8. **(P3, ~1 day)** Split `backend/xml/serialize.py` and `backend/services/{jump,rig}_service.py` into per-entity / per-operation modules.

The first three are an afternoon and ship a measurably tidier repo without risking any of the invariants.

---

## What this audit explicitly did NOT find

These were checked and look clean:

- **No direct `open(..., "w")` in production code** outside `atomic_write`/`atomic_write_stream`. `grep -rnE 'open\([^)]*["'"'"']w' backend/ -- not tests` matches only the two intentional sites inside `filesystem.py`. D10 invariant is preserved.
- **No `lxml.etree` direct imports outside `backend/xml/`** in production code. Tests import lxml directly for XSD-shape checks, which is fine. D2's hardened-parser invariant is preserved.
- **No `safe_join` bypass**. Every path construction in services goes through `sanitize_filename` / `sanitize_folder_name` / `jump_folder_name` / `safe_join`.
- **No NFC-skip on writes**. `sanitize_filename` and `sanitize_folder_name` both call `normalize_nfc` first. D4 invariant preserved.
- **No `dict` returns from API handlers in error paths**. Every error goes through `ServiceError` → `error_response` → RFC 9457 problem+json. D16 invariant preserved.
- **No HTTP-type leakage into services**. Services use `Upload` dataclasses, raise `ServiceError` subclasses, never import `fastapi`. D7 invariant preserved.
- **No bare `except:` clauses** anywhere in production code. Every `except` is typed.
- **No `console.log` / `console.debug` / `console.warn` left over** in `frontend/src/`.
- **No `TODO`/`FIXME` markers** anywhere in `backend/` or `frontend/src/`. The discipline of tracking deferrals through DECISIONS.md and `reviews/` is being held.
- **Pinned CVE floor for `starlette` and `python-multipart`** is in `pyproject.toml` with inline references.
- **OpenAPI is mounted at unversioned `/openapi.json`** per the spec's discoverability guidance; `/docs` interactive UI included.

---

## Methodology and limits

- Read all of `CLAUDE.md`, `ARCHITECTURE.md`, the most recent `HANDOFF.md` and `2026-04-30-finish-open-items.md`. Skimmed `DECISIONS.md` (342 KB) by D-entry headers (62 entries).
- Read full source of: `filesystem.py`, `manifest.py`, `lockfile.py`, `index.py`, `validator.py`, `bootstrap.py`, `_write_lock.py`, `errors.py`, `rest.py`, `openapi.py`, `deps.py`, `jumps.py`, `observability/logging.py`, `models/jump.py`, `models/__init__.py`, `services/file_service.py`, `dropzone_service.py` (header), `reindex_service.py` (first 300 lines), `jump_service.py` (~800 lines of 1,349). Sampled the rest by grep + targeted reads. Read CI workflow, `pyproject.toml`, frontend `package.json` / `vite.config.js` / `vitest.config.js`, every frontend test file.
- Verified every file:line citation against current code on disk before writing. Per CLAUDE.md §2 the code is the source of truth, and the docstring contradictions in finding 1.1 / 1.4 are exactly the case the policy was written for.
- **What this audit did NOT do:** run tests, run `pytest`/`pyright`/`ruff`/`vitest` (this was report-only per the brief). Did not benchmark `open_index` (deferred per prior profile). Did not run `verify` against a real logbook. Did not inspect the rig-manager service modules (`rig_service.py`, `main_service.py`, `aad_service.py`, `container_service.py`, `reserve_service.py`) in depth — high-level grep + spot-reads only. Did not inspect `jumper_credential_service.py` (863 LOC). The patterns identified across `jump_service` and `dropzone_service` are likely to apply, but each module deserves its own pass before any large refactor lands.

---

*— end —*
