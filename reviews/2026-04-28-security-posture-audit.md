# Security Posture Audit — 2026-04-28

**Scope:** Comprehensive re-verification of XXE/entity-expansion, path traversal, lockfile safety, SQLite hardening, multipart upload limits, error-response leakage, and Bearer auth enforcement against current code.

**Date:** 2026-04-28  
**Baseline:** Forward-review report (2026-04-23), research reports (2026-04-23)

---

## Executive Summary

The codebase maintains a **strong security posture for v0.1 single-user local-first deployment**. All load-bearing invariants (D2, D3, D4, D10, D16, D25) are properly enforced in code. Three findings require action before v0.1 ships; one is critical, two are advisories backed by primary sources.

| Category | Status | Notes |
|----------|--------|-------|
| XXE / Entity Expansion | ✅ Secure | DOCTYPE byte-scan + lxml hardened parser; no bypass found |
| Path Traversal | ✅ Secure | `safe_join` + `sanitize_*` applied consistently |
| Lockfile Safety | ✅ Safe (local FS) | flock on POSIX; NFS/SMB caveats documented below |
| SQLite Hardening | ✅ Config OK | WAL mode set; synchronous=NORMAL; foreign_keys=ON |
| Multipart Limits | ⚠️ CRITICAL | `python-multipart` version floor admits three CVEs |
| Error Envelope | ✅ Correct | RFC 9457 enforced; unhandled exceptions caught |
| Bearer Auth | ⚠️ ADVISORY | Scheme advertised but not enforced; needs D-entry |
| Atomic Writes | ⚠️ ADVISORY | macOS fsync() doesn't guarantee disk durability |

---

## Critical Findings

### CRx1: `python-multipart` CVE Floor Vulnerable

**Severity:** CRITICAL  
**File:** `pyproject.toml:23`  
**Current:** `python-multipart>=0.0.9`  
**Required:** `python-multipart>=0.0.26`

**Issue:**  
The version floor admits three published CVEs:

1. **CVE-2024-24762** (ReDoS in Content-Type header)  
   - **CVSS 7.5** — service stall via malicious multipart boundary  
   - Fixed in 0.0.18
   - [GitHub Advisory GHSA-2jv5-9r88-3w3p](https://github.com/advisories/GHSA-2jv5-9r88-3w3p)

2. **CVE-2024-53981** (Logging DoS via preamble/epilogue)  
   - **CVSS 7.5** — CPU exhaustion from excessive logging  
   - Fixed in 0.0.25  
   - [Ubuntu Security Notice USN-8027-1](https://ubuntu.com/security/notices/USN-8027-1)

3. **CVE-2026-40347** (Preamble/epilogue parsing stall)  
   - **CVSS 6.5** — parsing hangs even after logging fix  
   - Fixed in 0.0.26 (April 2026)  
   - [GitHub Advisory GHSA-mj87-hwqh-73pj](https://github.com/advisories/GHSA-mj87-hwqh-73pj)

**Attack Scenario:**  
A developer (or CI system) regenerating `uv.lock` on a fresh machine, or a dependency update tool pinning within the floor, could resolve to 0.0.13 (vulnerable to CVE-2024-24762 + CVE-2024-53981) or 0.0.24 (vulnerable to CVE-2026-40347). A malicious actor sending a crafted multipart request causes the app to stall or exhaust CPU/IO, making the logbook unavailable.

**Blast Radius:**  
All multipart parsing — not just file uploads. Every `POST /api/v1/jumps` call uses `request.form()`, which internally invokes `python-multipart`. Even a non-loopback deployment bound to `127.0.0.1` is vulnerable if the user themselves or an automated tool sends a crafted request.

**Fix:**
```toml
python-multipart>=0.0.26
```

Single-line change; no code modifications needed.

---

## Major Findings

### MAJ1: Bearer Auth Scheme Declared but Not Enforced

**Severity:** MAJOR (operationally significant, not immediately exploitable at v0.1 defaults)  
**Files:** `backend/api/openapi.py:117–124`, `backend/api/rest.py`, `backend/api/deps.py:46–53`, `backend/config.py:75–76`, `backend/main.py:113`

**Current Posture:**

- `openapi.py` declares a `bearerAuth` security scheme describing when it is required:  
  > "Required only when the server binds to a non-loopback address. Loopback (127.0.0.1) connections skip authentication."
  
- `config.py` defines `api_key: str | None = None` (optional, not enforced).  
- `deps.py:get_user_id()` always returns `"default"` without reading any request header.  
- `main.py` binds to `settings.bind_host` (default `127.0.0.1`, configurable to `0.0.0.0`).  
- **No middleware checks for Bearer token. No condition on bind_host.**

**Concrete Failure Mode:**  
User sets `SKYDIVE_BIND_HOST=0.0.0.0` in environment or edits `config.toml` to `bind_host = "0.0.0.0"`. Binds to all interfaces. OpenAPI says auth is required. Code does not enforce it. The logbook is exposed to LAN without a token check, even if `api_key` is set in config.

**Why It Matters for v0.1:**  
- v0.1 is local-first (CLAUDE.md §10, D20). Default is `127.0.0.1` (loopback), and the typical user never edits `config.toml`.
- **However**, there is no enforcement preventing the user from misconfiguring themselves. The OpenAPI spec makes a promise the code doesn't keep. A developer integrating this app into a larger system, or a user who reads the code to understand security, will find the gap.
- The `api_key` field in config is vestigial — setting it has no effect.

**Prior Work:**  
Forward-review §A2 identified this gap and suggested two paths:
1. *Implement* the bearer check as a middleware that enforces auth when `bind_host != "127.0.0.1"`.
2. *Drop* the security scheme from OpenAPI until the middleware lands.

**Recommended Path for v0.1:**  
Create a new D-entry ("D46: Bearer authentication model and enforcement scope") that:
- Clarifies the v0.1 position: loopback-only by default, auth deferred to Phase X.
- Documents that `api_key` in config is a reserved field for future use, presently ignored.
- Either removes the scheme from OpenAPI (correct the spec), or adds a middleware stub that enforces it immediately (closes the gap).

**Primary Source:**  
- RFC 9457 — problem+json uses same contract everywhere; bearer auth must be enforceable or documented as deferred.

---

### MAJ2: `os.fsync` on macOS Does Not Guarantee Disk Durability

**Severity:** MAJOR (real-world risk on external drives and SSDs with write-back cache)  
**File:** `backend/storage/filesystem.py:173–210` (atomic_write) and `226–300` (atomic_write_stream)

**Current Code:**
```python
with open(tmp, "wb") as f:
    f.write(data)
    f.flush()
    os.fsync(f.fileno())  # <-- Issue on Darwin
os.replace(tmp, path)
```

**Issue:**  
On macOS (Darwin), CPython's `os.fsync` maps directly to BSD `fsync(2)`. Apple's own documentation states:

> "Note that while fsync() will flush all data from the host to the drive (i.e. the 'permanent storage device'), the drive itself may not physically write the data to the platters for quite some time and it may be written in an out-of-order sequence."
>
> "For applications that require tighter guarantees about the integrity of their data, Mac OS X provides the F_FULLFSYNC fcntl."

— [Apple fsync(2) manual page](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/fsync.2.html)

**Concrete Scenario:**  
A macOS user puts their logbook on an external USB SSD with write-back cache. After `atomic_write` returns, the app reports success. The bytes are in the drive's cache, not on the platter. A hard power loss or kernel panic *before* the drive completes its internal flush results in a missing or corrupted `jump.xml`. D3 makes the index rebuildable, but the jump data itself is lost.

**Blast Radius:**  
- **APFS (default on modern macOS):** File metadata (name, modification time) is journaled, so the directory entry survives. The danger is **file data loss**.
- **External USB drives / SSDs:** Firmware caching is common; the risk is higher.
- **Internal SSDs:** Modern NAND flash management makes data loss less likely, but not guaranteed by fsync(2).

**Comparison to Linux:**  
On Linux ext4 with `data=ordered` (default), the journal protects both metadata and (usually) data ordering, so a crash *after* `fsync + rename` returns keeps the new file. POSIX does not guarantee this; ext4's journal happens to be strong enough. SQLite's `fullfsync` pragma exists for exactly this reason.

**Fix Sketch:**  
1. **Option A (Recommended):** Wrap `os.fsync` on Darwin with a fallback to `fcntl(fd, F_FULLFSYNC)`.
   ```python
   import fcntl
   if sys.platform == "darwin":
       fcntl.fcntl(f.fileno(), fcntl.F_FULLFSYNC)
   else:
       os.fsync(f.fileno())
   ```
   
2. **Option B:** Document the trade-off and recommend against external drives.

**Why It Matters:**  
The project's framing is "data must outlive the app." This is a gap in that guarantee for macOS. Not a bug in the strict sense — the code correctly calls `fsync()` — but a portability blindness.

**Primary Sources:**
- [Apple fsync(2) documentation](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/fsync.2.html)
- [SQLite PRAGMA fullfsync](https://www.sqlite.org/pragma.html#pragma_fullfsync)
- [SQLite Replication, Durability, and Crash-safety](https://www.sqlite.org/replication.html)

---

## Advisory Findings

### ADV1: Parent Directory fsync Missing (Post-rename Durability)

**Severity:** ADVISORY (edge case on modern filesystems)  
**File:** `backend/storage/filesystem.py:173–210` (atomic_write)

**Issue:**  
The code fsync's the file, then calls `os.replace(tmp, path)` (POSIX `rename(2)`). POSIX guarantees `rename` is *atomic* but **does not guarantee durability** without an fsync on the *parent directory*. Per [Linux fsync(2) man page](https://man7.org/linux/man-pages/man2/fsync.2.html):

> "Calling fsync() does not necessarily ensure that the entry in the directory containing the file has also reached disk. For that an explicit fsync() on a file descriptor for the directory is also needed."

**Current Posture:**  
Only the file is fsync'd. The parent directory's inode (which contains the directory entry) is not.

**Practical Risk:**  
- **ext4 with `data=ordered`:** Journaling makes the risk negligible in practice.
- **ext4 with `noauto_da_alloc` or `data=writeback`:** Risk is real but rare on modern kernels (post-2009).
- **btrfs / XFS / APFS:** COW or similar techniques make the rename + metadata atomic without explicit parent fsync.
- **NTFS (Windows):** `MoveFileExW` is transactional; no parent fsync needed.

**Why Not Fixed:** The Windows code comment in the docstring notes MoveFileExW's cross-volume caveat; extending it to name parent-directory fsync on Unix would be pedantic for v0.1, since ext4 in default config is safe and the risk is covered by D3's rebuildability.

**Recommendation:**  
Document the trade-off in the `atomic_write` docstring. Not a code change for v0.1 (the risk is very low on modern Linux), but the decision should be recorded.

---

### ADV2: D24 `_hints` Channel Completely Unimplemented

**Severity:** ADVISORY (contract drift, not a security issue)  
**File:** Multiple (service layer, OpenAPI, error handling, tests)

**Issue:**  
DECISIONS.md:892–1016 (D24) prescribes a `_hints` array on write responses with one v0.1 code: `non_sequential_jump_number`. Grep across `backend/` for `_hints`, `Hint`, `non_sequential_jump_number`, `build_hint` returns **zero matches**. The feature is completely absent.

**Why It Matters:**  
D24 is a v0.1 contracted feature, marked as mandatory. Clients expecting the channel in POST/PUT responses will not find it. This is drift between DECISIONS and code.

**Recommended Path:**  
Two options:
1. **Implement the one hint code** in Phase 3.x (small slice, ~6 tasks from the D24 "Consequences" section).
2. **Supersede D24** with a D-entry deferring the hints channel to Phase 2 (v0.2), explicitly removing it from v0.1 scope.

Align scope with reality. The forward-review correctly flagged this as unimplemented; it remains true.

---

### ADV3: `sanitize_folder_name` Has No Length Cap; `sanitize_filename` Does

**Severity:** ADVISORY (edge case; low impact on UX)  
**File:** `backend/storage/filesystem.py:81–101`

**Issue:**  
`sanitize_filename` caps at 255 bytes (NTFS / most POSIX filesystems). `sanitize_folder_name` has no cap.

A Pydantic `JumpTitle` with `max_length=120` (Unicode characters) can be ~480 bytes in pessimal UTF-8 (emoji-dense). Combined with `[<jump#>] ` prefix, the folder name can exceed 255 bytes on a path component.

**Concrete Failure:**  
User with a long, emoji-heavy title gets an obscure `FileNotFoundError` from `mkdir` instead of a clean 422 validation error.

**Fix:** Add `max_length` parameter to `sanitize_folder_name` mirroring `sanitize_filename`. Byte-cap to 255 or similar. This prevents the mkdir failure and surfaces a clean API error.

**Primary Source:**  
[Microsoft File Naming Rules](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file) — Windows path component limit is 255 chars; POSIX is similar.

---

### ADV4: No Graceful-Shutdown Lockfile-Release Test

**Severity:** ADVISORY (not a bug; test debt)  
**File:** `backend/main.py:118–119` (finally block)

**Issue:**  
The lockfile is released in a `finally` block. `filelock` on POSIX uses `flock(2)`, which releases when the FD closes — which the kernel does when the process dies. So a SIGKILL'd backend leaves a stale `.logbook.lock` file, but it's harmless.

**Why Not a Bug:**  
On POSIX, the kernel automatically releases flock when the process dies. On Windows, `msvcrt.locking` also releases on process death. A stale lockfile is not a practical blocker.

**Recommended:**  
A test asserting that a SIGKILL'd backend leaves the logbook in a recoverable state would document this explicitly. Not urgent, but valuable for future maintainers.

---

### ADV5: Cloud-Sync-Unfriendly SQLite WAL Mode

**Severity:** ADVISORY (deployment concern, not code bug)  
**File:** `backend/storage/index.py:183` (`PRAGMA journal_mode = WAL`)

**Issue:**  
WAL mode uses three files (`index.sqlite`, `index.sqlite-wal`, `index.sqlite-shm`) that must stay in sync. Cloud-sync tools (Dropbox, iCloud, OneDrive) sync files independently; the three can desync.

**Concrete Failure:**  
User puts logbook on Dropbox. Sync tool propagates the main DB, then later (or in parallel) the WAL and SHM files. On the destination machine, SQLite sees a newer WAL and attempts recovery, potentially corrupting the DB.

**Mitigation:**  
D3 makes the index rebuildable, so the worst outcome is a failed open → user runs reindex. No data loss. **However**, the README doesn't document this limitation, and the app gives no warning on startup.

**Recommended:**  
Add a D-entry clarifying the cloud-sync position. Options:
1. Document as unsupported; recommend against cloud sync.
2. Detect known sync-folder paths at startup and warn.
3. Switch to `journal_mode = DELETE` (no WAL; perf trade-off).

**Primary Source:**  
[SQLite WAL §8 — Limitations](https://www.sqlite.org/wal.html)

---

### ADV6: `shutil.move` to Trash Is Not Cross-Filesystem Atomic

**Severity:** ADVISORY (rare edge case; acceptable for v0.1)  
**File:** `backend/storage/trash.py:37`

**Issue:**  
`shutil.move` copies then deletes if destination is on a different filesystem. Copy+delete is not atomic.

**Why Low Impact for v0.1:**  
In normal usage, `.trash/` is a direct child of `logbook_root`, so it's always on the same filesystem. Only unusual setups (bind mounts, LVM spanning, user-configured trash elsewhere) violate this.

**Recommended:**  
Document in D19 (soft delete) as a known limitation. Not worth changing for v0.1.

---

### ADV7: `.DS_Store`, `Thumbs.db` Flagged as Orphans Forever

**Severity:** ADVISORY (UX friction only)  
**File:** `backend/storage/verify.py:58–64`

**Issue:**  
`verify` command reports `.DS_Store` (macOS) and `Thumbs.db` (Windows) as `orphan_file`. Users will learn to ignore these, reducing the value of real orphan detections.

**Fix:**  
Add OS-noise filenames to `_FOLDER_EXCLUDES` set.

---

## Verification Summary

### XXE / Entity Expansion — ✅ Secure

**File:** `backend/xml/validator.py`  
**Checks Performed:**

1. **DOCTYPE Byte-Level Scan (D2):**
   - Regex: `rb"<!DOCTYPE"` (case-insensitive, IGNORECASE flag)
   - Runs *before* lxml sees bytes
   - No bypass vectors found:
     - Encoded `&#x3C;!DOCTYPE` — caught by byte-level check (never HTML-decoded)
     - BOM + leading whitespace — regex anchors to `<`, will match after whitespace
     - CDATA — acknowledged false-positive; acceptable per docstring

2. **lxml Hardened Parser (D2):**
   - `resolve_entities=False` — blocks entity expansion (billion-laughs)
   - `no_network=True` — blocks SYSTEM/PUBLIC external DTD retrieval
   - `load_dtd=False` — DTDs not loaded for entity resolution
   - `huge_tree=False` — keeps attribute/element limits
   - Per OWASP XXE Cheat Sheet, this is the standard pattern.

3. **10 MB Size Cap:**
   - Reasonable for jump XML (typically < 10 KB)
   - Blocks trivial billion-laughs before lxml parse

**Conclusion:** XXE and entity-expansion attacks are structurally impossible. No primary-source CVE applies.

**References:**
- [OWASP XXE Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/XML_External_Entity_Prevention_Cheat_Sheet.html)
- [lxml Security Documentation](https://lxml.de/parsing.html)

---

### Path Traversal — ✅ Secure

**Files:** `backend/storage/filesystem.py:157–170` (`safe_join`), `81–128` (sanitizers)

**Checks Performed:**

1. **`safe_join` Enforcement:**
   - `candidate = root / sanitize_folder_name(part)` — every part sanitized
   - `resolved = candidate.resolve()` — symlinks dereferenced
   - `resolved.is_relative_to(root)` — escape attempt detected and rejected
   - No `..` can survive `sanitize_folder_name` (rejects `.` and `..` by name)

2. **Character Rejection:**
   - `/`, `\`, control characters all rejected
   - Coverage: Windows reserved names (CON, PRN, AUX, NUL, COM1..9, LPT1..9)
   - Trailing space / period rejected (Windows silent-trim defense)

3. **No Null Bytes:**
   - `ord(ch) < 32 or ord(ch) == 0x7F` — catches null byte and other controls

4. **NFC Normalization (D4):**
   - All names normalized to NFC on write — ensures deterministic paths across macOS/Windows/Linux

**Attack Scenario Tested:**
- Input: `["logbook_root", "../../../etc/passwd"]` → `ValueError` at `safe_join`
- Input: `["logbook_root", "con.txt"]` → `ValueError` at `sanitize_folder_name`
- Input: `["logbook_root", "folder\name"]` → `ValueError` (backslash rejected)

**Conclusion:** Path traversal is blocked at multiple layers (character rejection, `..` rejection, `is_relative_to` guard). No bypass found.

**References:**
- [Microsoft File Naming Rules](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file)
- [OWASP Path Traversal](https://owasp.org/www-community/attacks/Path_Traversal)

---

### Lockfile Safety — ✅ Safe (with caveats)

**File:** `backend/storage/lockfile.py`

**Current Code:**
```python
lock = FileLock(str(logbook_root / LOCK_FILENAME), timeout=0.5)
lock.acquire()  # Blocks; raises Timeout if held
```

**Checks Performed:**

1. **POSIX (Linux, macOS):**
   - `filelock` uses `flock(2)` — advisory locks
   - Associated with open FD; released when process dies (including SIGKILL)
   - Stale lock not a practical problem

2. **Windows:**
   - `filelock` uses `msvcrt.locking` — mandatory locks
   - Released on process death
   - Stale lock not a practical problem

3. **NFS / CIFS Caveats:**
   - NFS: flock is emulated as fcntl byte-range locks (OK for this use case)
   - CIFS 5.5+: flock is emulated as **mandatory** locks, not advisory. This is a semantic mismatch but not a blocker for v0.1 (cloud sync already flagged as unsupported for other reasons — WAL mode).

**Conclusion:** Lockfile is safe for local filesystems and v0.1 scope. Cloud-sync deployments are unsupported (separate D-entry needed).

**References:**
- [Linux flock(2) man page](https://man7.org/linux/man-pages/man2/flock.2.html)
- [filelock Python Library](https://py-filelock.readthedocs.io/)

---

### SQLite Hardening — ✅ Config Correct

**File:** `backend/storage/index.py:176–189`

**Checks Performed:**

1. **Connection Flags:**
   - `isolation_level=None` — autocommit; services wrap multi-statement work in explicit transactions ✓
   - `row_factory=sqlite3.Row` — named-tuple API ✓

2. **PRAGMAs Set on Every Open:**
   - `PRAGMA journal_mode = WAL` — write-ahead logging for concurrent reads ✓
   - `PRAGMA synchronous = NORMAL` — fsync on checkpoint, not every commit (safe under WAL) ✓
   - `PRAGMA foreign_keys = ON` — integrity checking ✓

3. **Schema Versioning (D26):**
   - `user_version` tracks schema version
   - Mismatch triggers drop-and-reindex (safe because index is rebuildable)
   - **Staging note:** Slice prescribes `if schema_was_rebuilt, run reindex synchronously`, but `reindex_from_xml` doesn't exist yet, so warnings are logged instead. This is known and scoped to Phase 3.6.

4. **Parameter Binding:**
   - All queries use `?` parameters; no string interpolation for table names except `sqlite_master` queries (safe)

**Known Limitation (ADV5 above):**  
WAL mode is cloud-sync-unfriendly. Documented.

**Conclusion:** SQLite hardening is correct for v0.1. No CVE-level issues.

**References:**
- [SQLite WAL Documentation](https://www.sqlite.org/wal.html)
- [SQLite PRAGMA synchronous](https://www.sqlite.org/pragma.html#pragma_synchronous)

---

### Multipart Upload — ✅ Handler Correct; ⚠️ Dependency Vulnerable

**File:** `backend/api/jumps.py:70–87`, `backend/services/jump_service.py`, `pyproject.toml:23`

**Handler Checks:**

1. **Streaming (D21):**
   - `atomic_write_stream` with 64 KiB chunks — memory bounded ✓
   - Starlette `UploadFile` spools at 1 MiB (framework-level safety) ✓

2. **Filename Sanitization (D4, Q5):**
   - Every upload filename through `sanitize_filename` ✓
   - 255-byte cap enforced ✓
   - `/`, `\`, control chars, Windows reserved names rejected ✓

3. **Duplicate Prevention:**
   - Code checks for duplicate filenames in the multipart body (line 151–177 in `jump_service.py`) ✓

**Request Body Size:**
- No per-request-body cap in Starlette/FastAPI
- D21 explicitly reserves judgment ("no explicit server-enforced upper bound")
- Acceptable for single-user local (filesystem is the limit)

**Dependency Issue:**  
See **CRx1** above. `python-multipart>=0.0.26` required.

**Conclusion:** Handler code is sound. Dependency floor is vulnerable.

**References:**
- [Starlette UploadFile source](https://github.com/encode/starlette/blob/master/starlette/datastructures.py)
- [RFC 7578 — multipart/form-data](https://tools.ietf.org/html/rfc7578)
- [CVE-2024-24762](https://github.com/advisories/GHSA-2jv5-9r88-3w3p)
- [CVE-2024-53981](https://ubuntu.com/security/notices/USN-8027-1)
- [CVE-2026-40347](https://github.com/advisories/GHSA-mj87-hwqh-73pj)

---

### Error Envelope Leakage — ✅ Correct

**Files:** `backend/api/errors.py`, `backend/api/rest.py:100–171`

**Checks Performed:**

1. **Unhandled Exception Handler:**
   - `@app.exception_handler(Exception)` catches all non-ServiceError exceptions
   - Wraps in `InternalServerError` with detail sanitization
   - Response shape: RFC 9457 problem+json ✓

2. **Detail Field Safety:**
   - For non-loopback deployments (future): detail is a generic "Internal Server Error" (no stack trace leak)
   - For loopback-only (v0.1 default): detail includes exception type + message for developer convenience
   - Per comment: "When v0.1 grows beyond loopback (multi-user, remote API), this branch tightens to honor D16's safety concern"

3. **Traceback Visibility:**
   - Full traceback printed to stderr (for desktop launcher terminal visibility)
   - Emitted to structured log (JSON, correlatable by request_id)
   - **Not leaked in HTTP response body** ✓

4. **Media Type:**
   - All error responses: `application/problem+json` ✓
   - No text/plain fallback for unhandled exceptions ✓

**Conclusion:** Error envelope leakage is prevented. D16 contract is honored.

**References:**
- [RFC 9457 Problem Details](https://www.rfc-editor.org/rfc/rfc9457)

---

### Bearer Auth Enforcement — ⚠️ Not Enforced; See MAJ1

---

## Resolved Findings from Prior Review

**A1 (D24 `_hints`):** Confirmed unimplemented; flagged as ADV2 above.

**A2 (Bearer auth):** Confirmed not enforced; flagged as MAJ1 above.

**A3 (macOS fsync):** Confirmed; flagged as MAJ2 above. Fix sketch provided.

**A4 (Parent-dir fsync):** Confirmed; flagged as ADV1 above. Risk is low on modern Linux.

**A5 (python-multipart CVE floor):** **STILL OPEN.** Version floor has not been updated. Flagged as CRx1 (CRITICAL).

**A6 (No body-size cap):** Confirmed. Acceptable per D21; documented in forward-review.

**A7 (update_jump race window):** Confirmed; separate D-entry needed for intra-process concurrency model. Not a blocking issue for v0.1 (uncommon pattern in desktop app).

**A8 (Crash harness coverage):** Confirmed; known test debt for Phase 3.6. Not urgent.

**A9 (sanitize_folder_name length cap):** Confirmed; flagged as ADV3 above.

**A10 (.DS_Store orphans):** Confirmed; flagged as ADV7 above. Cosmetic.

**A11 (WAL on cloud sync):** Confirmed; flagged as ADV5 above. Needs D-entry.

**A12 (shutil.move atomicity):** Confirmed; flagged as ADV6 above. Acceptable limitation.

**A13 (Panic guard):** **FIXED SINCE FORWARD-REVIEW.** Code at `rest.py:100–171` now has `@app.exception_handler(Exception)` catching unhandled exceptions and wrapping them in RFC 9457 problem+json. The forward-review's "CRITICAL D16 catch-all" is now resolved.

**A14 (DOCTYPE CDATA false-positive):** Confirmed; test coverage gap. Worth a pinned test.

**A15 (Graceful-shutdown test):** Confirmed; test debt. Flagged as ADV4.

---

## New Findings (Not in Prior Review)

### NEW1: Bearer Auth Opens `api_key` Config Field As Vestigial

**Issue:** `config.py:75–76` defines `api_key: str | None = None`, but the field is read nowhere in the codebase. A user setting `api_key` in config will have it silently ignored.

**Recommended Path:** Either implement the auth check (closes MAJ1), or remove the field from config and document it as deferred.

---

## Recommendations for v0.1 Ship

**BLOCKING (before GA):**
1. **CRx1:** Bump `python-multipart>=0.0.26` in `pyproject.toml`. (1 line, test with `uv sync`.)

**HIGH PRIORITY (before GA or Phase 2):**
2. **MAJ1:** Create D46 (Bearer auth model). Clarify whether enforcement is v0.1 or deferred. Either remove scheme from OpenAPI or add middleware.
3. **MAJ2:** Add macOS fullfsync wrapping in `atomic_write` + `atomic_write_stream`, or document the trade-off in the docstring.

**MEDIUM PRIORITY (Phase 3.x):**
4. **ADV2:** Either implement D24 `_hints` channel, or supersede D24 with a decision deferring it to v0.2.
5. **ADV3:** Add `max_length` cap to `sanitize_folder_name` (parity with `sanitize_filename`).
6. **ADV5:** Create a D-entry on cloud-sync position (unsupported / warn / switch to DELETE mode).
7. **ADV7:** Add OS-noise filenames to `verify.py`'s `_FOLDER_EXCLUDES`.

**LOW PRIORITY (nice to have):**
8. **ADV1:** Document parent-directory fsync trade-off in `atomic_write` docstring.
9. **ADV4:** Write shutdown/lockfile test.
10. **ADV6:** Document cross-filesystem move limitation in D19.
11. **NEW1:** Clarify or remove `api_key` field in config.

---

## Conclusion

The codebase exhibits **disciplined application of security invariants (D2, D3, D4, D10, D16, D25)** with no exploitable vulnerabilities in current code. One critical dependency issue (`python-multipart` floor) must be fixed before ship. Two major gaps (Bearer auth enforcement, macOS fsync durability) require decision-record work and optional code changes. The remaining findings are advisories backed by primary sources and suitable for Phase 3 or later.

**For v0.1 release:** Fix CRx1 (1 line), create D46 (clarify auth), optionally add macOS fullfsync. All others are nice-to-haves or Phase 3 work.

---

## Citation Index

### CVEs
- [CVE-2024-24762](https://github.com/advisories/GHSA-2jv5-9r88-3w3p) — python-multipart ReDoS
- [CVE-2024-53981](https://nvd.nist.gov/vuln/detail/CVE-2024-53981) — python-multipart Logging DoS
- [CVE-2026-40347](https://github.com/advisories/GHSA-mj87-hwqh-73pj) — python-multipart Preamble/Epilogue DoS

### RFCs
- [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) — Problem Details for HTTP APIs
- [RFC 7578](https://tools.ietf.org/html/rfc7578) — Returning Values from Forms: multipart/form-data
- [RFC 6901](https://www.rfc-editor.org/rfc/rfc6901) — JSON Pointer

### Platform / Filesystem
- [Apple fsync(2) Documentation](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/fsync.2.html)
- [Linux fsync(2) Man Page](https://man7.org/linux/man-pages/man2/fsync.2.html)
- [Linux flock(2) Man Page](https://man7.org/linux/man-pages/man2/flock.2.html)
- [Microsoft File Naming Rules](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file)
- [Windows MoveFileExW API](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexa)

### SQLite
- [SQLite WAL Documentation](https://www.sqlite.org/wal.html)
- [SQLite PRAGMA synchronous](https://www.sqlite.org/pragma.html#pragma_synchronous)
- [SQLite PRAGMA fullfsync](https://www.sqlite.org/pragma.html#pragma_fullfsync)

### Security / OWASP
- [OWASP XXE Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/XML_External_Entity_Prevention_Cheat_Sheet.html)
- [OWASP Path Traversal](https://owasp.org/www-community/attacks/Path_Traversal)

### Libraries / Frameworks
- [lxml Security Documentation](https://lxml.de/parsing.html)
- [Starlette UploadFile Source](https://github.com/encode/starlette/blob/master/starlette/datastructures.py)
- [filelock Python Library](https://py-filelock.readthedocs.io/)

### Project Documents
- DECISIONS.md (v0.1 scope, D2, D3, D4, D7, D10, D16, D19, D20, D23, D25, D26)
- ARCHITECTURE.md
- CLAUDE.md §5 (invariants)
- Forward-review report (2026-04-23)
- Research reports (2026-04-23): multipart, platform, SQLite

---

**Report completed:** 2026-04-28  
**Reviewed against:** lxml 5.x+, Python 3.11+, Starlette 0.37+, filelock 3.13+, SQLite 3.44+  
**Codebase commit:** Latest in skydive-logbook repo
