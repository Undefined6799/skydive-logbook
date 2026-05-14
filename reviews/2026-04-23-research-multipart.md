# Multipart / Attachment Edge Cases Research

**Date:** 2026-04-23
**Scope:** FastAPI multipart/form-data upload handler for jump attachments (POST /api/v1/jumps)
**Context:** Handler streams 64 KiB chunks via `atomic_write_stream`; filenames go through `sanitize_filename`

## Summary for the Reviewer

**What's correct:**
- `sanitize_filename` properly rejects `/`, `\`, control chars, Windows reserved names, trailing dots/spaces — covers RFC 7578 §4.2 forbidden cases and CVE-2026-24486 path-traversal vectors.
- NFC normalization on every write prevents Unicode homoglyph attacks and ensures deterministic folder names across platforms.
- Streaming via `atomic_write_stream` with 64 KiB chunks bounds memory regardless of file size — satisfies D21's no-cap requirement.
- Duplicate filename rejection pre-write prevents silent overwrites and surfaces clean 422 errors.

**What's surprising:**
- Starlette's `UploadFile` defaults to 1 MiB spool threshold (per github.com/encode/starlette), then spills to disk. At 64 KiB chunks, this is safe for the handler but not documented in the codebase. The comment on line 54 is reassuring but imprecise (just says "default 1 MiB spool").
- RFC 7578 explicitly forbids RFC 5987 `filename*=UTF-8''...` parameter in multipart, but real browsers and `python-multipart` parse it anyway. This codebase ignores it (passes `upload.filename` as-is), which works fine for the use case but represents silent non-compliance with the strict RFC.
- Request-body size is unbounded by Starlette/FastAPI defaults. `uvicorn` + reverse proxy (NGINX) + filesystem limits are the actual DoS boundary, not the handler.

**Real CVE risks:**
- **CVE-2024-24762 (ReDoS):** Malicious Content-Type header causes regex stall in `python-multipart`. Affects all multipart parsing, not just filenames. Mitigated by updating to `python-multipart >= 0.0.18` (requires checking `pyproject.toml`).
- **CVE-2024-53981 (Logging DoS):** Large preamble/epilogue sections cause excessive logging and CPU burn. Affects parsing before filename is even read. Mitigated by `python-multipart >= 0.0.25` (per ubuntu.com/security/notices/USN-8027-1).
- **CVE-2026-40347 (Preamble/Epilogue DoS):** Even with logging fixed, parsing stalls on very large preamble/epilogue. Requires `python-multipart >= 0.0.26`.
- **CVE-2026-24486 (Path Traversal):** Only triggered if `UPLOAD_DIR` and `UPLOAD_KEEP_FILENAME=True` are configured in `python-multipart`. This codebase doesn't use those configs, so it's not exposed—but worth documenting that `python-multipart >= 0.0.22` blocks the underlying bug.

**What matters for this codebase:**
- Content-Type is stored as-sent, no server-side sniffing. Implications are minimal (single user, local-first per D14), but when attachments are served back (future GET endpoint), ensure `X-Content-Type-Options: nosniff` to block browser-side MIME sniffing attacks.
- `.DS_Store`, `Thumbs.db`, and other OS metadata files are accepted as valid attachments. Not a security issue (single user), but worth noting for UX—the logbook will show them in attachment lists.

---

## 1. Starlette UploadFile Semantics

### SpooledTemporaryFile Default Spool Size

**Finding:** Starlette's `UploadFile` wraps `SpooledTemporaryFile(max_size=1024*1024)`, defaulting to a 1 MiB in-memory threshold before spilling to disk.

**Source & Details:**
- Per [github.com/encode/starlette](https://github.com/encode/starlette/blob/master/starlette/datastructures.py), the `UploadFile` class wraps a `SpooledTemporaryFile`. The `max_size` parameter controls when the spool overflows to temporary disk storage.
- The code comment on line 54 of `jumps.py` correctly notes this: *"UploadFile wraps a SpooledTemporaryFile (default 1 MiB spool before spill — see starlette.datastructures.UploadFile)"*.

**Implication for this codebase:**
- The handler reads via `upload.file.read(_UPLOAD_CHUNK_SIZE)` in a sync context (matching the sync route). Files under 1 MiB stay in RAM during the `_upload_chunks` generator; larger files are automatically promoted to disk by the framework.
- At 64 KiB chunks, the iterator is fine—reads are already bounded by the framework's spool.

### Sync `.file.read()` vs. Async `await upload.read()`

**Finding:** The route is sync (`def create_jump_route`), and the handler correctly uses `upload.file.read()` (the synchronous SpooledTemporaryFile API) rather than the async method.

**Details:**
- `UploadFile` exposes two read paths: synchronous `.file.read(size)` and asynchronous `await .read(size)`.
- Using `.file.read()` in a sync route avoids the overhead of switching to the async runtime for disk I/O—the right call here.

### Cleanup and Lifecycle

**Finding:** `SpooledTemporaryFile` objects are garbage-collected after the request completes. The temporary file (if spilled to disk) is automatically cleaned up on GC.

---

## 2. Request Size Limits & Streaming

### Starlette/FastAPI Defaults

**Finding:** Starlette's `request.form()` method has three limits:
- `max_files`: 1000
- `max_fields`: 1000  
- `max_part_size`: 1024 × 1024 (1 MiB per part)

**Source:** [FastAPI/Starlette discussion #12961](https://github.com/fastapi/fastapi/discussions/12961), [FastAPI/Starlette discussion #12943](https://github.com/fastapi/fastapi/discussions/12943)

**Critical:** There is **no built-in maximum request body size** in Starlette. The limit is `max_part_size` per individual part (file or form field), not the total body.

### DoS Surface

**Attack vector:** A client can send a 500 GB multipart request with:
- Each part under 1 MiB (bypassing `max_part_size`)
- Fewer than 1000 files (bypassing `max_files`)
- But a huge number of 1 KiB parts ("epilogue bloat" attack)

**Layer-by-layer response:**
1. **Uvicorn** (ASGI server): No built-in limit; will buffer until the part is complete or the connection closes.
2. **Starlette/python-multipart**: No per-request-body limit; will parse parts as they arrive.
3. **Handler (`atomic_write_stream`)**: Streams to disk, so memory is bounded. But disk can fill.
4. **Filesystem**: Eventually runs out of space.

**Knobs to consider:**
- Set `max_part_size` explicitly in a middleware or in the form parsing call (this codebase doesn't override it).
- Use NGINX's `client_max_body_size` or equivalent reverse-proxy setting (deployment concern, not app code).
- Monitor disk space and reject uploads when free space drops below a threshold (future feature).

### python-multipart Defaults

**Finding:** `python-multipart` has no built-in memory-per-part limits beyond what Starlette enforces. The `max_part_size` parameter is Starlette's knob, not `python-multipart`'s.

---

## 3. RFC 7578 / RFC 6266 Edge Cases

### Filename Encoding: RFC 7578 §4.2, RFC 5987

**RFC 7578 Position:** Section 4.2 ("Using the multipart Media Type for Form Data") specifies the `filename` parameter in `Content-Disposition`. RFC 7578 **explicitly forbids** the RFC 5987 `filename*=UTF-8''...` syntax in multipart contexts—it is reserved for HTTP headers only (like `Content-Disposition: attachment`).

**Real-world behavior:** Browsers, curl, and `python-multipart` all ignore the RFC restriction and parse `filename*` anyway. The parameter is percent-encoded; example: `filename*=UTF-8''M%C3%BCller.pdf` decodes to `Müller.pdf`.

**Codebase handling:** This code passes `upload.filename` directly from Starlette. Starlette reads the `filename` parameter (RFC-compliant) but does not parse `filename*`. If a client sends only `filename*`, `upload.filename` will be `None`, and the handler passes `""` to `sanitize_filename`, which rejects it as invalid. This is correct behavior—either upload without non-ASCII (use `filename`), or ensure your HTTP library encodes it in the ASCII `filename` parameter per RFC 7578.

### Filename Edge Cases: Quotes, Backslashes, Null Bytes

**RFC 7578 guidance:** Parameter values can be quoted; quotes and backslashes inside must be escaped with backslash. Real parsers vary in strictness.

**This codebase:**
- Starlette's parser (via `python-multipart`) handles escaping.
- `sanitize_filename` then rejects `\` as a forbidden character (line 24 in `filesystem.py`).
- Control characters (including null bytes, ord < 32 or ord == 0x7F) are rejected on line 57–58.

**Result:** Safe. Null bytes and path-traversal backslashes cannot reach the filesystem.

### Part With No Filename

**RFC 7578:** A part without the `filename` parameter is a regular form field, not a file. This is legal and common.

**Codebase:**
- The `jump` field (containing JSON) has no `filename` parameter—it's a form field.
- The `files` list is defined as `list[UploadFile] | None`.
- If Starlette cannot parse a file part (missing filename), it raises an error during form parsing, before the handler runs. This is correct.

### Duplicate Part Names

**RFC 7578:** Multiple parts with the same name are allowed (common for multi-file upload). Multiple parts with the same name as metadata (like two `jump` fields) are not tested by this code.

**Codebase:**
- The route expects exactly one `jump` field. If the client sends two, Starlette's form parser will likely keep the last one (framework behavior varies; not guaranteed by RFC). This is a minor spec gap but not a security issue—the Pydantic validation will reject invalid JSON.
- Multiple `files` parts are handled correctly and de-duplicated pre-write (line 151–177 in `jump_service.py`).

### Zero-Length Files

**RFC 7578:** Legal. Some clients may send empty attachments.

**Codebase:**
- A zero-length file reaches `atomic_write_stream`, which writes nothing to disk and computes a valid SHA-256 hash of empty bytes.
- The attachment is created with `size=0`, which is correct and queryable.

### Missing or Wrong Content-Type

**RFC 7578:** The `Content-Type` of a part is optional; if absent, the receiver may assume `application/octet-stream`.

**Codebase:**
- The handler stores whatever Content-Type the client sends (line 161: `content_type=f.content_type`).
- If `content_type` is `None`, the `Attachment` model stores `None`, and serialization to XML uses an empty `<content_type/>` (or omits it, depending on the schema).
- No validation is done on the MIME type value. The string is stored as-sent.

---

## 4. Known CVEs (Last 3 Years)

### CVE-2024-24762 — ReDoS in Content-Type Header

**Severity:** High (CPU exhaustion, service stall).

**Details:** A specially crafted `Content-Type` header in a multipart part causes the `python-multipart` regex engine to stall for minutes. Affects all multipart parsing, not just file uploads.

**Fix:** Upgrade to `python-multipart >= 0.0.18`.

**Source:** [CVE-2024-24762](https://www.sentinelone.com/vulnerability-database/cve-2024-24762/), [PulsePatch analysis](https://pulsepatch.io/posts/cve-2024-24762-python-multipart-redos/)

### CVE-2024-53981 — Logging DoS via Preamble/Epilogue

**Severity:** High (CPU exhaustion, service stall).

**Details:** Large preamble (data before the first boundary) or epilogue (data after the last boundary) sections cause `python-multipart` to emit log messages for every byte skipped, exhausting CPU and I/O.

**Fix:** Upgrade to `python-multipart >= 0.0.25`.

**Source:** [CVE-2024-53981](https://www.sentinelone.com/vulnerability-database/cve-2024-53981/), [Ubuntu security notice USN-8027-1](https://ubuntu.com/security/notices/USN-8027-1), [Fedora bug #2330007](https://bugzilla.redhat.com/show_bug.cgi?id=2330007)

### CVE-2026-40347 — DoS via Large Preamble/Epilogue (Continued)

**Severity:** Medium (parsing stall even after logging fix).

**Details:** Even with logging fixed (CVE-2024-53981), the parsing loop itself stalls on very large preamble/epilogue because it skips one byte at a time without yielding control.

**Fix:** Upgrade to `python-multipart >= 0.0.26`.

**Source:** [CVE-2026-40347 (Advisory)](https://advisories.gitlab.com/pypi/python-multipart/CVE-2026-40347/), [THREATINT listing](https://cve.threatint.eu/CVE/CVE-2026-40347)

### CVE-2026-24486 — Path Traversal (Configuration-Specific)

**Severity:** High (file write to arbitrary paths), but **not exposed in this codebase**.

**Details:** If `python-multipart` is configured with custom `UPLOAD_DIR` and `UPLOAD_KEEP_FILENAME=True` settings, directory traversal sequences (e.g., `../../../etc/passwd`) in filenames bypass sanitization.

**Exposure:** This codebase does NOT use those configuration options. Starlette's built-in form parsing does not expose them. The handler then calls `sanitize_filename`, which rejects `/` and `\`, making double-checks redundant.

**Fix (if using custom multipart config):** Upgrade to `python-multipart >= 0.0.22`.

**Source:** [CVE-2026-24486](https://www.sentinelone.com/vulnerability-database/cve-2026-24486/)

### Recommendation

Check `pyproject.toml` for the current pinned version of `python-multipart`. If `< 0.0.26`, upgrade to `>= 0.0.26` to pull in all three DoS fixes.

---

## 5. Cross-Platform Filename Concerns

### Path Traversal

**Handled:** `sanitize_filename` rejects `/` and `\` (line 24), blocking `../`, `..\\`, and other traversal attempts.

### Windows Reserved Names

**Handled:** `_reject_windows_reserved` (line 61–78 in `filesystem.py`) rejects `CON`, `PRN`, `AUX`, `NUL`, and `COM1..9`, `LPT1..9` case-insensitively, even with extensions (e.g., `CON.txt` is invalid).

### Trailing Space / Period

**Handled:** Rejected on line 72–75. Windows silently trims these, breaking round-trip.

### Unicode Homoglyphs and RTL

**Not mitigated:** Characters like Cyrillic `А` (U+0410) look identical to Latin `A` (U+0041). RTL markers (U+200F) can reorder filename display.

**Assessment:** Single-user app, low risk. If multi-user, consider flagging filenames with non-Latin scripts or RTL markers during upload. Not a blocker for v0.1.

### Zero-Width Characters

**Not stripped:** NFC normalization preserves zero-width joiner (U+200D), zero-width non-joiner (U+200C), and BOM (U+FEFF). These may appear in filenames and be invisible to users.

**Assessment:** Cosmetic, not security. NFC normalization ensures consistency, which is the main goal (D4).

### BOM as First Character

**Not stripped:** A filename starting with U+FEFF (BOM) is stored as-is. Some text editors treat BOM as a file-encoding marker, but the filesystem stores it as part of the name.

**Assessment:** Rare and cosmetic. The filesystem doesn't interpret it.

### OS Metadata Files

**Not rejected:** `.DS_Store` (macOS), `Thumbs.db` (Windows), `desktop.ini` (Windows) pass `sanitize_filename` and are stored as attachments.

**Assessment:** Harmless for a single-user app. For multi-user, you might want to filter them out, but v0.1 scope is single-user (D8, D14).

---

## 6. Content-Type Handling

### No Server-Side Sniffing

**Current behavior:** The handler stores `upload.content_type` as-sent by the client. No MIME magic (libmagic) or content inspection is done.

**Implications:**
- A client can upload a `.exe` claiming `image/png`.
- A client can upload HTML claiming `application/octet-stream`.
- The attachment is stored with whatever type the client declared.

**Risk assessment:** **Low for v0.1** (single user, local-first, no untrusted consumers). The user is uploading to their own logbook.

### When Attachments Are Served Back

**Future concern:** When `GET /api/v1/jumps/{id}/attachments/{filename}` ships, the server will return the stored `content_type` in the response. Browsers will interpret the `Content-Type` header.

**Mitigation (when serving attachments):**
1. Add `X-Content-Type-Options: nosniff` header to all attachment responses. This blocks browsers from MIME sniffing the content and executing scripts.
2. Consider serving attachments from a separate domain or subdomain to isolate them from the main app's cookies and session storage.

**Sources:** [Coalfire: MIME Sniffing and Security](https://coalfire.com/the-coalfire-blog/mime-sniffing-in-browsers-and-the-security), [MDN: X-Content-Type-Options](https://httpsecurity-headers.com/X-Content-Type-Options)

### Dangerous MIME Types to Flag (Optional, Not v0.1)

- `application/x-msdownload` (`.exe`)
- `application/x-ms-installer` (`.msi`)
- `application/x-shellscript` (`.sh`)
- `application/x-sh` (shell script)

These are not blocked today, but a future UI could warn the user or reject them. Not required for v0.1.

---

## Citations

1. [RFC 7578 — Returning Values from Forms: multipart/form-data](https://tools.ietf.org/html/rfc7578)
2. [RFC 6266 — Use of the Content-Disposition Header Field in HTTP](https://tools.ietf.org/html/rfc6266)
3. [RFC 5987 — Encoding Filename and Creating Links in HTTP Header Fields](https://tools.ietf.org/html/rfc5987)
4. [Starlette Datastructures — UploadFile](https://github.com/encode/starlette/blob/master/starlette/datastructures.py)
5. [Starlette Form Parsers](https://github.com/encode/starlette/blob/master/starlette/formparsers.py)
6. [CVE-2024-24762 — python-multipart ReDoS](https://www.sentinelone.com/vulnerability-database/cve-2024-24762/)
7. [CVE-2024-53981 — python-multipart Logging DoS](https://www.sentinelone.com/vulnerability-database/cve-2024-53981/)
8. [CVE-2026-40347 — python-multipart Preamble/Epilogue DoS](https://advisories.gitlab.com/pypi/python-multipart/CVE-2026-40347/)
9. [CVE-2026-24486 — python-multipart Path Traversal](https://www.sentinelone.com/vulnerability-database/cve-2026-24486/)
10. [Ubuntu Security Notice USN-8027-1 — python-multipart Vulnerabilities](https://ubuntu.com/security/notices/USN-8027-1)
11. [FastAPI Discussion #12961 — Setting Starlette's max part size](https://github.com/fastapi/fastapi/discussions/12961)
12. [FastAPI Discussion #12943 — Modifying upper limits for max_part_size](https://github.com/fastapi/fastapi/discussions/12943)
13. [Coalfire — MIME Sniffing and Security Implications](https://coalfire.com/the-coalfire-blog/mime-sniffing-in-browsers-and-the-security)
14. [OWASP — Path Traversal](https://owasp.org/www-community/attacks/Path_Traversal)
15. [PortSwigger Web Security Academy — Path Traversal with Null Byte Bypass](https://portswigger.net/web-security/file-path-traversal/lab-validate-file-extension-null-byte-bypass)
