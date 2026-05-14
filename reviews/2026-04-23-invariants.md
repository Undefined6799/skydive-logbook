# Invariants & Security Audit — 2026-04-23

## Scope & method

Systematic audit of load-bearing invariants from CLAUDE.md §5 across the backend codebase:
1. Atomic writes (D10)
2. XSD validation before write (D2)
3. Hardened XML parser (D2 consequences)
4. SQLite never authoritative (D3)
5. Path safety + Unicode NFC (D4)
6. RFC 9457 errors only (D16)
7. Lockfile race windows (D9)

Methodology: grep-based search for direct file operations, XML parsing calls, unvalidated writes, path concatenations, and error handling, followed by targeted code reads to verify the invariants.

## Findings

| # | Severity | Invariant (D#) | File:line | Finding | Suggested fix |
|---|----------|----------------|-----------|---------|---------------|
| 1 | CRITICAL | D16 | backend/api/rest.py:45 | Only `ServiceError` has an exception handler; unhandled exceptions (syntax errors, async task crashes, unknown exceptions) will return FastAPI's default 500 response (plain HTML) instead of RFC 9457 application/problem+json. | Add a catch-all exception handler that logs the error and returns error_response() with a generic "internal_error" code and request_id. |

## Clean passes

**Atomic writes (D10):** Every persisted write goes through `atomic_write` or `atomic_write_stream` in `backend/storage/filesystem.py`. The implementation correctly fsyncs before `os.replace`. Verified:
- `backend/storage/filesystem.py:173–211` — atomic_write: fsync at line 202 before os.replace at line 203.
- `backend/storage/filesystem.py:226–300` — atomic_write_stream: fsync at line 293 before os.replace at line 294.
- `backend/services/jump_service.py:318–323` — validation then atomic_write for jump.xml.
- `backend/services/jump_service.py:296–329` — stream then atomic_write for attachments.
- `backend/storage/reconcile.py:81` — atomic_write for manifest regeneration.
- Test write_bytes usage (168 instances in test files) are all test fixtures, never persisted data paths.

**XSD validation before write (D2):** Every XML write is validated before `atomic_write`:
- `backend/services/jump_service.py:318–319` — element serialized, then `validate(element)` BEFORE `atomic_write` at line 323.
- `backend/services/jump_service.py:595–596` — same pattern in `update_jump`.
- `backend/storage/manifest.py:131–140` — `from_jump_xml` validates the element for recovery-path manifest generation.
- No calls to `jump_to_bytes()` or `equipment_to_bytes()` observed outside these validated contexts.

**Hardened XML parser (D2 consequences):** All XML reads go through `backend/xml/validator.py:parse`:
- `backend/xml/validator.py:87–95` — parser configured with: `resolve_entities=False`, `no_network=True`, `load_dtd=False`, `dtd_validation=False`, `huge_tree=False`.
- `backend/xml/validator.py:106–109` — DOCTYPE declarations rejected before lxml sees bytes (regex scan).
- No other `lxml.etree.parse()`, `fromstring()`, or `XMLParser()` calls found in non-test code paths. The only other parse is `backend/xml/validator.py:122` which loads the XSD (app-shipped, trusted; DTD bypass is explicitly documented as safe in D2 and necessary per lxml's XSD loader internals).
- OWASP XXE Prevention Cheat Sheet compliance verified: entity resolution off, network access disabled, DTD loading disabled, external resources blocked.

**SQLite never authoritative (D3):** Every field in the index exists in XML; reindex can rebuild from XML alone:
- `backend/storage/index.py:46–80` — schema has only (id, user_id, jump_number, date, dropzone, title, folder, schema_ns, created_at, updated_at); all derive from XML.
- `backend/services/jump_service.py:340–359` — index row inserted AFTER XML write.
- `backend/services/jump_service.py:395–441` — reads via index lookup then parse XML for authoritative data.
- `backend/scripts/reindex.py` can walk `jumps/` and `equipment/` folders and rebuild the entire index from XML (verified: script imports `folder_reconcile`, opens logbook, walks folders, parses XML, inserts rows).

**Path safety + Unicode NFC (D4):** User-derived paths go through `safe_join` and sanitization:
- `backend/storage/filesystem.py:157–170` — `safe_join()` validates each part via `sanitize_folder_name()` and checks final resolved path is within root (zip-slip safe).
- `backend/storage/filesystem.py:81–101` — `sanitize_folder_name()` calls `normalize_nfc()` at line 96, rejects forbidden chars, Windows reserved names, trailing space/period.
- `backend/storage/filesystem.py:110–128` — `sanitize_filename()` calls `normalize_nfc()` at line 119, applies same rules, plus 255-byte cap.
- `backend/services/jump_service.py:254–259` — `jump_folder_name()` calls `sanitize_folder_name()` which applies NFC.
- `backend/services/jump_service.py:155` — attachment filenames sanitized via `sanitize_filename()` before any disk write.
- No `pathlib.Path(user_input)`, `os.path.join(user_input, ...)`, or string `/` concatenation with untrusted data found.

**RFC 9457 errors only (D16):** All service errors return RFC 9457 problem+json:
- `backend/api/errors.py:45–195` — `error_response()` produces `application/problem+json` via JSONResponse with correct media type.
- `backend/api/rest.py:45–68` — `@app.exception_handler(ServiceError)` catches all service errors and returns `error_response()`.
- `backend/api/jumps.py` — all route handlers call service functions; `ServiceError` subclasses bubble to the handler.
- No bare `raise HTTPException`, `return {...}, 500`, or unstructured error bodies found.

**Lockfile race windows (D9):** Single-instance lock via filelock library:
- `backend/storage/lockfile.py:20–37` — uses `filelock.FileLock` which wraps fcntl (POSIX) / msvcrt (Windows).
- fcntl advisory locking is per IEEE Std 1003.1; msvcrt locks on Windows are exclusive file locks.
- Lock is acquired on app start in `backend/main.py` and held for the app lifetime (verified by checking main.py pattern).
- Timeout is 0.5s (reasonable); on timeout raises `LockError` so the app refuses to start if another instance is running.
- No TOCTOU window: lock is acquired before any logbook operation.

## References

**D-entries:**
- D2 — XML on disk is the source of truth, validated against versioned XSD
- D3 — SQLite is an index, rebuildable from the XML
- D4 — Human-readable folder names; stable UUIDs in the XML
- D9 — Locking and single-instance enforcement
- D10 — Atomic writes everywhere
- D16 — Structured error responses (RFC 9457 problem+json)
- D18 — XSD versioning inside the logbook folder
- D25 — Crash semantics for multi-file writes: XML is truth, manifest is derived

**RFC/Standards:**
- RFC 9457 — Problem Details for HTTP APIs
- RFC 6901 — JSON Pointer
- IEEE Std 1003.1 — POSIX advisory locking semantics
- OWASP XXE Prevention Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/XML_External_Entity_Prevention_Cheat_Sheet.html

**Upstream libraries:**
- lxml 5.x / 6.x — XML parser and XSD validator
- filelock — cross-platform file locking (fcntl/msvcrt wrapper)
- FastAPI — REST framework with OpenAPI generation
