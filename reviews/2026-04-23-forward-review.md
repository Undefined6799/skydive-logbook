# Forward-Looking Review — 2026-04-23

**Scope.** A re-examination of the backend, informed by (1) first-hand reading
of every module touched by the request/response and storage paths, (2) three
research subagents whose sources I verified independently, and (3) targeted
cross-checks of the four initial axis reports. The aim is not to re-produce
a defect list — the earlier pass did that well — but to surface things the
earlier reports missed or misweighted, and to name the blind spots where the
code is fine today but is aimed at a future the record hasn't named yet.

The bar applied to every item below is: **is this a real concern with a
primary-source citation, or is it a nitpick?** Nitpicks were dropped.

---

## How this review differs from the earlier pass

The earlier four-agent pass was disciplined and caught the high-impact items
(D16 catch-all, D26 reindex code-lag, OpenAPI `responses=` wiring, D3
reindex test gap). It did not:

1. Audit every D-entry consequence — D24 was missed and is a real drift.
2. Go to primary sources on platform portability — `fsync` durability on
   Darwin and `flock` mandatory-mode on CIFS 5.5+ deserve explicit notice.
3. Verify dependency-version floors against current CVEs — one of the
   three `python-multipart` CVEs is from April 2026 and the `>=0.0.9`
   floor in `pyproject.toml` admits a vulnerable resolve.
4. Revisit the severity of its own findings after re-examination.

The four earlier reports remain valid. This one builds on top of them.

---

## Executive summary

The codebase is in a strong position. Every load-bearing invariant (D2, D3,
D4, D10, D16, D25) has evidence of discipline in the code; the crash
harness in `_crash_child.py` is particularly well-constructed and deserves
to be emulated for `update_jump`. The remaining gaps are mostly forward-
looking: either the DECISIONS record got ahead of the code (D24, D26), or
the code is fine for single-user-local-first-today but will need thought
for the next scope layer (cloud-synced folders, pywebview auth, multi-user).
Nothing below calls for immediate code change; everything below either
needs a D-entry, a README note, or a future-phase slice.

---

## Section A — Genuine blind spots (things not considered yet in code or DECISIONS)

### A1. D24 `_hints` channel is completely unimplemented (new drift)

**Evidence.** `DECISIONS.md:892–1016` prescribes a `_hints: list[Hint]`
channel on write responses with one required v0.1 code:
`non_sequential_jump_number`. The D-entry explicitly names the
implementation points: `Pydantic response models wrap the resource in a
thin subclass that adds _hints: list[Hint] = []`, `backend/api/errors.py
gains a build_hint helper`, `OpenAPI schema adds a Hint component`, `one
test per hint code`.

Grep across `backend/` for `_hints`, `Hint`, `non_sequential_jump_number`,
`build_hint` returns **zero matches**. `backend/api/openapi.py` has no
`Hint` component. `backend/services/jump_service.py` has no
non-sequentiality check and no `_hints` field on the response. Tests have
no hint coverage.

**Why it matters.** This is drift the earlier DECISIONS-drift agent
missed. It's not load-bearing — clients are free to ignore `_hints` and
nothing breaks — but it's a documented v0.1 contract item that's absent.
Two ways forward: (a) a small phase that implements the one v0.1 hint
code, or (b) supersede D24 with a D-entry explicitly deferring it.

**Status.** Undocumented gap. Deserves an explicit decision.

---

### A2. Bearer auth scheme is advertised but not enforced

**Evidence.** `backend/api/openapi.py:117–124` declares a `bearerAuth`
security scheme with the description *"Required only when the server
binds to a non-loopback address. Loopback (127.0.0.1) connections skip
authentication."* `backend/config.py:76` defines `api_key: str | None =
None`. `backend/main.py` binds uvicorn to `settings.bind_host` without
checking whether that host is loopback. `backend/api/rest.py` has no
auth middleware. `backend/api/deps.py:46–53` returns `"default"` from
`get_user_id()` without reading any request header.

**Concrete failure mode.** A user sets `bind_host = "0.0.0.0"` in their
`config.toml`, opens the port, and the app serves every route to the
LAN without asking for a token — regardless of whether `api_key` is set.
The OpenAPI document says otherwise.

**Why it matters.** v0.1 is local-first (CLAUDE.md §10) and the default
is loopback-only, so no one is at risk today. But the surface area is
real the moment a user changes one line in TOML. Either (a) the spec
should not describe an auth scheme that isn't wired, or (b) a middleware
should reject non-loopback requests without the right bearer. There is
no D-entry governing *when* the bearer check is enforced — the
description in `openapi.py` reads like an enforcement commitment that
the code doesn't deliver.

**Status.** A new D-entry ("Bearer auth is enforced iff bind_host ≠
loopback; documented here; implementation deferred to Phase X") would be
the cleanest way to close the gap. The alternative is to drop the
security scheme from OpenAPI until the middleware lands, so the spec
stops promising something that isn't true.

---

### A3. `os.fsync` on macOS flushes to cache, not to platter

**Evidence.** `backend/storage/filesystem.py:173–210` uses
`os.fsync(f.fileno())` before `os.replace`. On Darwin, CPython's
`os.fsync` maps to BSD `fsync(2)`. Apple documents that `fsync(2)` on
Darwin "does not cause the drive to flush its internal buffer to the
disk platter"; true crash durability on APFS requires
`fcntl(fd, F_FULLFSYNC)`. SQLite exposes this as its `fullfsync` pragma
and flips it on for Darwin by default.
([fsync(2) – Apple](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/fsync.2.html),
[SQLite WAL & syncing](https://www.sqlite.org/pragma.html#pragma_fullfsync))

**Concrete failure mode.** A kernel panic or hard power loss after
`atomic_write` returns but before the drive completes its internal
flush could lose the write — the bytes were never actually on the
platter. Likelihood on a modern Mac with a healthy SSD is very low.
Likelihood on an external USB drive or a bus-powered portable is
higher.

**Why it matters.** D3 makes the index rebuildable, so the *index* is
safe. But `atomic_write` is also the write path for `jump.xml` and
every attachment-claim manifest — the source-of-truth data. The
comment on `atomic_write` mentions the MoveFileExW cross-volume
caveat for Windows; it doesn't mention the Darwin fullsync gap.

**Status.** Not a bug. It *is* a subtle property that the project's
"data must outlive the app" framing would want to know about. A
paragraph in the `atomic_write` docstring, or a D-entry acknowledging
the trade-off, would land it in the record.

---

### A4. Parent-directory fsync missing after `os.replace`

**Evidence.** `atomic_write` does `fsync(file) → os.replace(tmp,
path)`. POSIX guarantees `rename(2)` is atomic on the same filesystem,
but the rename's *durability* is only guaranteed after an `fsync` on
the parent directory. ext4, btrfs, XFS, and APFS all ship with enough
ordering that the rename survives a crash in practice, but the POSIX
contract doesn't require it. This is the exact gap that bit Firefox
("0-byte file after crash") and is documented in the LWN coverage of
ext4 delayed-allocation and Chromium's crashsafe write-up.
([LWN: ext4 and data loss](https://lwn.net/Articles/322823/))

Windows (NTFS via MoveFileExW) does not need parent-directory fsync —
different durability model.

**Concrete failure mode.** A kernel panic between `os.replace`
returning and the kernel flushing the parent directory inode could in
theory leave the directory entry un-updated on next boot — the new
bytes exist as an orphaned tmp file, the old file's directory entry
remains. Modern ext4 with `data=ordered` makes this essentially
impossible in practice, but it's not a POSIX guarantee.

**Why it matters.** Same answer as A3: D3 rebuildability protects the
index; the XML source-of-truth writes rely on the same routine. The
difference from A3 is that A3 is a real-world risk on external drives,
while A4 is mostly pedantic on modern Linux but would matter if the
logbook ever lives on a less-common filesystem.

**Status.** Not a bug. Worth a paragraph in the `atomic_write`
docstring naming the trade-off.

---

### A5. `python-multipart` floor in `pyproject.toml` admits vulnerable versions

**Evidence.** `pyproject.toml:23` declares `python-multipart>=0.0.9`.
`uv.lock` resolves to `0.0.26` today, which is not affected. Three
published CVEs affect older versions:

- [CVE-2024-24762](https://github.com/advisories/GHSA-2jv5-9r88-3w3p):
  Content-Type header ReDoS, fixed in 0.0.7.
- [CVE-2024-53981](https://nvd.nist.gov/vuln/detail/CVE-2024-53981):
  Boundary-parsing DoS via excessive logging, fixed in 0.0.18.
- [CVE-2026-40347](https://github.com/advisories/GHSA-mj87-hwqh-73pj):
  Preamble/epilogue parsing stall, fixed in 0.0.26 (April 2026).

**Concrete failure mode.** A developer running `uv sync --upgrade` on
a new machine without an existing lockfile, or a future CI job that
regenerates the lockfile with different resolver preferences, could
land on a version between `0.0.9` and `0.0.17` that is vulnerable to
CVE-2024-24762 + CVE-2024-53981. Small blast radius, real surface
area.

**Why it matters.** The lockfile is currently safe; the version floor
is a statement about what combinations the project *accepts*.
Tightening the floor to `>=0.0.26` matches current reality and
eliminates the stale-resolve path.

**Status.** Advisory. One line in `pyproject.toml`.

---

### A6. No total-request-body size limit

**Evidence.** Starlette's `max_part_size` defaults to 1 MiB (per-part,
for form fields; `UploadFile` is separate). `max_files` defaults to
1000, `max_fields` to 1000. There is no `max_request_body_size` — the
whole body can be arbitrarily large. uvicorn does not enforce one
either.

D21 is explicit: *"Attachment uploads have no explicit server-enforced
upper bound on file size in v1."* The decision is deliberate for
single-user-local.

**Why it mentions as a blind spot, not a finding.** D21 is the
authoritative decision for attachment size; fine. But D21 is about
*attachment* size, not *request* size. A malicious (or buggy) client
could send a multipart body with 1000 parts × 1 GB each — 1 TB total
— and the server would happily stream every byte to disk, potentially
filling the filesystem before the `create_jump` error surfaces. The
atomic_write_stream cleanup is per-attachment; earlier attachments
that streamed successfully are *already on disk* when a later one
fills the disk.

For single-user local, "the owner of the disk did it to themselves" is
D21's answer. For a future LAN-exposed deployment, or if a bug in the
future frontend ever does the wrong thing, this is a usability failure
(gigabytes of partially-written orphan attachments). No D-entry
commits to a position here.

**Status.** Deferred-by-omission. Worth naming in D21 or in a
successor: "request body has no cap either" — so the position is
explicit instead of inferred.

---

### A7. update_jump has a rename/index race window

**Evidence.** `backend/services/jump_service.py:617–673` (update_jump
steps 6–9):

1. Step 6: `atomic_write(current_folder / JUMP_XML_NAME, ...)`
2. Step 7: `atomic_write(current_folder / MANIFEST_NAME, ...)`
3. Step 8: `os.rename(current_folder, new_folder)` (only if name changed)
4. Step 9: `UPDATE jumps SET folder = ?, ... WHERE id = ?`

Between steps 8 and 9, a concurrent `get_jump` opens its own SQLite
connection, reads `folder` (still the *old* path), and tries to read
`<old_folder>/jump.xml`. The folder no longer exists at the old path —
it was just renamed. The reader sees `FileNotFoundError` bubbling from
`folder_reconcile`.

FastAPI sync handlers run on a threadpool (Starlette's
`run_in_threadpool`, default 40 concurrent threads). The process-level
lockfile serialises *processes*, not *threads within a process*. So
two concurrent requests to the same process share this window.

**Concrete failure mode.** User A clicks "rename jump" in the UI; user
A's concurrent tab is polling list/get; the polling request falls
into the window and returns a 500 (IntegrityError from
`folder_reconcile`) or a 404 before the next polling tick.

**Why it matters for v0.1.** For a single-user desktop app where
concurrent-request patterns are unusual, probably never hits. For a
pywebview frontend that polls while the user edits, probably hits
sometimes. No D-entry covers intra-process concurrency; D9 only covers
inter-process.

**Status.** A future D-entry on the intra-process concurrency model
would close this. The fix (if taken) would probably be an in-process
writer lock around multi-step mutations, or reordering so the index
update precedes the rename.

---

### A8. Crash harness covers `create_jump` only

**Evidence.** `_crash_child.py` exposes three crash points
(`after_mkdir`, `after_first_attachment`, `after_jump_xml`) — all
inside `create_jump`. `update_jump` is a 9-step write ordering with at
least three multi-file-write boundaries (jump.xml write, SHA256SUMS
write, folder rename, index update) where an interrupt could leave
state mid-transition. `delete_jump` is simpler (trash move, index
delete) but still two-step.

**Why it matters.** CLAUDE.md §7 explicitly says "write a test for the
half-written case whenever you add a multi-file write." The earlier
Test-quality agent noted `test_crash_recovery.py` is outstanding; it
is — *for `create_jump`.* Extension to `update_jump` and `delete_jump`
is a natural next phase for the harness.

**Status.** Test debt that follows Phase 3.5's work, not a new
finding against current code.

---

### A9. `sanitize_folder_name` has no length cap; `sanitize_filename` does

**Evidence.** `backend/storage/filesystem.py:81–101` defines
`sanitize_folder_name` with no max-length. `sanitize_filename` caps at
`_MAX_FILENAME_LEN = 255`. A title-generated folder name can exceed 255
bytes — the Pydantic `JumpTitle` caps title at 120 *Unicode
characters*, which is ~480 bytes in pessimal UTF-8 (emoji-dense), far
over 255. That plus the `[<jump#>] ` prefix could exceed 255 bytes on
a path component and fail at `mkdir` time with a platform-specific
error.

**Concrete failure mode.** A user with a legitimately long,
emoji-heavy title gets an obscure `OSError` from `jump_folder.mkdir`
instead of a clean 422 from the API. The JumpTitle max_length=120 is
measured in Python `len()` (Unicode chars), not bytes.

**Why it matters.** Low impact. Most users don't hit this. For
correctness and parity with `sanitize_filename`,
`sanitize_folder_name` could accept a byte-length cap.

**Status.** Minor blind spot. Not urgent.

---

### A10. `.DS_Store` / `Thumbs.db` / `desktop.ini` will be flagged as orphans forever

**Evidence.** `backend/storage/verify.py:58–64` defines
`_FOLDER_EXCLUDES = {jump.xml, SHA256SUMS, SHA256SUMS.tmp, summary.md,
summary.md.tmp}`. macOS Finder, Windows Explorer, and some file
managers automatically create these alongside user-opened folders.
Every time `verify` runs after the user has browsed the logbook
visually, it will report them as `orphan_file`.

**Why it matters.** UX friction, not correctness. `verify` noise
teaches the user to ignore `orphan_file` findings, which reduces the
value of real ones. A set of OS-noise filenames in `_FOLDER_EXCLUDES`
closes this.

**Status.** Future improvement. Not in any D-entry; worth a
one-liner.

---

### A11. Cloud-sync-hostile SQLite WAL

**Evidence.** `backend/storage/index.py:147` sets `PRAGMA journal_mode
= WAL`. WAL mode uses three files: `index.sqlite`, `index.sqlite-wal`,
`index.sqlite-shm`. SQLite documents that WAL is not safe on
network filesystems or cloud-sync folders because the three files
must be updated atomically and most sync tools don't guarantee that.
([SQLite WAL §8](https://www.sqlite.org/wal.html))

**Concrete failure mode.** A user puts their logbook on iCloud /
Dropbox / OneDrive for backup and laptop-switching. The sync tool
propagates the three files independently; on the destination machine,
`sqlite3.connect` sees a WAL file newer than the DB and triggers
recovery that may corrupt the DB. Since D3 makes the index
rebuildable, the worst observable outcome is a failed open → `main.py`
exits with a friendly error → user runs reindex (once reindex exists).

**Why it matters.** Cloud-backed logbooks are a plausible deployment
pattern for a self-hosted, laptop-resident, one-user tool — exactly
the profile of this app. The README doesn't discourage it; nothing
in D-entries names the position. D3 saves the data, but the user
experience is confusing.

**Status.** Deserves a deployment-level note. Options: (a) document
as unsupported in README, (b) detect known sync-folder paths and warn
on startup, (c) switch the index to `journal_mode = DELETE` (no WAL)
and accept the perf hit (index is single-process anyway). Each is
scoped by a future D-entry.

---

### A12. `shutil.move` to `.trash/` is not atomic across filesystems

**Evidence.** `backend/storage/trash.py:37` uses `shutil.move`. Python
docs: *"If the destination is on a different filesystem, the function
copies and then deletes the source."* Copy+delete is not atomic — a
crash mid-copy leaves both folders partially present.

**Why it matters.** In normal usage, `.trash/` is a direct child of
`logbook_root` so it's always on the same filesystem. Unusual setups
(bind mounts, LVM spanning, a user-configured trash elsewhere)
violate that assumption. D19 commits to soft-delete; it doesn't
commit to cross-filesystem atomicity. Worth naming in D19 as a known
limitation.

**Status.** Blind spot documented by omission.

---

### A13. Middleware stack has only correlation; no panic guard

**Evidence.** `backend/api/rest.py:43` adds only
`CorrelationIdMiddleware`. The earlier Invariants agent's CRITICAL
finding (only `ServiceError` has a handler) is the consequence: any
exception that isn't a `ServiceError` subclass escapes past the
handler and FastAPI returns its default `PlainTextResponse("Internal
Server Error", 500)` — which is text/plain, not
`application/problem+json`, so D16's contract is breached.

I spot-verified this directly against the code. Downstream, the
correlation middleware catches the finally block (line 268) and logs
`http_request` with status=500 (the default in `status_box`), so at
least the observability side records the failure — but the wire shape
is wrong.

**Revision from earlier pass.** The earlier agent called this
CRITICAL. I agree the invariant breach is real. Severity is bounded
by two facts the earlier agent didn't foreground:

1. Almost no code path in the service layer raises a non-ServiceError
   exception. Every business-logic error is typed. The only paths
   that currently raise a bare exception are: (a) a Pydantic bug,
   (b) a filesystem error during atomic_write that the service
   didn't wrap, (c) a developer-introduced bug.
2. The loopback-only default means only the developer themselves
   sees the leaked 500 during local testing.

So "critical by invariant" but not "immediately exploitable." Still
worth closing.

**Status.** Earlier finding stands, with the nuance above.

---

### A14. DOCTYPE pre-parse scan has a known CDATA false-positive

**Evidence.** `backend/xml/validator.py:39` defines a byte-level
regex `rb"<!DOCTYPE"` applied before lxml parse. The validator
docstring (lines 33–38) acknowledges *"Theoretical false positive:
literal `<!DOCTYPE` inside a CDATA section. Not a real shape in this
project — jump notes never contain that string."*

**Concrete failure mode.** If a user's `<notes>` field ever contains
the literal string `<!DOCTYPE`, the parser rejects the file. It would
have to be a user writing about XML in their jump notes.

**Why it matters.** This is documented in the code; it's not a
hidden blind spot. But there's no unit test proving the behaviour
(accept or reject) for a CDATA-embedded DOCTYPE, so a future parser
refactor could silently change the posture.

**Status.** Test-coverage gap. Worth a pinned test that documents the
intended behaviour.

---

### A15. No graceful-shutdown / lockfile-release test

**Evidence.** `backend/main.py:118` releases the lock in a `finally`
block. Uvicorn installs its own SIGTERM handler that unwinds the ASGI
app cleanly; on SIGKILL the `finally` does not run and the lockfile
is left behind. `filelock` on POSIX uses `flock(2)`, which releases
when the fd closes — which the kernel does when the process dies —
so a stale `.logbook.lock` file is harmless. On Windows, msvcrt
locking also releases on process death.

**Why it matters.** Probably never. But there's no test asserting
that a SIGKILL'd backend leaves a recoverable state. The test exists
conceptually in `_crash_child.py` but only for internal write
primitives; there's no parent-process-level "SIGKILL uvicorn → next
start works" test.

**Status.** Test debt; low priority.

---

## Section B — Future improvements to already-working code

These are not "bugs." They are "room to evolve" for when the context
asks more of the code than it asks today.

### B1. `responses=` kwargs on routes

The API agent flagged this; restating with more context. `openapi.py`
already defines `NotFound` / `Conflict` / `ValidationFailed` /
`IntegrityError` as shared response components referencing
`ProblemDetails`. Routes in `jumps.py` don't attach them via
`responses={}`. The consequence is purely documentary — runtime
behaviour is correct. This is a mechanical improvement that makes
`/openapi.json` more useful to third-party SDK generators.

### B2. Connection-per-request cost

`jump_service.py` opens and closes a fresh SQLite connection for every
operation. The research into `sqlite3.connect` puts the cost in the
tens of microseconds on SSD — negligible. If a future phase adds a
dashboard endpoint that runs many queries per request, a
connection-per-request dependency (instead of per-operation) becomes
a cleaner pattern and saves the PRAGMA-setting overhead on each call.
See Simon Willison's [Datasette SQLite patterns](https://simonwillison.net/2022/Jul/18/weeknotes/)
for the established shape.

### B3. `update_jump` re-reads folder path

Step 1 of `update_jump` calls `get_jump` which already knows the
folder path; the function discards it and step 4 queries SQLite again
to resolve it. Refactoring `get_jump` to return or expose the folder
path internally would shave one DB round-trip per update. Cosmetic;
not worth pulling forward.

### B4. Attachments never re-streamed

`atomic_write` in `create_jump` already has `jump_xml_bytes` in
memory when it writes `jump.xml`; `from_jump_xml` then reads
`jump.xml` back from disk to hash it. That's a needless re-read.
`manifest.sha256_bytes` exists. A small refactor would pass the
bytes through. Not a hot path.

### B5. `PRAGMA busy_timeout` is unset (defaults to 0)

Under the current process-lockfile + connection-per-op pattern,
SQLITE_BUSY is essentially unreachable. If a future phase holds a
connection longer, or multiple threads within a request get
connections, setting `busy_timeout` to a small value (e.g. 250ms)
makes contention graceful instead of immediate. Cost-free insurance
when it becomes relevant.

### B6. Metric emission is log-only

`observability/logging.py` emits JSONL events; there's no Prometheus-
style counter/histogram surface. For v0.1 desktop use, fine. When
the pywebview frontend ships and wants to show "last 5 write
durations," a metric surface would be helpful. Out of v0.1 scope; a
D-entry when the frontend design is ready.

---

## Section C — Revisiting the earlier pass's findings

### C1. Invariants: "CRITICAL D16 catch-all" — stands, with nuance

I verified the handler chain directly (`rest.py:45`, no `Exception`
handler). The earlier agent was right on invariant, but the severity
depends on the deployment posture. Loopback-only + typed-error
discipline means the current observable surface is very small.
Reframe: "High priority, low blast radius at current defaults."

### C2. DECISIONS drift: "25 of 31 verified, 1 drift" — revise to "24 of 31 verified, 2 drift"

The agent caught D26 code-lag on `reindex_from_xml`. It missed D24
`_hints`. No other D-entries flagged by spot-read.

### C3. Tests: "D3 reindex test untestable because reindex doesn't exist" — unchanged

Accurate and scoped to the natural next phase.

### C4. API: "OpenAPI error-responses incomplete" — correct but quicker-to-fix than implied

The `ProblemDetails` schema and four response components already
exist in `openapi.py`. Wiring is four lines per route — mechanical,
not architectural.

### C5. Earlier "clean passes" that remain clean after re-read

D2 hardened parser (DTD-reject byte-level + lxml defenses layered),
D3 index rebuildability (no field in `_SCHEMA` lacks an XML
counterpart), D4 path safety (safe_join + sanitizers applied
consistently), D7 route thinness (every handler is ≤5 lines of
adapter logic), D10 atomic_write (caveats in the docstring), D19
soft delete, D25 crash ordering in `create_jump`, D27 JSON logging +
correlation via pure-ASGI middleware, D28 config layering, D29
idempotent bootstrap.

---

## Section D — Open research questions deserving a D-entry

### D1. Cloud-sync-folder deployment position

**Question.** Does the project support Dropbox / iCloud / OneDrive
as the logbook location, reject it, or document it as a known
limitation?

**Why now.** Every self-hosted single-user tool faces this. D9's
lockfile story (flock → mandatory-locking on CIFS in kernels 5.5+
per [Linux CIFS flock patch](https://man7.org/linux/man-pages/man2/flock.2.html))
and D3's WAL posture both depend on the answer. Without a position,
the app will either surprise users or quietly work around the gap.

**Shape of the decision.** Three options are defensible: (a) local-FS
only; document in README, detect known paths at bootstrap, warn.
(b) cloud-FS safe; switch to `journal_mode=DELETE`, accept perf hit,
add tests on a simulated delayed-sync. (c) cloud-FS best-effort;
document that WAL-on-cloud is unsupported; leave it to the user.

### D2. pywebview auth model (when the frontend lands)

**Question.** When the pywebview bundle ships (D11), how does the
embedded UI authenticate to the localhost backend?

**Why now.** The bearer-auth scheme (§A2) is half-wired.
Resolving A2 requires knowing whether the UI needs a token (yes, if
LAN exposure ever happens) or not (no, if loopback-only forever).
The answer shapes `config.py`, `deps.py:get_user_id`, and the UI
bootstrapping story.

### D3. Intra-process concurrency model

**Question.** Is the app expected to serialise all writes within a
single process, or to permit concurrent writes subject to SQLite
+ lockfile semantics?

**Why now.** §A7 identifies a race in `update_jump`. D9 covers
inter-process. Nothing covers intra-process. A small D-entry ("writes
are serialised by an in-process writer lock at the service layer")
closes A7 by construction. Alternatively, "concurrent writes are
OK; every service step is idempotent or carries enough state to
recover" commits to a different but equally defensible posture.

### D4. Signing scope (future D-entry for D6)

**Question.** When D6's digital signing lands, does the signature
cover `jump.xml` bytes only, or does it additionally bind attachment
hashes externally?

**Why now.** Not urgent (D6 reserves it). But decisions made now
about the auto-strip rule, the scope of `update_jump`'s XML rewrite,
and how `folder_reconcile` handles signed jumps all pre-commit to
shape. Writing a draft D-entry before the signing phase starts
preserves optionality.

### D5. Backup + disaster-recovery story

**Question.** How does a user restore from a drive failure? Is the
recommendation "`rsync -a` the logbook folder," or is there a
shipped `export` / `import` flow?

**Why now.** Not a code decision for v0.1; a README decision. The
answer informs whether the lockfile, `index.sqlite`, and
`.trash/` should be excluded from backups or included. D3's
rebuildability makes the index skippable; the trash is a user-value
question.

### D6. Windows path-length posture

**Question.** On Windows, the path limit is 260 chars without
`\\?\` prefix or the opt-in long-paths registry flag. A
deeply-nested user path + a long jump title can approach this. The
code does not use `\\?\` prefixes. Is long-path support in scope?

**Why now.** Rare in practice. Worth naming the limit so a user
who hits it gets a clean error rather than an OSError. Could be
resolved by a byte-length check in `sanitize_folder_name` (§A9).

---

## References

### Primary sources cited

- [RFC 9457 — Problem Details for HTTP APIs](https://www.rfc-editor.org/rfc/rfc9457)
- [RFC 6901 — JavaScript Object Notation (JSON) Pointer](https://www.rfc-editor.org/rfc/rfc6901)
- [RFC 7578 — Returning Values from Forms: multipart/form-data](https://www.rfc-editor.org/rfc/rfc7578)
- [RFC 8259 — The JavaScript Object Notation (JSON) Data Interchange Format](https://www.rfc-editor.org/rfc/rfc8259)
- [Linux flock(2) man page — CIFS details](https://man7.org/linux/man-pages/man2/flock.2.html)
- [POSIX rename(2) specification](https://pubs.opengroup.org/onlinepubs/9699919799/functions/rename.html)
- [SQLite Write-Ahead Logging (WAL) §5, §8](https://www.sqlite.org/wal.html)
- [SQLite PRAGMA fullfsync](https://www.sqlite.org/pragma.html#pragma_fullfsync)
- [Python `os.fsync` / `os.replace` docs](https://docs.python.org/3/library/os.html#os.fsync)
- [Python `sqlite3` module docs](https://docs.python.org/3/library/sqlite3.html)
- [Python `importlib.resources` docs](https://docs.python.org/3/library/importlib.resources.html)
- [Microsoft — MoveFileExW API](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw)
- [Microsoft — File naming rules, reserved device names](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file)
- [Apple — fsync(2) manual, F_FULLFSYNC note](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/fsync.2.html)
- [LWN — ext4 and data loss](https://lwn.net/Articles/322823/)
- [OWASP XML External Entity Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/XML_External_Entity_Prevention_Cheat_Sheet.html)

### CVEs

- [CVE-2024-24762 — python-multipart ReDoS](https://github.com/advisories/GHSA-2jv5-9r88-3w3p)
- [CVE-2024-53981 — python-multipart DoS via deformation boundary](https://nvd.nist.gov/vuln/detail/CVE-2024-53981)
- [CVE-2026-40347 — python-multipart preamble/epilogue DoS](https://github.com/advisories/GHSA-mj87-hwqh-73pj)

### Project sources

- CLAUDE.md (project root)
- DECISIONS.md (project root) — D2, D3, D4, D6, D7, D9, D10, D14, D16, D17,
  D18, D19, D21, D22, D23, D24, D25, D26, D27, D28, D29, D30, D31, D32
- ARCHITECTURE.md (project root)
- Research reports from the initial pass (same `reviews/` folder):
  `2026-04-23-invariants.md`, `2026-04-23-decisions-drift.md`,
  `2026-04-23-tests.md`, `2026-04-23-api.md`,
  `2026-04-23-research-platform.md`,
  `2026-04-23-research-sqlite.md`,
  `2026-04-23-research-multipart.md`
