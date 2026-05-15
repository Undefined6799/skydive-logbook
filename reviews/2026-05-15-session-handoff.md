# Session handoff — 2026-05-15

You are picking up a multi-slice audit-driven cleanup branch
(`claude/code-debt-analysis-VgIBB`). The prior session shipped 12
commits over 2 audits' worth of findings and is at a clean checkpoint
— every gate green, no unfinished work in the tree.

This document is the **fast on-ramp**. Two-minute read; then either
open a PR for what's already here or carry on with the three remaining
slices.

---

## State of the branch right now

`git log main..claude/code-debt-analysis-VgIBB --oneline` (newest first):

```
103fb31 Slice 21: configurable CORS allow-list
8728b48 Slice 11: magic-bytes content-type sniffing on uploads
c459313 Slice 10: per-request + per-file body-size caps
9401f4e Slice 5: normalize FastAPI built-in errors to RFC 9457 problem+json
d7f16f6 Slice 6: pin ProblemDetails OpenAPI ↔ Pydantic alignment
6bfed69 Slice A-finalize/b: fix OpenAPI $ref+sibling bug; consolidate 500 responses
d05bf99 Slice A-finalize/a: D67 + D68 + CHANGELOG + stale-docstring cleanup
4e74dd0 Slice 4: OpenAPI responses= + operation_id on every route
235f652 Slice 3: settings-gated exception redaction + bind_host warning
abc6b1e Slice 8: open_index refuses newer-on-disk schema
98468c1 Slice 2: frontend nav fix + backend code-hygiene
3c1c312 Slice 1: documentation truth-up + delete dead code
66a7192 Add prioritized slice plan synthesizing all audit findings
aa8557c Verify the ChatGPT technical sweep against actual code
b232b1f Add deep-dive walkthrough of ChatGPT-flagged findings
90a4d26 Verify ChatGPT review findings against code; add Appendix A
652925a Add 2026-05-15 deep code-debt audit
```

**Test counts:**
- Backend: **1788 passed, 1 Darwin-only skip** (was 1737 on `main`; +51).
- Frontend: **65 passed**, unchanged from `main`.

**Static gates:** `ruff check backend` clean. `pyright backend` reports
**0 errors, 0 warnings, 0 informations**.

Run before starting work:

```bash
uv sync --extra dev
uv run ruff check backend
uv run pyright backend
uv run pytest backend/tests -q
cd frontend && npm install && npm test --silent
```

---

## What the audit documents say (read in this order)

All live under `reviews/`:

1. **`2026-05-15-code-debt-deep-audit.md`** — the root audit. 788 lines.
   Section IDs (§1.1 … §6.6) are referenced by every later doc.
   Appendix A cross-checks ChatGPT's review.
2. **`2026-05-15-chatgpt-findings-deep-dive.md`** — 8 findings traced
   through code with file:line citations. Most have already shipped.
3. **`2026-05-15-chatgpt-technical-sweep-verification.md`** — verifies a
   second ChatGPT review. Headline ChatGPT claim ("only Jumps Log
   wired") was stale; the README has been corrected.
4. **`2026-05-15-slice-plan.md`** — 32 slices, 5 waves, dependency
   graph. **This is the canonical roadmap.** Wave A is done; Wave B
   is mostly done (3 slices left); Waves C–E are future work.

DECISIONS.md was updated with **D67** (exception-redaction policy)
and **D68** (newer-on-disk index refusal). CHANGELOG.md has an open
`[Unreleased]` section listing every Wave A + B.1 + most of B.2.

---

## Wave B remaining work (in priority order)

Per the slice plan, "Wave B = gate D48 LAN exposure" — 7 slices total.
**5 are done.** Three left:

### Slice 12 — `Idempotency-Key` for multipart POSTs (≈4 hours, P2)

**Why:** Retry-on-network-stutter on `POST /api/v1/jumps` currently
creates duplicate jumps. The audit's main §1.4 and the deep-dive's
Finding 6 flagged this.

**Design (already settled):**

- New SQLite table `idempotency_keys` in `backend/storage/index.py`:
  ```sql
  CREATE TABLE idempotency_keys (
      key TEXT PRIMARY KEY,
      user_id TEXT NOT NULL,
      request_hash TEXT NOT NULL,
      response_status INTEGER NOT NULL,
      response_content_type TEXT,
      response_body BLOB NOT NULL,
      created_at TEXT NOT NULL,
      expires_at TEXT NOT NULL
  );
  CREATE INDEX idx_idempotency_expires ON idempotency_keys(expires_at);
  ```
- Bump `INDEX_SCHEMA_VERSION = 11` (Slice 8's refusal logic protects
  the user from a downgrade; D26 covers the rebuild).
- New D-entry **D69** pinning the semantics: scope = POST routes
  only; hash = `sha256(method + path + user_id + content_length +
  first_4KB_of_body)`; TTL = 24h; on hit + matching hash = replay
  full stored response (status + body + content_type); on hit +
  different hash = 422 `code=idempotency_key_reuse` (extend the
  `errors.py` typed exception hierarchy with `IdempotencyKeyReuseError`).
- ASGI middleware in `backend/api/middleware.py` (sits next to
  `RequestSizeLimitMiddleware` from Slice 10):
  - On POST with `Idempotency-Key` header: look up; on hit, build
    the replay response and short-circuit (same inline-build pattern
    Slice 10 uses); on miss, wrap `send` to capture the response,
    then commit on `http.response.body` with `more_body=False`.
  - Opportunistic cleanup: `DELETE FROM idempotency_keys WHERE
    expires_at < now()` once per request (cheap; covered by the
    `idx_idempotency_expires` index).
- OpenAPI: add a new component `IdempotencyKeyReuse` (409 or 422 —
  decide; Stripe uses 422 to mean "same key, different body"). Add
  to `ERR_CREATE` and `ERR_UPDATE`.
- Test file: `backend/tests/test_idempotency_key.py`. ~10 tests
  including the happy-path replay, hash mismatch, expiry, no-header
  passthrough.

**File touch list:**
- `backend/storage/index.py` (schema bump, table creation)
- `backend/api/middleware.py` (new middleware class)
- `backend/api/rest.py` (`add_middleware`)
- `backend/api/errors.py` (new typed exception)
- `backend/api/openapi.py` (new component + ERR_* update)
- `DECISIONS.md` (D69)
- `CHANGELOG.md` (Unreleased section)
- `backend/tests/test_idempotency_key.py` (new)

**Open question for the human / next session:**

- Stripe-style validation hashes the **whole body**. We can't do that
  for multipart (a 2 GiB video bytewise hash would defeat retry
  performance). Proposed compromise (above): hash `method + path +
  user_id + content_length + first_4KB_of_body`. This catches honest
  retries (same content reproduces same hash) and rejects malicious
  reuse with a different request (content_length differs OR the
  multipart preamble differs). **Confirm this is acceptable before
  shipping** — if you'd rather require exact body match for JSON
  endpoints and skip multipart entirely, the design tightens.

### Slice 7 — `folder_reconcile_rigs` + crash test (≈1 day, P2)

**Why:** Main audit §2.2 and deep-dive §8.1. `create_rig` is
non-atomic post-`rig.xml`-write: components 1..4 get back-linked in a
loop, and a crash partway through leaves rig.xml referencing all four
components but only the iteration-completed ones pointing back. The
**retry path is doubly broken** — `mkdir(exist_ok=False)` fails on the
existing folder, AND `_validate_component_for_assignment` rejects the
half-bound components because their `assigned_rig_id` points at the
old rig's UUID. Manual XML edit is the only user-facing recovery
today.

**Design:**

- New `folder_reconcile_rigs(logbook_root)` in
  `backend/storage/reconcile.py`. Idempotent. Walks
  `rigs/*/rig.xml`; ensures each referenced component's
  `assigned_rig_id` matches the referring rig; repairs under
  WRITER_LOCK via the existing service-layer `_write_*` helpers.
- For each component XML: if `assigned_rig_id` is set but the named
  rig is missing or doesn't ref back, clear it.
- Boot wiring: `backend/main.py`, after `bootstrap_logbook()` and
  `open_index()`, before `uvicorn.run`. Same posture as the existing
  `folder_reconcile` for jumps.
- New D-entry **D70** pinning the policy decision (deep-dive §8.1
  lists two defensible policies — forward-complete the assignment
  OR revert the rig). Pick one and document.
- Test: `backend/tests/test_rig_partial_create_recovery.py`. Use
  monkeypatch to raise after the first iteration of the
  component-assigner loop; assert (a) the on-disk state is the
  documented partial state; (b) after `folder_reconcile_rigs` runs,
  state is converged per the chosen policy.

### Slice 9 — Partial-write crash matrix (≈1 day, P2)

**Why:** Main audit §5.1, §5.2, §2.3; deep-dive §8.1, §8.2, §8.3;
sweep verification §8. The audit identified at least three crash
points without explicit tests:
- `create_rig` partial-create (covered by Slice 7).
- `update_jump` between `os.rename` and index update.
- `track_files` size/hash race.

**Design:**

- New `backend/tests/test_partial_write_recovery.py`. Parametrized
  matrix over the multi-step writes:
  - `create_jump`, `update_jump`, `delete_jump`
  - `add_attachments`, `track_files`, `delete_attachment`
  - `create_rig`, `update_rig`, `delete_rig`
  - `jumper_migration.migrate_all_jumpers`
- For each operation × each documented step boundary: monkeypatch-
  raise the step after; assert (a) `verify_logbook` detects exactly
  the expected residue; (b) the next public read does NOT crash;
  (c) `reindex_from_xml` converges to a clean index.
- ~12-15 tests total.

**Compounds with Slice 7** — once `folder_reconcile_rigs` ships, this
matrix tests it. Order: Slice 7 first, then Slice 9.

---

## Process notes from the prior session

- **D-entries are mandatory** per CLAUDE.md §4 for any cross-cutting
  change. The prior session left two `D-NEW` placeholders in shipped
  code; the A-finalize/a commit cleaned them up. Don't ship code
  with `D-NEW` references — write the D-entry first, even tersely.
- **CHANGELOG.md `[Unreleased]` section** is the running log. Update
  it per slice.
- **Commit style**: each slice is one self-contained commit. Title
  format `Slice N: <short summary>` (no leading verb). Detailed body
  describing the why + the test surface. The branch's `git log` is
  itself a review surface.
- **Test isolation**: the API integration tests need
  `bootstrapped_root(tmp_path)` + `app.dependency_overrides[get_logbook_root]`
  to avoid writing to the developer's real `~/SkydiveLogbook`. See
  `backend/tests/test_request_size_limits.py` and `test_rest_jumps.py`
  for the pattern. **Tests that pass in isolation but fail in the
  full suite** almost always have this isolation bug.
- **LogRecord-attribute collisions**: `filename` is reserved by
  stdlib logging. Use `upload_filename`, `attachment`, etc. instead
  if logging a filename. The pattern is documented inline at the
  raise sites in `jump_service.py` and `uploads.py`.
- **`get_settings` is `lru_cache`'d**: tests overriding it need to
  patch the module-level reference for build-time reads AND set
  `app.dependency_overrides[get_settings]` for handler-level reads.
  Pattern in `test_request_size_limits.py:_build_client` and
  `test_cors_configurability.py:_build_client`.

---

## After Wave B

The remaining waves from the slice plan, summarised:

- **Wave C** — gate v0.2 release (Slices 13, 14, 15, 16, 17, 26, 27).
  Cache-Control/ETag, frontend error surfacing, self-host Google
  Fonts, release pipeline + SBOM. ~3 days.
- **Wave D** — gate "serious desktop app" framing (Slices 18, 19, 20,
  22, 23, 24, 25, 28). Signed binaries, trash + backup features,
  model dedup, structural splits, a11y pass. ~1 week.
- **Wave E** — long horizon (Slices 29, 30, 31, 32). TypeScript,
  modal decomposition, property tests, FlySight CSV. Multi-week.

See `reviews/2026-05-15-slice-plan.md` §"Recommended execution
waves" for the full breakdown.

---

## Two-minute orientation

The simplest path forward:

1. `git log -p main..HEAD -- reviews/` to see the four audit docs
   in full.
2. `git log -p main..HEAD -- '*.py'` for the actual code changes.
3. Pick a slice from the "Wave B remaining" section above. Slice 7
   (rig recovery) is highest correctness value; Slice 12
   (Idempotency-Key) is highest LAN-readiness value; Slice 9 (crash
   matrix) is highest test-net value but depends on Slice 7.
4. Read its design notes here. Confirm the open question (if any).
5. Ship the slice as one commit. Run all gates. Update CHANGELOG.

The branch is in a clean state — there's no "in-flight" work to
finish. Whatever you ship next stands alone.

---

*— end —*
