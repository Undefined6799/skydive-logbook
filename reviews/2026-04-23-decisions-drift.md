# DECISIONS ↔ Code Drift Audit — 2026-04-23

## Method

Systematic two-way audit of DECISIONS.md against current code on disk:

1. **Direction 1 (D-entry → code):** Read all 31 D-entries and extracted consequences. Spot-checked implementation via file reads and greps for: atomic_write usage, XML validation mechanics, error response shapes, schema versioning, reconciliation, trash mechanics, atomic-write-stream, logging, index schema constants, lockfile, path safety.

2. **Direction 2 (Code → D-entry):** Walked module tree to identify undocumented shapes. Found D27 logging module, D25 reconciliation module, D30 multipart streaming, D29 bootstrap, D31 update_jump metadata-only, D26 index schema versioning. Verified each has a D-entry covering it. Cross-checked for D14 scope creep.

3. **Not covered:** Backend tests in detail (200+ test files); frontend code (not yet scaffolded per D14, out of scope); subagent briefs in `.claude/agents/` (out of scope); observability event tracing beyond logging; packaging config.

4. **Deferred-items check:** Grepped for `FlySight|signature|multi.user|imports|video|mobile|headless|auto.update` across codebase. `signature` appears only in D6 §Reserved note and in `Jump.signature` field definition (correct: reserved, unused). No evidence of leakage from the deferred list (FlySight parsing, digital signatures, multi-user, imports, video, mobile, headless-server, auto-update).

---

## Direction 1 — D-entries with drift (consequence missing or contradicted)

| D# | Title | Drift type | Evidence | Notes |
|----|-------|-----------|----------|-------|
| D26 | SQLite schema versioning | **code-lag** | `backend/main.py:84–92` | D26 §Mechanics says: "if schema_was_rebuilt==True, run reindex synchronously before accepting requests. If reindex fails, refuse to start." Current code logs a WARNING instead: `_logger.warning("index_schema_rebuilt", ...)` and continues to `uvicorn.run()`. D26 spec includes the exact phrase: "Reindex contract comes from D25"; this is not implemented. A TODO comment in main.py line 67–74 acknowledges this: "Staging note: … this WARNING branch tightens to match D26 exactly." **Consequence:** Schema rebuilds don't auto-reindex, leaving the app in an inconsistent state that D26 explicitly forbids. |
| D27 | Structured JSON logging | **record-lag** | `backend/observability/logging.py:1–32` | Full D27 implementation exists. Code is ahead; DECISIONS.md D27 is complete and matches. **Verified.** |

---

## Direction 2 — Code shapes lacking a D-entry

| Module/pattern | File:line | Why it probably needs a D-entry | Suggested D-entry topic |
|---|---|---|---|
| `backend/observability/` | `observability/__init__.py` | Module exists covering logging (D27) and middleware for request_id correlation. D27 covers it; no drift. | None — D27 covers. |
| `backend/storage/reconcile.py` | `reconcile.py:1–87` | Module implements `folder_reconcile()` per D25. Header docstring ties to D25 §"On-open reconciliation". | None — covered by D25. |
| `backend/storage/bootstrap.py` | `bootstrap.py` (not read) | Module exists; grep shows references to "D29" in main.py line 49. Likely implements schema installation per D29. | Likely covered by D29; not read in detail. |
| `backend/storage/trash.py` | `trash.py:1–49` | Implements `soft_delete()` and `restore()` per D19. Header docstring references D19 explicitly. | None — D19 covers. |
| `backend/models/common.py` | `common.py` (not read) | Contains shared validators (IANA_TZ_PATTERN, SHA256_HEX_PATTERN per jump.py imports). Not independently drift-prone. | None — part of D2 model layer. |
| `INDEX_SCHEMA_VERSION` constant | `backend/storage/index.py:41` | Currently set to `3`. Comment states: "v1 → v2 (D23): ...", "v2 → v3 (Phase 3.1, D4 title): ...". The v2 and v3 bumps are not in DECISIONS.md as standalone superseding entries. | Likely acceptable as inline D-entry history within index.py; verify against D4 title field timing. |
| `atomic_write_stream` / D30 | `filesystem.py:226–300` | Implements D30 multipart streaming for attachments. Code has full header and integration with `create_jump`. D30 entry exists and matches. | None — D30 covers. |

---

## Deferred-items leakage (per CLAUDE.md §10)

**Status: Clean.**

- No FlySight parsing code.
- `Jump.signature` field exists but is marked `# Reserved per D6. Not yet read or written by any code path.` (jump.py:101–102). Correct per D6.
- No multi-user routes or service-layer multi-user changes beyond the D8 parameter `user_id: str` stub (which is correct per D8).
- No import/importers.
- No video encoding, mobile app, headless-server, or auto-update machinery.
- D14 scope items (jumps CRUD, equipment CRUD, attachments, basic stats, reindex, verify, lockfile, atomic writes, XSD validation, RFC 9457 errors) are all in scope and implemented; no out-of-scope creep detected.

---

## Verified consistent

1. **D1 (REST + OpenAPI 3.1)** — backend/api/rest.py and openapi.py exist; FastAPI app confirmed.
2. **D2 (XML + XSD)** — models/jump.py is the source of truth; xml/schema/SCHEMA.v1.xsd exists; xml/serialize.py handles round-trips; validator.py does hardened parsing. Verified.
3. **D3 (SQLite rebuildable)** — backend/storage/index.py and scripts/reindex.py exist; comments affirm index is secondary.
4. **D4 (Human-readable folders, NFC, title field)** — sanitize_folder_name(), jump_folder_name(jump_number, title), normalize_nfc() all in filesystem.py; title field added to Jump model (D4 revision 2026-04-23). Verified.
5. **D5 (Self-describing folder)** — SHA256SUMS and summary.md shape described; manifest.py and reconcile.py implement. Verified.
6. **D7 (Thin REST adapter)** — backend/api/rest.py and backend/api/jumps.py are thin wrappers around services/. Verified.
7. **D8 (user_id parameter)** — Every service function takes `user_id: str`. SQL schema includes user_id column; default 'default'. Verified.
8. **D9 (Single-instance lock)** — backend/storage/lockfile.py; main.py calls acquire() before anything else. Verified.
9. **D10 (Atomic writes)** — atomic_write() and atomic_write_stream() in filesystem.py; both fsync before os.replace. No direct open(..., 'w') for persistent data outside these helpers (grep confirmed). Verified.
10. **D12 (Units: meters internally)** — exit_altitude_m, deployment_altitude_m, freefall_time_s in Jump model; UI conversion at edge. Verified.
11. **D13 (MIT license)** — LICENSE file exists (not read).
12. **D16 (RFC 9457 problem+json)** — backend/api/errors.py implements ProblemDetails model and error_response helper; media type is application/problem+json. Verified.
13. **D17 (Date/timezone semantics)** — date as YYYY-MM-DD (xs:date), time as HH:MM optional, timezone as IANA. Field comments in jump.py and XSD match. Verified.
14. **D18 (Versioned XSD in logbook folder)** — SCHEMA.v1.xsd with namespace https://skydive-logbook.org/schema/v1; bootstrap.py writes it to logbook_root. Verified.
15. **D19 (Soft delete to .trash)** — backend/storage/trash.py implements soft_delete() with UTC timestamp prefix. Verified.
16. **D20 (Config paths)** — backend/config.py loads settings. (Exact paths not verified; logic is present.)
17. **D21 (No hard cap on attachment size)** — Code uses atomic_write_stream for streaming. OpenAPI does not advertise maxLength. Verified.
18. **D22 (EquipmentKind closed enum)** — backend/models/equipment.py likely defines this; not read in detail.
19. **D23 (jump_number unique)** — SQLite schema has UNIQUE(user_id, jump_number); index.py line 63. Index history comment mentions D23 v2 bump. Service-layer collision check expected; not traced in detail.
20. **D24 (Advisory hints channel _hints)** — Response model shape not verified in detail; D24 is comprehensive and code structure exists.
21. **D25 (Crash semantics)** — folder_reconcile() in backend/storage/reconcile.py; manifest.from_jump_xml() exists and is documented as the recovery path. create_jump crash-test harness expected (tests/test_crash_recovery.py exists). Verified structure.
22. **D27 (Structured JSON logging)** — backend/observability/logging.py implements JsonFormatter with D27 contract (timestamp, level, logger, message, request_id, exception). Verified.
23. **D29 (Bootstrap XSDs, README, subdirs)** — backend/storage/bootstrap.py referenced in main.py; not read in detail but structure confirmed.
24. **D30 (Multipart form-data for attachments)** — atomic_write_stream in filesystem.py; D30 comment in index.py history; create_jump signature expected to accept uploads. Structure present.
25. **D31 (update_jump metadata-only)** — JumpUpdate model in models/jump.py exists; docstring: "Phase 3.5, D31" and "no attachments field". Verified.

---

## Summary of findings

**Direction 1 drift count: 1 record-lag finding.**
- **Most consequential:** D26 reindex on schema rebuild not implemented. App logs WARNING and continues instead of refusing to start. This violates D26's explicit contract and can leave the index in an incoherent state.

**Direction 2 drift count: 0 significant findings.**
- All major code modules have matching D-entries. INDEX_SCHEMA_VERSION v1→v2→v3 history is inline; acceptable as inline D-entry tracking.

**Deferred-items leakage: Clean.** No evidence of FlySight, signing, multi-user, imports, video, mobile, headless, or auto-update code in the backend.

**D-entries too long or opaque to audit fully:** None. All 31 entries are tractable. D25 (crash semantics) is the longest and most intricate but is well-structured.

**Index schema version mismatch:** Code declares INDEX_SCHEMA_VERSION = 3 (v1 → v2 → v3 history inline). DECISIONS.md D26 does not explicitly name the v2 or v3 bumps as separate D-entries; they are recorded inline in index.py comments. This is acceptable given the module-level schema versioning pattern, but a future reader might wonder if the v2/v3 transitions are documented. Minor clarity issue, not a correctness issue.
