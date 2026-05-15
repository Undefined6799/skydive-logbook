# Decisions

Canonical record of *why* this project is shaped the way it is. Each
decision is the outcome of a deliberate trade-off. If something in the
code disagrees with this file, the code is right and this file is
stale — please fix.

Format: numbered decisions. Each has **Decision**, **Why**, and
optionally **Alternatives considered** and **Consequences**. Decisions
are versioned by adding a new numbered decision that supersedes an
older one; we don't edit old decisions in place.

In-flight decisions that haven't been ratified yet live in
[docs/decisions-draft.md](docs/decisions-draft.md) — they keep their D-number
when promoted here. D55 and D56 are currently drafts; D52 was
promoted to this file on 2026-05-14.

---

## D1 — Public API is REST + OpenAPI 3.1, not SOAP

**Decision.** The public API is a REST API documented with a published
OpenAPI 3.1 specification. There is no SOAP endpoint.

**Why.** The original motivation for SOAP was "XML for data integrity
and accuracy" — but integrity comes from the on-disk storage format
(XML + XSD + manifest), not from the wire protocol. SOAP adds friction
for third-party developers and for our own SPA, provides nothing we
need, and depends on Spyne, which has had no release since February
2022. Dropping SOAP removes a bus-factor risk and widens the potential
developer audience.

**Alternatives considered.** (a) SOAP-only — rejected: Spyne risk, no
precedent in skydiving tooling, painful for SPA. (b) Dual SOAP + REST
— rejected: two public contracts for the same logic, twice the
versioning discipline, for a use case we haven't validated.

**Consequences.** We author an OpenAPI spec alongside the REST
adapter. Generated clients for third-party devs in any language come
for free via OpenAPI Generator. The `api-contract-steward` agent now
owns the OpenAPI spec and the XSD schemas.

---

## D2 — XML on disk is the source of truth, validated against versioned XSD

**Decision.** Every jump is stored as a `jump.xml` file validated
against a versioned XSD schema. The XML files and the XSDs live
together in the logbook folder (see D18 for versioning), so the data
is self-describing even without the app.

**Schema choice:** XSD 1.0, validated with `lxml.etree.XMLSchema`.
1.0 is supported by every mainstream XML tool (`xmllint`, language
parsers in every major language); 1.1 would require the
pure-Python `xmlschema` library and narrows interop.

**Why.** XML + XSD gives us a machine-enforceable schema, human-
readable storage, and long-term data portability. If this project
disappears, anyone with a text editor and an XSD validator can still
read and verify the files.

**Consequences.** Every field exists in three synchronized places: the
Pydantic model (runtime validation, API shape), the XSD (file-format
contract), and the SQLite index schema (query performance). The
Pydantic model is the single source of truth; XSD and SQLite schemas
derive from it, enforced by a generation/diff check in CI.

---

## D3 — SQLite is an index, rebuildable from the XML

**Decision.** `index.sqlite` exists only to make list/filter/stats
queries fast. Every write path updates XML first, then updates the
index. A `reindex` command walks the jump folders and rebuilds the
index from scratch.

**Why.** Databases corrupt. Formats drift. If someone deletes the
index file, we should lose zero user data — a reindex recovers
everything. This also makes cloud-sync scenarios tolerable: Dropbox
may race on the SQLite file, but the XML files survive, and reindex
fixes the damage.

**Consequences.** We never store anything in SQLite that doesn't
also exist in XML. Computed values (total freefall time, jump counts)
are calculated on read, not persisted.

---

## D4 — Human-readable folder names; stable UUIDs in the XML

**Revised 2026-04-23.** The folder-name shape and the ASCII-only rule
have been edited in place because neither has shipped to any user
data yet. Pre-revision: `[<jump#>] <ISO-date>`, ASCII folder names
only. Post-revision: `[<jump#>] <title>` (title optional), Unicode
allowed. The security and portability rules (forbidden chars,
Windows reserved names, NFC normalization, trailing-space/period
rejection) are unchanged.

**Decision.** Jump folders are named `jumps/[<jump#>] <title>/` —
for example `jumps/[851] First 4-way of the season/`. The title is
optional human-readable free text stored as a `<title>` element in
`jump.xml` and mirrored into the folder name for at-a-glance
browsing. When the title is absent or empty, the folder name is just
`[<jump#>]` (e.g. `jumps/[851]/`).

Each `jump.xml` contains a stable `<id>` element with a UUIDv4 that
never changes, even if the jump number or title are later corrected.
Database references use the UUID, not the folder name.

**Title field.** `<title>` on `jump.xml`:
- Optional. Omitted or empty → folder name has no title segment.
- Free text; no uniqueness requirement. Two jumps can share a title —
  the `[<jump#>]` prefix keeps folder names unique (jump_number is
  unique per D23).
- Maximum 120 Unicode characters. Comfortably under the 255-byte
  filename cap even for emoji-dense UTF-8, and short enough that a
  file manager still lays out each row legibly.

**The title ↔ folder-name relationship is asymmetric by design.**
The rules below keep D6's future signing story sound (a signed
`jump.xml` must not be mutated by cosmetic filesystem operations)
while keeping the common UX intuitive (editing the title in the app
updates the folder name you see in Finder).

- **On jump creation**, the service reads `<title>` from the
  just-written XML and generates the folder name
  `[<jump#>] <title>` (or bare `[<jump#>]` when title is absent).
- **Manual folder rename** — through Finder, Explorer, `mv`, or any
  tool that isn't the app — **never touches `jump.xml`**. XML bytes
  stay byte-identical, so any signature on them remains valid. The
  `[<jump#>]` prefix is the only load-bearing piece (D23 uniqueness
  scans for it); everything after is user-editable free text. If
  the user breaks the `[<N>]` prefix, the folder still indexes
  cleanly because `reindex` reads jump_number from XML, not from
  folder name; the only consequence is that D23's filesystem
  backstop may miss a subsequent duplicate-number attempt (the
  SQLite UNIQUE constraint and `verify` both still catch it).
- **API title edit** rewrites `jump.xml` AND atomically renames the
  folder to match the new title — same mechanics as the D23
  jump_number correction flow. This is a data-mutation path: the
  caller deliberately chose to edit the title, any existing
  signature is already being invalidated by the XML rewrite, and
  keeping the folder name in sync with the canonical title is the
  intuitive UX. See D23 §Renames.

The asymmetry in one line: **data changes (API) propagate to
cosmetic layers; cosmetic changes (filesystem) do not propagate to
data.**

For display: the app UI always shows the XML `<title>` as the
canonical label. A manually-renamed folder that drifts from the XML
title is invisible to the app user — they see the XML title — until
they browse the filesystem directly.

**Character rules for folder names:**
- Forbidden on any platform: `/`, `\`, `:`, `*`, `?`, `"`, `<`, `>`,
  `|`, and control characters. The filesystem module rejects these
  in every user-provided folder-component via `sanitize_folder_name`.
- Forbidden by Windows filesystem rules: reserved device names
  (`CON`, `PRN`, `AUX`, `NUL`, `COM1..9`, `LPT1..9`) and trailing
  space or period. Also rejected.
- **Unicode is permitted.** Titles can contain accents, emoji, CJK,
  etc. Folder names on modern macOS (APFS), Windows (NTFS), and
  Linux (ext4) all support arbitrary Unicode filenames. Edge cases
  — old FAT32, some NAS firmware, some non-Unicode CLIs — are out
  of the v0.1 supported configuration.
- All writes normalize Unicode to NFC (Windows default), even on
  macOS which can internally use NFD on HFS+, so the same jump
  produces the same folder name across sync machines.

**Why this shape.** Folder names exist for humans browsing in Finder
or Explorer. The date was machine-readable duplication — already in
`jump.xml` as `<date>` — and told users *when* but not *what*. A
human-written title ("Glacier jump," "First wingsuit," "Night dive")
is what people actually remember. Sort order still works: the
`[<jump#>]` prefix gives a natural sort key; file managers lay out
by jump number regardless of title content. Jump numbers still get
corrected — the UUID stays stable so the index follows cleanly.

**Why relax the ASCII-only rule.** The earlier constraint was
cross-platform caution, but the real portability work is done by
the forbidden-char set, Windows reserved-name check, and NFC
normalization — not by the ASCII restriction. Forcing users to
rewrite "Première" as "Premiere" adds friction with no
correspondingly reduced failure mode on any filesystem v0.1
targets.

**Alternatives considered.**
- *UUID folders.* Rejected: unreadable in Finder/Explorer.
- *Jump-number only.* Rejected: collisions with deleted-then-
  recreated jumps (the `.trash/` namespace is disjoint, but the
  user may want to distinguish two jumps numbered the same across
  time via folder name).
- *ISO date only.* Rejected: multiple jumps per day collide.
- *`[<jump#>] <ISO-date>` (pre-2026-04-23).* Rejected on 2026-04-23
  as noted above: date is redundant with `<date>` in jump.xml;
  title gives humans what the date cannot (the *what*).
- *Require title.* Rejected: forces users to think of a name at
  create time even when they just want to quickly log a jump.
  Optional title + stable `[<jump#>]` prefix satisfies both
  workflows.
- *Keep ASCII-only for folder names.* Rejected as above.

**D23 interaction.** Title-varying folder names mean
`mkdir(exist_ok=False)` is no longer sufficient for uniqueness;
the collision check moves into the service layer as a prefix scan
of `jumps/` for `[<N>]` or `[<N>] `. API-driven renames (jump_number
or title change) rewrite XML then atomically rename the folder;
filesystem-level renames never round-trip to XML. See D23.

**Consequences.**
- `backend/storage/filesystem.py:jump_folder_name` takes
  `(jump_number: int, title: str | None = None) -> str` and returns
  `[<N>]` when title is absent/empty, `[<N>] <title>` otherwise.
  Both forms go through `sanitize_folder_name` so the returned
  string is safe to use as a path component.
- `sanitize_folder_name` keeps all its rejection rules; the
  implicit ASCII-only norm was never enforced in code (the tests
  still accept ASCII, we just now also accept Unicode).
- `backend/models/jump.py:Jump` gains `title: str | None =
  Field(default=None, max_length=120)`. `JumpCreate` gains the
  same.
- `SCHEMA.v1.xsd` adds `<title>` as an optional element on `<jump>`
  with `maxLength="120"`. This is additive within v1 per D18
  (changes within a major version are strictly additive). Jumps
  written before the field was added validate without a title;
  jumps written after may carry one.
- `backend/xml/serialize.py` serializes and parses the new element.
- D23 §Renames: the rename trigger list expands from "jump_number
  change" to "jump_number or title change via API." Same atomic
  mechanics (rewrite XML → `os.rename` folder → index upsert);
  manual filesystem renames are NOT covered by this path and
  deliberately don't round-trip to XML.
- Index schema: no change. Title is not indexed (if it ever becomes
  searchable, that's a future D26 bump with `UNIQUE` not required).
- Signing (D6, reserved): manual folder renames are safe — XML
  bytes stay unchanged, so any signature holds. API title edits
  DO mutate XML; on a signed jump that rewrite strips the
  `<signature>` element as part of the same write (D6 §Reserved
  note). The jump is unsigned after the edit; the user re-signs
  when ready. The title↔folder asymmetry keeps "I tidied a folder
  name in Finder" from ever touching the signature.

---

## D5 — Each jump folder is self-describing to a human

**Decision.** Two files per jump folder are authoritative:
- `jump.xml` — structured source of truth (D2).
- `SHA256SUMS` — SHA-256 checksums, one per line, compatible with
  `shasum -c SHA256SUMS` (GNU coreutils convention). Covers every file
  in the folder, including `jump.xml` itself and all uploads.

A third file is **derived, non-authoritative, and regenerable**:
- `summary.md` — one-paragraph, plain-language summary (jump number,
  date, dropzone, aircraft, discipline, freefall time, deployment
  altitude). Present for humans browsing the folder in Finder,
  Explorer, GitHub, or any Markdown viewer. If it is missing, stale,
  or corrupted, the app regenerates it from `jump.xml` on next read.
  Its absence is not an error and does not affect verification.

The logbook root also contains:
- `SCHEMA.v1.xsd` (and later `SCHEMA.v2.xsd`, etc.) — see D18.
- `README.md` — human-authored overview of the directory structure
  for someone opening the folder cold. *This* README is authoritative
  and ships with the app; only the per-jump `summary.md` is derived.

**Why.** Authoritative files define what the app must keep in sync on
every write. Derived files are convenience and can be rebuilt. Naming
the derived file `summary.md` instead of `README.md` avoids the
"read-this-first, source-of-truth" connotation that `README.md`
carries everywhere else in software. A user opening the logbook with
no app installed can still (a) read the per-jump summary at a glance
(`summary.md`), (b) verify nothing is corrupted (`shasum -c
SHA256SUMS`), (c) validate the XML (`xmllint --schema SCHEMA.v1.xsd
jump.xml`).

**Consequences.** The write transaction must keep `jump.xml` and
`SHA256SUMS` consistent at all times; `summary.md` is written as a
best-effort convenience and a failure to write it does not fail the
jump save. A `verify` command checks every folder's `SHA256SUMS`
against files on disk. `summary.md` is excluded from `SHA256SUMS` —
because it is derived, including it would turn every summary-template
change into a false-positive integrity failure.

---

## D6 — Integrity today: XSD + SHA-256 manifest. Digital signing: deferred.

**Decision.** v0.1 enforces data integrity through two mechanisms:
(1) XSD validation on every XML read/write, (2) SHA-256 manifest per
jump folder detecting corruption or accidental edits. The XML schema
reserves an optional `<signature>` element on `<jump>` so
cryptographic signing can be added in a later version without a
schema migration.

**Why.** Integrity against corruption and truncation is high-value
and cheap. Tamper-evident signing is high-value but expensive to
design (key management, signature scope, verification UX). We do the
cheap thing now and set up the schema seam so the expensive thing is
additive later.

**Consequences.** The `<signature>` element is documented as
"reserved, unused in v1" in the XSD. No code reads or writes it yet.

**Reserved note for when signing lands (added 2026-04-23).**

**Signing is optional at every point in a jump's lifecycle.** A
jump can be created unsigned, live unsigned forever, be signed
later, be edited (auto-unsigning it), be re-signed, or never be
signed. "Unsigned" is not a defect state; it is the default and
always a valid state. The user initiates signing deliberately when
they decide a jump is final.

The only concern is what happens when an already-signed jump is
edited. The rule is simple: **any edit to a signed jump strips the
`<signature>` element as part of the same write.** The jump becomes
unsigned after the edit; the user re-signs explicitly when they're
ready — same as if it had never been signed.

Rationale. A confirmation prompt ("this will invalidate the
signature — proceed?") at every edit introduces friction that
either (a) becomes annoying and trained-through by users, or (b)
blocks edits on signed jumps until a separate unsign step. The
auto-strip rule threads the needle: edits proceed freely, the
signature accurately reflects state (present = cryptographically
valid; absent = stale or never signed), and re-signing is a
deliberate action the user takes when they're done editing.

Concrete rules for the signing slice to implement:

- **Any service function that rewrites `jump.xml`** — `update_jump`
  on any field, `create_jump` on an overwrite path, future
  import/migration paths — clears `<signature>` as part of the
  serialization. Absent/empty → no-op; present → dropped. Tests
  assert that an update preserves every other field and strips
  `<signature>`.
- **Manual filesystem folder renames** never touch `jump.xml`, so a
  signed jump survives cosmetic folder renames unchanged. This is
  the whole reason D4 made the title↔folder relationship
  asymmetric.
- **`folder_reconcile` (D25)** rewrites `SHA256SUMS` only, not
  `jump.xml`. `SHA256SUMS` is a derived index file outside the
  signed scope, so reconcile is safe on signed jumps and does NOT
  strip the signature.
- **Verify (D25)** reports a signed jump whose XML fails XSD or
  whose signature string is malformed as an integrity issue. The
  exact issue kind (e.g. `signature_invalid`) lands with the
  signing slice alongside the verification function itself.

In one line: edits strip signatures automatically; everything else
preserves them.

---

## D7 — Service layer owns logic; REST adapter is thin

**Decision.** All business logic lives in `backend/services/`. The
FastAPI adapter in `backend/api/rest.py` only translates inputs,
calls a service function, and translates outputs.

**Why.** Keeps the adapter easy to replace or add to (a CLI adapter,
a future GraphQL adapter). Makes services directly testable without a
web stack. Keeps the API surface honest — if a service function is
awkward to expose, the function is wrong, not the adapter.

**Consequences.** Every service function is fully usable without
FastAPI. Tests import services directly.

---

## D8 — `user_id` is a parameter from day one; default `"default"`

**Decision.** Every service function takes `user_id: str` as a
parameter. Today it is always `"default"`. The on-disk layout does
not currently include a user prefix, but the services route through
`user_id` and `storage/filesystem.py` is the only place that maps it
to paths.

**Why.** Adding multi-user support later becomes a localized change
rather than a cross-cutting refactor.

**Consequences.** Service signatures are stable across the single-
user → multi-user transition.

---

## D9 — Single-instance lock per logbook folder

**Decision.** On startup, the app acquires an advisory file lock on
`<logbook_root>/.logbook.lock` using the `filelock` library (cross-
platform: `fcntl` on POSIX, `msvcrt` on Windows). If another instance
already holds the lock, the new instance exits with a clear error
message.

**Why.** Two app instances writing to the same folder is unsafe.
Locking is simpler and safer than trying to coordinate multiple
writers.

**Consequences.** Read-only tools (a terminal, another browser,
someone running `shasum -c`) can coexist with the running app. The
lock only gates writers.

---

## D10 — Atomic writes everywhere

**Decision.** Every file write goes through a single
`filesystem.atomic_write(path, bytes)` helper that: (1) writes to
`<path>.tmp` in the same directory, (2) `os.fsync()`s the file, (3)
`os.replace(tmp, path)` — atomic on POSIX and on Windows since Python
3.3 (`MoveFileExW` with `MOVEFILE_REPLACE_EXISTING`).

**Why.** A crash mid-write must never leave a partially written
XML/README/manifest. Atomic rename is the standard guarantee.

**Consequences.** No direct `open(path, 'wb')` calls outside the
helper. Linting/review enforces this.

---

## D11 — Packaging: pywebview frontend, per-platform bundlers

**Decision.** The app is distributed as a native installer per
platform: `.dmg` for macOS (universal: Intel + Apple Silicon), `.exe`
or MSI for Windows 10/11 x86_64, AppImage for Linux.

The shell is pywebview (native WebView per OS: WKWebView on macOS,
WebView2 on Windows, WebKit2GTK on Linux). The Python backend and the
Vite-built React frontend are bundled together. Cross-platform
bundling is split by tooling:

- **macOS:** `py2app`. pywebview's own docs recommend this over
  PyInstaller because PyInstaller has known issues with pywebview's
  WKWebView integration on macOS.
- **Windows:** PyInstaller. WebView2 runtime is shipped by default
  on Windows 11 and is distributed via the Evergreen bootstrap on
  Windows 10.
- **Linux:** PyInstaller producing an AppImage. Depends on system
  WebKit2GTK.

**Why.** Keeps us in one language (Python) with a static frontend.
Avoids the Rust toolchain of Tauri and Electron's binary footprint.
Different bundlers per platform is a small, bounded amount of config
per target — less risk than a cross-platform abstraction we'd have to
debug.

**Consequences.** The release pipeline is a GitHub Actions matrix
across three runners. The packaging config lives in `scripts/
packaging/<platform>/`. First release is manual; automation lands
with v0.2.

---

## D12 — Units: configurable; meters internally; default per locale

**Decision.** All altitudes and distances are stored in meters and
transmitted in meters over the API. The UI displays in feet or meters
based on a user preference. On first run we default to feet if the
system locale is `en_US`, meters otherwise.

**Why.** Single canonical unit internally avoids conversion bugs.
Display preference matches the user's mental model (USPA-trained
jumpers think in feet; most of the world thinks in meters).

**Consequences.** `frontend/src/lib/units.ts` handles the conversion
at the UI boundary. API responses always return meters. Never store
feet anywhere in the backend.

---

## D13 — License: MIT

**Decision.** The project is MIT licensed. `LICENSE` at the
repository root.

**Why.** Shortest, most widely recognized permissive license. Low
friction for users, forks, and downstream commercial use.

**Consequences.** We do not accept contributions encumbered by a
non-compatible license.

---

## D14 — v0.1 scope

**Decision.** The first release is complete when all of the
following work end-to-end:

1. **Log a jump.** Manual entry via a web form, all USPA-style fields
   (jump#, date, DZ, aircraft, altitudes, freefall time, jump type,
   equipment-by-reference, signature, notes). Saves to XML, indexes
   to SQLite, visible in the jump list.
2. **Upload files to a jump.** FlySight CSVs, videos, photos —
   stored as-is under the jump folder, recorded in `<attachments>`
   in `jump.xml`, hashed into `SHA256SUMS`.
3. **Equipment tracking.** Add/edit containers, canopies, and AADs
   as separate entities. Link them to jumps by reference. Track
   reserve repack dates and AAD service intervals.
4. **Basic stats.** Dashboard widget: total jumps, total freefall
   time, jumps by canopy, jumps this year.

**Why.** Usable-from-day-one feature set for a skydiver logging
their own jumps. Defers FlySight parsing, importers, digital signing,
and multi-user explicitly.

**Consequences.** We resist scope creep by pointing at this list.

---

## D15 — Python version: 3.11+

**Decision.** The backend runs on Python 3.11 or newer. We do not
support 3.10 or earlier.

**Why.** 3.11 improved exception groups, TOML support in the standard
library (`tomllib`), and substantial performance gains. Every
dependency we care about (FastAPI, Pydantic v2, lxml, filelock,
pywebview, PyInstaller, py2app) works on 3.11+. Dropping 3.10 costs
nothing because we have no existing users.

**Consequences.** `pyproject.toml` declares `requires-python = ">=3.11"`.
CI tests run on 3.11, 3.12, 3.13.

---

## D16 — Structured error responses (RFC 9457 problem+json)

**Decision.** Every REST error response uses the `application/problem+json`
media type and the body shape defined in [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457.html)
(the successor to RFC 7807), with three documented extensions:

```json
{
  "type":       "about:blank",
  "title":      "Not Found",
  "status":     404,
  "detail":     "No jump with id 42a3c4 exists.",
  "instance":   "/api/v1/jumps/42a3c4",
  "code":       "not_found",
  "request_id": "a1b2c3d4-...",
  "errors":     [
    { "pointer": "#/exit_altitude_m", "detail": "must be >= 0" }
  ]
}
```

Standard members (per RFC 9457 §3.1):
- `type` — URI identifying the problem type. We ship `about:blank` in v1
  (the RFC default, §3.1.1) because we do not publish dereferenceable
  documentation URIs yet. Switching to per-type URIs later is additive.
- `title` — constant short human-readable summary for the problem type.
- `status` — advisory duplicate of the HTTP status (§3.1.2). The HTTP
  header is authoritative; `status` exists for clients persisting the
  body without headers and for detecting status changes by intermediaries.
- `detail` — occurrence-specific human-readable explanation.
- `instance` — URI reference identifying this occurrence. We use the
  request path.

Extensions (RFC 9457 §3.2, names ≥3 chars matching `[A-Za-z_][A-Za-z0-9_]*`):
- `code` — stable machine-readable identifier. Clients MUST branch on
  `code`, not `title` or `detail`. Adding a code is additive; renaming
  one is a breaking change.
- `request_id` — the per-request UUIDv4 also returned in the
  `X-Request-Id` header.
- `errors` — optional array of field-level validation errors; each entry
  is `{ "pointer": "<RFC 6901 JSON Pointer>", "detail": "<message>" }`.
  The shape matches the validation-error example in RFC 9457 §3.

Status-code families: 400 (client error), 404 (not found), 409 (conflict),
422 (validation), 500 (server error). The OpenAPI spec registers
`ProblemDetails` as a shared component and lists the error codes each
endpoint can return.

**Why RFC 9457.** Third-party developers will code against whatever
shape we ship first. The RFC is the industry standard (IETF, 2024);
committing to it now means we inherit tooling, client libraries, and
ecosystem knowledge without inventing a bespoke envelope. A flat body
with siblings keeps extensions discoverable rather than hiding them
behind a nested `error` key.

**Consequences.**
- Error codes are part of the contract (see D1). Additions are additive;
  renames are breaking changes requiring a v2.
- The media type is `application/problem+json`, not
  `application/json`. Clients doing strict content-type matching must be
  updated accordingly — this is documented in the API reference.
- A later move to per-type URIs (`type: "https://skydive-logbook.org/errors/not-found"`)
  is additive within v1 provided the existing `code` identifiers remain
  unchanged.

---

## D17 — Time and date semantics

**Decision.**
- **Jump date** (`<date>` in jump.xml, `date` in API) is a **local
  date** (`YYYY-MM-DD`), no time, no timezone. It represents the
  calendar day the jumper considers the jump to belong to. This
  matches how logs are actually kept.
- **Jump time of day** (`<time>`, optional) is a local clock time
  (`HH:MM`), paired with an explicit `<timezone>` (IANA, e.g.,
  `America/Los_Angeles`) on the containing jump. If timezone is
  absent, the time is "unspecified local."
- **Audit timestamps** (`created_at`, `updated_at` in API; stored in
  SQLite, not in the XML) are **UTC ISO 8601 with offset**
  (`2026-04-22T19:03:00Z`).

**Why.** Jumpers think about "April 22, 2026" regardless of UTC;
storing the jump date with a timezone leads to bugs where a late-
evening jump in UTC-8 looks like it happened the next day. Audit
timestamps need UTC for sortability across machines.

**Consequences.** Date and time are separate fields in the schema.
The XSD uses `xs:date` for the jump date and `xs:time` for time-of-
day. The API surfaces both clearly. Tests cover a jump logged at
23:00 local in a non-UTC zone.

---

## D18 — XSD versioning inside the logbook folder

**Decision.** The logbook folder contains every XSD version ever
shipped: `SCHEMA.v1.xsd`, `SCHEMA.v2.xsd`, etc. Each `jump.xml`
declares which schema version it conforms to via its XML namespace:

```xml
<jump xmlns="https://skydive-logbook.org/schema/v1" ...>
```

When the app writes a new jump it uses the current schema. When it
reads a jump it picks the XSD matching the declared namespace.

**Why.** We will evolve the schema. Old jump files must keep
validating forever — breaking old data is unacceptable. Keeping every
version of the XSD *with* the data means third-party tools can
validate any jump without knowing which version of our app wrote it.

**Consequences.** Release of a v2 schema requires: (a) a migration
script that optionally upgrades v1 files to v2, (b) dual-read support
in the code (accept either namespace), (c) a deprecation window for
v1 writes. We keep v1 read support indefinitely.

---

## D19 — Soft delete to `.trash/`

**Decision.** Deleting a jump moves its folder to
`<logbook_root>/.trash/<timestamp>_<original-name>/` rather than
removing it. The SQLite index is updated to reflect the deletion. A
"Restore" UI action (or manual `mv` back into `jumps/`) brings it back
and reindexes.

**Why.** Accidental deletion is a real failure mode. Disk is cheap;
second chances are cheap too. Users not aware of the trash folder can
still recover via cloud-sync history if they use it — and via the
trash folder if they don't.

**Consequences.** `.trash/` is ignored by the index but included in
`verify`. The UI shows a Trash view. v0.1 does not auto-empty the
trash; users clear it manually.

---

## D20 — Config paths: app config in user dir; logbook config in logbook

**Decision.**
- **App config** (which logbook folder to open, window size, last-
  run version) lives in the OS user config dir:
  - macOS: `~/Library/Application Support/skydive-logbook/config.toml`
  - Windows: `%APPDATA%\skydive-logbook\config.toml`
  - Linux: `$XDG_CONFIG_HOME/skydive-logbook/config.toml` (falling
    back to `~/.config/skydive-logbook/config.toml`)
- **Logbook config** (display units, jumper name, default DZ) lives
  **inside the logbook folder** as `<logbook_root>/settings.xml`.

**Why.** Settings that belong to the *person* (unit preference, name)
should travel with the data when the folder is copied or synced.
Settings that belong to the *installation* (where the logbook is)
don't.

**Consequences.** Copying the logbook folder to another machine
preserves the user's preferences automatically. Reinstalling the app
doesn't lose the logbook folder selection if the user picks the same
root again.

---

## D21 — Attachment size: no hard cap in v1

**Decision.** Attachment uploads have no explicit server-enforced upper
bound on file size in v1. The server writes whatever the client sends,
subject only to the filesystem and OS limits of the logbook folder.

**Why.** Jump videos vary wildly — a GoPro clip can be 200 MB, a
multi-camera wingsuit edit can be several GB. Picking an arbitrary
ceiling now either excludes legitimate content (too low) or provides
false security (too high). Since the app is single-user local-first
(D8, §D14) the threat model for "client uploads 50 GB and fills the
disk" is *the owner of the disk did it to themselves*; the correct
response is UI feedback (free-space indicator) rather than server
refusal.

**Consequences.**
- Upload endpoints stream to disk rather than buffer in memory; a
  naïve implementation would exhaust RAM on large video files. The
  attachment upload handler MUST use chunked streaming.
- OpenAPI does not advertise a `maxLength` or size header contract —
  a future cap would be additive (a new 413 response, not a breaking
  change to the success path).
- If we later expose the API to multi-tenant scenarios (deferred per
  D8), this decision is revisited: shared disk demands quota.
- The JSON contract still reports `size` on every attachment, so
  clients can display "4.2 GB" in a UI and make their own judgment.

---

## D22 — EquipmentKind is a closed enum

**Decision.** The `<kind>` element on an equipment XML file is
restricted by the XSD to exactly four values: `container`, `canopy`,
`reserve`, `aad`. The Pydantic model enforces the same set.

**Why.** Mistyped kinds silently create a new category of equipment
in dashboards and reports — a jumper with `aadd` on one AAD sees their
reserve-repack reminder logic break and can't tell why. Closed
validation at both the schema and model layer forces the typo to
surface at write time with a clear error. The four kinds cover the
entire mainstream sport-skydiving stack; adding one (e.g. a helmet-
mounted audible altimeter as `audible`) is an additive change within
v1 per D18 — files written before the enum expansion remain valid
because they only use the existing values.

**Consequences.**
- Adding a new kind is a two-line change in both the XSD and
  `EquipmentKind` / `Equipment.kind` definitions, plus a DECISIONS
  note and a migration test proving old files still validate.
- Removing or renaming a kind is a breaking change that requires a
  new schema namespace (`/schema/v2`) per D18.
- A future "freeform user-defined gear" feature (e.g. skydiving
  cameras, altimeters) ships as a separate `<accessory>` element
  rather than by opening `EquipmentKind`.

---

## D23 — `jump_number` is unique within a logbook; collisions are 409, not auto-resolved

**Decision.** Within a single logbook folder, `<jump_number>` is
unique across all `jump.xml` files. No two jumps may share the same
jump number. The invariant is enforced in two places on every write:

1. **Service layer (first line).** `create_jump` and any `update_jump`
   that changes `jump_number` query the SQLite index for the target
   number. A hit raises `JumpNumberConflict`, which surfaces as a
   `409 Conflict` response with RFC 9457 body:
   ```json
   {
     "type": "about:blank",
     "title": "Conflict",
     "status": 409,
     "detail": "jump_number 851 is already in use",
     "code": "jump_number_conflict",
     "request_id": "…",
     "errors": [{"pointer": "#/jump_number", "detail": "already in use"}]
   }
   ```
2. **Filesystem (backstop).** Before creating the jump folder, the
   service scans `jumps/` for any entry whose name equals `[<jump#>]`
   or starts with `[<jump#>] ` (bracket-number-space prefix). A hit
   is translated to the same `JumpNumberConflict`. This prefix-scan
   backstop exists because SQLite is not authoritative (D3): the
   index can drift, and a service-layer-only check is unsound if
   the index is stale or the user just restored from backup. After
   the scan passes, the service creates the folder with
   `os.mkdir(exist_ok=False)` so a race between the scan and the
   create still fails loudly rather than silently overwriting.

   Note (2026-04-23): before D4's folder-name revision to
   `[<jump#>] <title>`, this backstop relied purely on
   `mkdir(exist_ok=False)` catching identical names. Title makes
   folder names vary for the same jump number, so the backstop had
   to tighten to a prefix scan. Mechanism identical in intent — the
   pre-flight check now happens in the service layer rather than
   the kernel. The service translates this to the same
   `JumpNumberConflict`. This backstop exists because SQLite is not
   authoritative (D3) — the index can drift, and a service-layer-only
   check is unsound if the index is stale or the user just restored
   from backup.

**Renames.** A `jump_number` correction goes through the same two
checks against the *new* number before `os.rename(old_folder,
new_folder)`. Rename is atomic on POSIX and on Windows same-volume
(`MoveFileExW`, per D10 caveats). Index upsert follows the rename; if
the rename succeeds but the index update fails, `reindex` is the
authoritative repair.

**Corruption case — two XMLs on disk claim the same `jump_number`.**
Cannot happen via the write path, but may happen via manual edit,
a cloud-sync conflict, or a restored backup that overlaps live data:

- `verify` flags it (exit code 1) and prints both UUIDs and folder
  paths.
- `reindex` **refuses to complete** and surfaces the duplicate pair
  to the user. The app does not auto-pick a winner — the user must
  resolve manually (edit one XML, move the other to `.trash/`, or
  delete). The trade-off: auto-resolve is tempting but silently
  discarding user data is worse than a loud stop.

**Why.** `jump_number` is the skydiver's canonical sequence; "what
jump was that?" is the first thing any logbook has to answer. A
duplicate breaks that foundational query, every stats calculation
that uses it as a primary key, and every PDF printout. Two layers of
enforcement — index plus filesystem — give us a real guarantee even
when one layer is stale. Refusing to auto-resolve is the D3 + D10
posture applied to integrity: we don't silently mutate user data to
satisfy an invariant; we stop and ask.

**Monotonic default (soft expectation, not an invariant).** In the
overwhelming common case, new jumps are entered in chronological
order: jump 851, then 852, then 853. Backfills (a forgotten #500
discovered years later) and imports are legitimate and allowed —
the invariant is only uniqueness, not monotonicity. Supporting the
common case without forbidding the edge case:

- **Default source:** `GET /api/v1/stats` (already in D14 §4 scope)
  returns `next_jump_number = max(jump_number) + 1`, or `1` on an
  empty logbook. Clients prefill the "new jump" form from this
  single stats call they already make for the dashboard. We
  deliberately **do not** introduce a dedicated
  `/api/v1/jumps/next-number` endpoint — it would be one-field
  duplication of a trivially-computed value already in stats, at
  the cost of a new route, test, and OpenAPI entry. If profiling
  later shows stats is too heavy for CLI-shaped callers, a
  dedicated endpoint is a purely-additive change.
- **POST contract:** `jump_number` is **required** on `POST
  /api/v1/jumps`. The server never auto-assigns; the client always
  sends an explicit number, usually the stats-provided default,
  which the user may have overridden. This keeps the contract
  symmetric (same field required on input and returned on output)
  and matches the D23 stance that the user deliberately chose this
  number.
- **Non-sequential is allowed.** If the supplied number is not
  `max + 1`, no error is raised. The UI may surface a "are you
  sure?" affordance by consuming the advisory-hints channel defined
  in D24 (emitting `code: "non_sequential_jump_number"`). Backfills
  are legitimate and never rejected.

**Alternatives considered.**
- *Auto-renumber on collision* (insert as "852a" or shift everything
  up by one): rejected. Silently mutating user-entered numbers
  destroys data the user may have chosen deliberately (e.g. matching
  a paper logbook). Being loud about the conflict is correct.
- *Filesystem-only enforcement* (rely on `mkdir(exist_ok=False)`):
  rejected. Under the current D4 naming `[<jump#>] <title>/`, two
  jumps with the same number but different titles would not collide
  at the bare-mkdir level — kernel-level `exist_ok=False` catches
  only identical names. The service-layer prefix scan (see
  §Filesystem backstop above) is what makes the disk-level check
  sound; the index check is the fast common-case line of defense.
- *Index-only enforcement*: rejected. Index is rebuildable from XML
  (D3), which means the index can be absent, stale, or mid-rebuild.
  Filesystem backstop is what makes the invariant real.
- *Compound key `(user_id, jump_number)`*: partially deferred. D8
  already keeps `user_id` as a service-layer parameter; uniqueness
  today is per-logbook, which is effectively per-user in v0.1. When
  multi-user lands (deferred per §Deferred), uniqueness becomes
  per-user and the scope of this D-entry expands at that point.

**Consequences.**
- Pydantic `Jump.jump_number` is a `PositiveInt` (>= 1).
- The SQLite index places a `UNIQUE` constraint on `jump_number`
  (and, when D8 comes into force, on `(user_id, jump_number)`). The
  index schema version bumps per D26 when this changes.
- The XSD `SCHEMA.v1.xsd` keeps `<jump_number>` typed as
  `xs:positiveInteger`; uniqueness across files is not expressible in
  XSD 1.0, so it lives in the service layer and the index, not the
  schema.
- `verify` grows a duplicate-jump-number check; `reindex` grows the
  refuse-on-duplicate-detected behaviour. Both are in-scope for v0.1.
- Error taxonomy (D16): one code `jump_number_conflict` for both
  create and rename paths. The client only needs to know "pick a
  different number."

---

## D24 — Advisory hints channel (`_hints`) on write responses

**Decision.** Write responses (`POST`, `PUT`, and any `DELETE` that
returns a body) MAY include a top-level `_hints` array alongside the
resource fields. The field is optional; absent or empty means no
hints. Read responses (`GET`) do not carry `_hints`. Error responses
(4xx/5xx) do not carry `_hints` — those use RFC 9457 `problem+json`
exclusively (D16).

Shape of each hint:

```json
{
  "code": "non_sequential_jump_number",
  "detail": "Jumps 847-850 are missing before 851.",
  "pointer": "#/jump_number"
}
```

- `code` (required, string) — stable machine-readable identifier.
  Open set. Clients branch on `code`, not `detail`.
- `detail` (required, string) — human-readable explanation for this
  occurrence. Safe to display verbatim.
- `pointer` (optional, string) — RFC 6901 JSON Pointer into the
  request body, when a hint is tied to a specific field. Mirrors the
  `errors[].pointer` convention from D16.

**Why a separate channel, and why on the response body.**

Some conditions are not errors but are worth the user knowing about:
a non-sequential jump number, an AAD whose service interval is
within 30 days of expiry, a reserve repack older than 180 days. If
we overload RFC 9457 for these, we violate the RFC's semantic (it is
specifically for 4xx/5xx). If we carry them in HTTP response headers
(e.g. `Warning:`, which is deprecated in RFC 9111 anyway), structured
data gets awkward to parse and most clients won't notice.

A top-level response field on write endpoints is the minimum-weight
place to put them: no envelope, no extra round-trip, discoverable in
OpenAPI, and clients that ignore the field are unaffected.

**Why `_hints` (underscore-prefixed).** The field name is namespaced
to avoid collision with resource fields now or in the future. A future
`Jump` or `Equipment` field named `hints` (e.g. user-authored gear
hints) would otherwise clash. Underscore is a weak convention for
"meta/system" fields and works cleanly in JSON.

**Why only on write responses.** Read responses represent the state
of the resource; hints are "about the write that just happened." On
a read, the equivalent concept is dashboards, health warnings, or
stats — those belong in their own endpoints (D14 §4 covers the
dashboard slot), not sprinkled across every GET.

**Client obligations.** None. A client MAY ignore `_hints` entirely.
The server MUST NOT rely on the client doing anything with them.
Hints are strictly informational; a missed hint never degrades
correctness.

**Server obligations.**

- Hints are only generated for conditions documented in this file
  or in a subsequent D-entry that introduces a new code.
- Generating a hint must not fail the write. If the hint generator
  raises, the handler logs it and omits the hint; the successful
  response still returns the resource.
- Hints are not persisted. They are computed at response time from
  the same index reads the service already performs; no extra DB
  calls should be needed for the hints we plan to emit in v0.1.

**v0.1 hint codes (open set, extended by later D-entries):**

- `non_sequential_jump_number` — emitted by `POST /api/v1/jumps` when
  the supplied `jump_number` is not `max + 1` in the index. Points
  to `#/jump_number`. Relevant to D23. **This is the only hint code
  emitted in v0.1.**

**Reserved for v0.2 (name fixed, emission deferred):**

- `equipment_service_due_soon` — will be emitted by `POST
  /api/v1/jumps` when any linked equipment's `aad_service_due` or
  `last_reserve_repack + 180d` falls within 30 days of the jump
  date. Points to `#/equipment_refs/<index>`. The code name is
  reserved now so v0.1 clients don't accidentally use it for
  something else; the service-layer logic (resolve each linked
  equipment, compute the window, emit the hint) is additional work
  that we deliberately keep out of the create_jump slice to respect
  the "master the basics" working rule. When v0.2 adds it, no
  contract change is needed — the channel is already in place.

Adding a new hint code is a documentation change (append to this
D-entry or a successor) and a small service-layer change. No schema
version bump is needed — the `_hints` contract is additive and
clients that don't recognize a code simply display `detail`.

**Alternatives considered.**

- *Overload RFC 9457 on 2xx responses.* Rejected: RFC 9457 §1 is
  explicit that problem+json is for HTTP error responses. A
  successful write is not an error.
- *HTTP `Warning` header.* Deprecated in RFC 9111 (§5.5 removes it),
  and parsing structured JSON out of a header is awkward. Dead end.
- *JSON:API-style envelope `{"data": ..., "meta": {"hints": [...]}}`.*
  Rejected: we've already committed to flat resource bodies
  everywhere else. Introducing an envelope only for writes creates
  shape asymmetry between GET and POST that every client has to
  learn and branch on.
- *Separate GET endpoint for warnings.* Rejected: "warnings about
  the write I just did" needs to arrive in the write response. A
  follow-up GET is a round trip the UI can't easily sequence.

**Consequences.**

- Pydantic response models for writes wrap the resource in a thin
  subclass that adds `_hints: list[Hint] = []`. Pydantic's alias
  config renders `_hints` verbatim despite the underscore.
- OpenAPI schema adds a `Hint` component and references it from
  write response schemas. The `Hint` component is authored next to
  `ProblemDetails` in `backend/api/openapi.py`.
- `backend/api/errors.py` gains a small `build_hint(code, detail,
  pointer=None) -> dict` helper, symmetric with `build_problem`.
- Test coverage: one test per hint code (present when the condition
  is met, absent when it isn't), plus a contract test that read
  responses never include `_hints`.

---

## D25 — Crash semantics for multi-file writes: XML is truth, manifest is derived

**Decision.** A jump folder is **valid** iff a single condition
holds: `jump.xml` exists, parses through the hardened parser (D2),
and validates against the XSD declared by its `<schema_version>`.
Everything else in the folder is either an attachment listed and
hashed by `jump.xml`, the derived `SHA256SUMS` manifest, or the
derived `summary.md`. Absence of any non-XML file is *recoverable*,
not *corrupting*.

This is D3 ("SQLite is an index, rebuildable from the XML") applied
at the per-folder level: the folder's source of truth is `jump.xml`;
everything else is rebuildable from it.

**Write ordering — `create_jump`.**

1. `os.mkdir(folder, exist_ok=False)` — the D23 uniqueness backstop.
2. For each attachment, in a single streaming pass over the
   client-provided bytes: compute its SHA-256 and `atomic_write` it
   (D10). The hash and the bytes are committed together — they agree
   by construction.
3. `atomic_write` `jump.xml`. It includes `<attachment>/<sha256>`
   for every attachment written in step 2.
4. `atomic_write` `SHA256SUMS`. Content is derived by hashing every
   file produced in steps 2–3 (the existing
   `manifest.generate(folder)` is appropriate here; bytes just
   written are authoritative).
5. `atomic_write` `summary.md` (derived, non-authoritative per D5;
   absence is never an error).

**Write ordering — `update_jump`.**

1. For each *new* attachment: `atomic_write` bytes (same as create).
2. `atomic_write` the new `jump.xml`. It references the final
   desired attachment set and carries the correct `<sha256>` for
   each.
3. `atomic_write` the new `SHA256SUMS`.
4. *Last:* delete any attachments the new `jump.xml` no longer
   references. Only after the new jump.xml is durable on disk.

The "delete last" rule prevents the state where `jump.xml`
references an attachment that has already been deleted. After the
new jump.xml is durable, removed attachments become orphans until
step 4 completes — orphans are detectable and recoverable, dangling
references are not.

**Crash states — what's on disk, what the app does on next open.**

| Crash point                                         | State                                              | Handling on next open                                                                                 |
|-----------------------------------------------------|----------------------------------------------------|-------------------------------------------------------------------------------------------------------|
| Before step 1 (mkdir)                               | Nothing                                            | N/A                                                                                                   |
| After mkdir, before jump.xml                        | Folder may contain attachments, no jump.xml        | Folder is **not a valid jump**. `reindex` ignores it; `verify` reports "incomplete folder". Manual cleanup. |
| After jump.xml, before SHA256SUMS                   | Valid jump.xml, stale/missing SHA256SUMS           | `folder_reconcile` regenerates SHA256SUMS **from jump.xml's claims** (see below). Logged at INFO.     |
| After SHA256SUMS, before summary.md                 | Fully valid, no summary                            | `summary.md` regenerated lazily on next read (D5).                                                    |
| update_jump, after new jump.xml, before delete step | Valid new jump.xml, old-attachment orphans on disk | `verify` reports orphans. v0.1: no auto-cleanup; v0.2 may add a `--repair` flag.                      |

**Invariant.** After any crash, either the folder is coherent
(valid `jump.xml`, its claims resolve to files that exist) or the
folder is recognizably mid-write (no `jump.xml` or an invalid one).
There is no silent-corruption state the app cannot detect.

**On-open reconciliation (`folder_reconcile`, per-folder, read path).**

Cheap and non-byte-reading:

1. Parse `jump.xml` through the hardened parser.
2. Validate against the XSD for its `<schema_version>`.
3. If `SHA256SUMS` is missing *or* its parsed content does not match
   `jump.xml`'s claims (same filenames, same hashes, plus the
   computed hash of `jump.xml` itself), **regenerate it from
   `jump.xml`'s claims**. Use a new helper
   `manifest.from_jump_xml()`, **not** `manifest.generate()`.

Critical distinction, easy to get wrong and worth locking down:

- `manifest.generate(folder)` *reads bytes from disk and hashes
  them*. Correct for the initial-write path only — bytes were just
  written, jump.xml claims were derived from those same bytes, no
  divergence possible at that point.
- `manifest.from_jump_xml(folder)` *reads jump.xml and emits the
  manifest from its claimed hashes, plus a freshly-computed hash of
  jump.xml itself*. Correct for the recovery path. If attachment
  bytes have silently rotted, `generate()` would "successfully"
  produce a manifest matching the rotted bytes, blessing the
  corruption and erasing the jump.xml claim as evidence. The
  claim-based regeneration preserves jump.xml as the authoritative
  witness.

Attachment byte integrity is **not** checked on open. That is a
`verify` concern. Opening a jump trusts `jump.xml`'s claimed hashes
as the authoritative record of what the bytes ought to be. This
keeps the open path O(1) on attachment size and keeps a stale
manifest from masquerading as a false-positive integrity alarm.

**`verify` command — in-scope per D14.**

The only operation that performs byte-level integrity checking, on
demand:

1. Walk `jumps/*/` folders.
2. For each, parse+validate `jump.xml`. If that fails, report the
   folder as "invalid"; continue.
3. For each `<attachment>`, re-hash the file on disk and compare to
   `<sha256>` in jump.xml. Report mismatches.
4. Parse `SHA256SUMS`; compare to what `manifest.from_jump_xml()`
   would produce. Report any disagreement (stale manifest).
5. List orphan files (present in folder, not referenced by jump.xml,
   not in the always-excluded set).
6. Report duplicate `<jump_number>` across folders (D23).

Exit 0 when clean, exit 1 on any issues. Issues to stderr, summary
to stdout.

**`reindex` command — in-scope per D14.**

1. Walk `jumps/*/` folders.
2. Skip any folder without a valid `jump.xml` (log INFO; defer to
   `verify` for diagnosis).
3. For each valid folder, trigger `folder_reconcile` (so a crashed
   write's manifest is repaired during reindex), then upsert into
   the SQLite index.
4. Abort with a clear error if two folders claim the same
   `<jump_number>` (D23).
5. On success, set `PRAGMA user_version` to the current schema
   version (D26).

**Directory fsync — known caveat, not a guarantee.**

`atomic_write` fsyncs the tmp file before `os.replace` but does not
fsync the parent directory. On ext4 (default `data=ordered`), APFS,
and NTFS in normal configurations this is durable enough under
kernel crashes and power loss in practice, per each OS's rename
semantics. On exotic mount options (`data=writeback`), some network
filesystems, and some FUSE backends, a rename may not be visible
after a crash even though the tmp bytes are durable.

We accept this risk for v0.1 because:

- The worst crash outcome is a *lost* update (old `jump.xml`
  remains), not a torn write — D3's "XML is truth" still holds.
- Exotic filesystems are outside v0.1's supported configurations.
- Directory fsync is an additive hardening step that does not
  change the contract; it can land in v0.2 if we see a real
  incident.

**Alternatives considered.**

- *Manifest first, XML second.* Rejected: the manifest would claim
  integrity for files that don't yet exist. On crash, the folder
  contains a manifest with hashes of absent or future files — harder
  to reason about than "no valid jump.xml = mid-write."
- *Per-folder transaction via shadow folder + final atomic rename.*
  The whole folder becomes one atomic unit. Rejected for v0.1:
  cross-file atomicity isn't a filesystem primitive; emulating it
  means writing everything to `folder.tmp/` and renaming at the
  end. Works, but the "XML-is-truth, manifest-is-derived" model
  handles every realistic crash case without the extra machinery.
  Revisitable if we ever need same-folder multi-file atomicity
  (e.g., multi-jump batch operations).
- *Regenerate SHA256SUMS from disk bytes on open.* Rejected:
  corrupted attachment bytes would be silently blessed by a
  regenerated manifest. Claim-based regeneration keeps jump.xml as
  the authoritative witness.
- *Force directory fsync after every write.* Rejected for v0.1:
  latency hit, and the failure mode is rare on supported
  filesystems. Hardening later is non-breaking.

**Consequences.**

- `backend/storage/manifest.py` grows
  `from_jump_xml(folder: Path) -> bytes`, which reads `jump.xml`,
  parses attachments, and emits a manifest from the claimed hashes
  plus the freshly-computed hash of `jump.xml` itself. The existing
  `generate()` is kept as-is; its docstring is updated to say "use
  on the write path only; use `from_jump_xml()` for recovery."
- `backend/storage/` grows `folder_reconcile(folder: Path) -> None`,
  idempotent and cheap (no attachment byte reads), implementing the
  on-open recovery logic above.
- `backend/services/jump_service.py` `create_jump` follows the
  ordering above. Crash-path tests use a subprocess harness: write
  N steps in a child process, `SIGKILL` after step N, reopen in the
  parent, assert the reconciled state matches the crash-states
  table. One test per row.
- `backend/scripts/verify.py` and `backend/scripts/reindex.py`
  implement the `verify` / `reindex` contracts above. They call
  service-layer helpers; the script layer stays thin (D7).
- Linting/review rule: `manifest.generate(folder)` is only ever
  called from the write path. Any call site in a recovery or open
  path is a bug and should fail code review.

---

## D26 — SQLite index schema versioning via `PRAGMA user_version` + drop-and-reindex

**Decision.** The SQLite index carries a single schema version
stored in `PRAGMA user_version`. When the schema definition
(`_SCHEMA` in `backend/storage/index.py`) changes in any way that
affects reads or writes, we bump the module-level constant
`INDEX_SCHEMA_VERSION`. On `open_index`:

1. If `user_version == 0` → fresh database. Execute `_SCHEMA`, set
   `user_version = INDEX_SCHEMA_VERSION`. Report as fresh.
2. If `user_version == INDEX_SCHEMA_VERSION` → no-op. Report OK.
3. Otherwise (any mismatch, either a newer or older version on
   disk) → **drop every user table, re-execute `_SCHEMA`, set
   `user_version = INDEX_SCHEMA_VERSION`**. Report that the schema
   was rebuilt; the caller's responsibility to trigger reindex.

We do not use `ALTER TABLE`. We do not maintain numbered migration
scripts.

**Why drop-and-reindex, not ALTER TABLE.**

- The SQLite index is rebuildable from XML (D3). Cost of a rebuild
  is O(jumps) — a skydiver with 10,000 jumps sees a few seconds of
  file walk and parse at startup. Cost of maintaining a linear
  history of migration scripts forever is an indefinite engineering
  tax for zero user-visible benefit.
- SQLite's `ALTER TABLE` is limited. Rename and add-column have
  been fine since 3.25, but dropping a column requires recreating
  the table and copying data — which is exactly what drop-and-
  reindex does anyway, more reliably, from the authoritative source.
- Downgrades work for free. If the user moves from v0.2 back to
  v0.1, the v0.2-written index sees a higher `user_version`, v0.1
  drops and rebuilds to the v0.1 schema. No bidirectional migration
  code.

**What counts as a schema change (bump the constant).**

- Adding or removing a table.
- Adding or removing a column.
- Changing a column's type, nullability, default, or constraint.
- Adding or removing a `UNIQUE` constraint. (This is how D23's
  `jump_number` uniqueness lands — adding `UNIQUE` on
  `(user_id, jump_number)` to the `jumps` table bumps the
  version.)
- Adding or removing an index that queries depend on.
- Any `PRAGMA` that changes the on-disk format (none today; noted
  for completeness).

What does **not** count:

- Pure query code in service modules.
- Fixing a comment inside `_SCHEMA`.
- `PRAGMA` settings that don't change the on-disk format
  (`journal_mode`, `synchronous`, `foreign_keys`).

**Mechanics.** `open_index` returns a small result object so the
caller can distinguish "nothing happened" from "tables were just
dropped":

```python
@dataclass(frozen=True)
class IndexOpenResult:
    conn: sqlite3.Connection
    schema_was_rebuilt: bool   # True if tables were dropped and recreated
    previous_version: int      # 0 on fresh, N > 0 on rebuild-from-N

def open_index(logbook_root: Path) -> IndexOpenResult: ...
```

Callers branch on `schema_was_rebuilt`:

- **REST app startup (`backend/main.py`)**: if True, run reindex
  synchronously before accepting requests. If reindex fails,
  refuse to start with a clear error.
- **`scripts/reindex.py`**: ignores `schema_was_rebuilt` (it is
  about to reindex regardless).
- **`scripts/verify.py`**: if True and reindex hasn't yet run,
  refuses to proceed (or forces a reindex first). A verify pass
  against empty tables would false-positive as "clean."

**Dropping tables safely.**

```python
for row in conn.execute(
    "SELECT name FROM sqlite_master "
    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
).fetchall():
    conn.execute(f"DROP TABLE IF EXISTS {row['name']}")
```

Table names come from `sqlite_master`, not user input, so the
string interpolation is safe. Dropping a table drops its indexes
and triggers automatically. If we later add views, this query
widens to include `type IN ('table', 'view')`.

**Concurrency with the single-instance lock (D9).**

Schema rebuild happens inside the D9 lock. Two app instances
cannot race on `PRAGMA user_version` because the second instance
never starts. No additional coordination needed.

**Alternatives considered.**

- *Numbered migration scripts (Alembic-style).* Rejected: the
  index is rebuildable, so accumulating migration history is pure
  overhead. Revisit if D3's "index is rebuildable" ever stops
  holding.
- *Use SQLite's built-in `PRAGMA schema_version`.* Rejected:
  `schema_version` is an internal counter SQLite increments on
  every DDL, including `CREATE TABLE IF NOT EXISTS` no-ops. It is
  not a stable marker for app-owned schema intent. `user_version`
  is designed for exactly this.
- *Write a sentinel row into a `meta` table.* Rejected: adds a
  table just to hold an integer SQLite already gives us a slot
  for.
- *Leave `CREATE TABLE IF NOT EXISTS` alone, no versioning.*
  Current behavior. Rejected: silently keeps stale schemas in
  place on upgrade. A user who upgrades to a new column gets
  broken queries with no error, and the bug is indistinguishable
  from a buggy query. D26 exists to fix this.

**Consequences.**

- `backend/storage/index.py` gains:
  - `INDEX_SCHEMA_VERSION: int = 1` (the current schema).
  - An `IndexOpenResult` dataclass.
  - A rewritten `open_index` that returns `IndexOpenResult`,
    implements the three-branch logic, and keeps the existing
    `PRAGMA journal_mode = WAL` and `synchronous = NORMAL`
    settings.
- `open_index`'s signature change is a breaking change to an
  as-yet-uncalled function. No migration work is needed for
  existing callers because there are none.
- `backend/main.py` app-startup code branches on
  `result.schema_was_rebuilt` to trigger `reindex_from_xml`. Reindex
  contract comes from D25.
- `backend/scripts/verify.py` refuses to run on a rebuilt-but-not-
  reindexed DB.
- When D23's uniqueness constraint is actually added to `_SCHEMA`
  (currently the schema has a non-unique `idx_jumps_user_number`),
  `INDEX_SCHEMA_VERSION` bumps from 1 to 2. That same commit adds a
  test that an index built at v1 opens cleanly, drops, rebuilds to
  v2, and reindexes from XML.
- Test coverage:
  - Fresh DB: `previous_version == 0`, `schema_was_rebuilt == False`
    (fresh is not a rebuild), tables present.
  - Open current-version DB: `schema_was_rebuilt == False`.
  - Open older-version DB: `schema_was_rebuilt == True`,
    `previous_version` reflects prior, tables empty of user data.
  - End-to-end: open older-version DB → rebuild → reindex → rows
    back, matching XML.

---

## D27 — Structured JSON logging with request_id correlation

**Decision.** The backend emits structured logs as one JSON object per
line (JSON Lines, https://jsonlines.org), UTF-8, to stderr. Every log
record carries the same `request_id` UUIDv4 that D16 publishes in the
`X-Request-Id` response header and the `problem+json` body, so a log
line, a response header, and an error body can be pivoted by a single
field.

**Record shape.** Reserved top-level keys — callers MUST NOT override
these via `extra=`:

```json
{
  "timestamp":  "2026-04-23T19:03:17.412Z",
  "level":      "INFO",
  "logger":     "backend.api.rest",
  "message":    "http_request",
  "request_id": "a1b2c3d4-...",
  "exception":  "Traceback (most recent call last): ...\\n..."
}
```

- `timestamp` — ISO 8601 UTC with millisecond precision and a `Z` suffix.
  Matches D17's audit-timestamp convention so log and DB timestamps sort
  together.
- `level` — `record.levelname` uppercased: `DEBUG` / `INFO` / `WARNING`
  / `ERROR` / `CRITICAL`.
- `logger` — dotted logger name, e.g. `backend.services.jump_service`,
  `uvicorn.error`. Lets ops filter by source.
- `message` — the already-formatted message string.
- `request_id` — UUIDv4 string, or the JSON value `null` when the
  record is emitted outside any HTTP request (startup, shutdown, CLI
  scripts, tests).
- `exception` — present only when `exc_info` was supplied. The full
  traceback as a single string, newlines preserved but *inside* the
  JSON string value — the line itself still contains exactly one
  newline at the end so `jq -c` remains happy.

Any keys passed via `logger.info("msg", extra={...})` land as top-level
siblings provided they don't collide with a reserved key. A collision
raises at format time (server bug, fail loudly). This mirrors how D16
treats `ServiceError(**details)` colliding with RFC 9457 standard
members.

Additions to the reserved set are a breaking change for anyone grep'ing
our logs. Treat this field list the same way we treat D16's extension
names: additive is fine, renaming isn't.

**Two named events** carry extra structure because they're load-bearing
for correlating errors to requests:

- `http_request` — emitted by the correlation middleware after the
  response is built. Fields: `method`, `path`, `status`, `duration_ms`
  (float, 3 dp). No query string (may contain user data), no client IP
  (PII, and we bind loopback-only by default per D20).
- `service_error` — emitted by the FastAPI exception handler whenever
  a `ServiceError` is converted to a problem+json response. Fields:
  `code`, `http_status`. Lets an operator pivot from a log line to the
  exact error body the client received.

**Why JSON Lines + stdlib `logging`.** JSON Lines is grep- and
jq-friendly, loads cleanly into any log platform, and survives
truncation (a partial line at EOF just gets discarded). Python's
stdlib `logging` already has the hooks we need — `Formatter`,
`LogRecord.__dict__` allowlisting, `ContextVar` for
per-request state — and does not add a dependency. Consistent with the
lean `pyproject.toml` (D15).

**Request-ID transport: pure ASGI middleware, not `BaseHTTPMiddleware`.**
Today the correlation lives in `backend/api/rest.py` as
`@app.middleware("http")`, which is `starlette.middleware.base.BaseHTTPMiddleware`.
D27 replaces it with a hand-written pure ASGI middleware class
(`CorrelationIdMiddleware` in `backend/observability/logging.py`)
because:

- `BaseHTTPMiddleware` runs the inner ASGI app inside a spawned task
  group. `ContextVar` values set in the endpoint or service layer do
  not propagate back out to the middleware's `finally` block. See
  Starlette discussion #1729 (known limitations) and issue #1678
  (active deprecation proposal). One-way propagation (middleware →
  endpoint) works today because the spawned task copies the parent
  context at creation; the other direction, which future log-binding
  work (e.g. "bind `jumper_id` inside a route") would need, is broken.
- The reference implementation for this exact problem,
  `snok/asgi-correlation-id`, uses pure ASGI middleware for the same
  reasons. Its `middleware.py` sets the contextvar in the outer ASGI
  scope and wraps `send` to attach the header.
- Switching now is ~20 lines and one middleware. Switching later, after
  services have started binding additional context, would be a
  coordinated rewrite.

The new middleware mints a fresh UUIDv4 per request. It does **not**
accept an inbound `X-Request-Id` header in v0.1 — preserving the
current behaviour. Accepting inbound correlation IDs is a safe
additive change (validate as UUIDv4, fall back to minting) we can
land later if a deployment scenario wants it.

**Uvicorn integration.** Two flags in `uvicorn.run`:

- `log_config=None` — uvicorn skips installing its own `dictConfig`,
  so our root-logger setup stands. Uvicorn's own `uvicorn` and
  `uvicorn.error` loggers still propagate to root, so startup and
  shutdown messages land in the JSON stream.
- `access_log=False` — uvicorn's `uvicorn.access` logger is silenced.
  Its `LogRecord.args` shape is uvicorn-specific (client addr,
  request line, status) and bolting a generic JSON formatter onto it
  forces per-field special casing. Our middleware emits `http_request`
  instead, with contextvar visibility guaranteed because it runs in
  the same task.

**Destination: stderr only in v0.1.** No file sink. Reasoning:

- Until the pywebview packaging work lands (D11, deferred), the REST
  API runs from a terminal and stderr is visible. Bug reports can
  be copy-pasted.
- Adding a file sink now means picking a path (D20's
  `user_config_dir()` or inside `logbook_root`), a rotation size,
  and inheriting the decade of Windows-file-server bugs the Python
  tracker has filed against `RotatingFileHandler`. None of this is
  value-creating yet.
- Adding a file sink later is additive: attach a second handler
  (same formatter, same filter) to the root logger. No shape
  change, no D27 revision needed. When packaging lands, logs go to
  `user_config_dir() / "logs" / "skydive-logbook.log"`, **not**
  inside `logbook_root/` — logs are app-level debug output, not
  user-facing logbook data, and polluting `logbook_root/` would
  violate D2's "anyone with a text editor and an XSD validator
  should be able to read and verify it."

**Configurability.** `Settings` (D20) gains one field:
`log_level: str = "INFO"`, env var `SKYDIVE_LOG_LEVEL`. No log-format
toggle — the format *is* the contract.

**Alternatives considered.**

- *structlog.* Rejected for v0.1. Its bound-logger model is genuinely
  nice but the stdlib already covers our needs, and the project has
  been deliberate about dependency weight (see D15, plus the lean
  `pyproject.toml`). Reconsider if we ever want structured key/value
  binding inside services that outgrows `extra=`.
- *python-json-logger.* Rejected. Same tradeoff as structlog but with
  fewer features — no reason to take a dep for something that's two
  dozen lines in `logging.Formatter`.
- *Keep `BaseHTTPMiddleware`.* Rejected per the rationale above. The
  downward-only contextvar propagation works for today's narrow case
  but blocks any future route-level binding without another rewrite.
- *Re-route `uvicorn.access` through our formatter.* Rejected. The
  special-case parsing of `record.args` is fragile across uvicorn
  versions, and emitting our own `http_request` is simpler and puts
  the event squarely inside our middleware's contextvar scope.
- *Accept inbound `X-Request-Id` now.* Deferred, not rejected. Easy
  to add when a use case (reverse proxy, cross-service tracing)
  appears.
- *Ship a rotating file sink in v0.1.* Rejected per scope discipline
  (D14). Becomes free with packaging (D11).
- *Nested extras under a fixed key like `"tags": {...}"` or `"data"`.*
  Rejected. Flat top-level siblings match D16's problem+json extension
  style and make `jq '.request_id'` work the same across bodies and
  logs.

**Consequences.**

- New module: `backend/observability/logging.py` exporting
  `JsonFormatter`, `request_id_var: ContextVar[UUID | None]`,
  `CorrelationIdMiddleware`, and `configure_logging(level: str) -> None`.
- `backend/api/rest.py` drops the `@app.middleware("http")` block and
  adds `app.add_middleware(CorrelationIdMiddleware)` instead.
  `on_service_error` emits a `service_error` log record before
  returning the problem+json response.
- `backend/main.py` calls `configure_logging(settings.log_level)`
  before `uvicorn.run(..., access_log=False, log_config=None)`.
- `backend/config.py` gains `log_level: str = "INFO"`.
- Public contract additions: the log record field set above, the
  `http_request` and `service_error` event shapes, and the `X-Request-Id`
  minting behaviour. All additive relative to D16.
- Test coverage:
  - `JsonFormatter` — reserved fields present and typed correctly;
    `extra=` kwargs passed through; reserved-key collision in `extra=`
    raises; `exc_info` renders as an `exception` string field;
    `ensure_ascii=False` so non-ASCII characters survive (matches
    D4's NFC discipline on the storage side).
  - `request_id_var` — `None` outside a request; a UUIDv4 from inside
    the middleware; reset on the way out (a record emitted after the
    middleware exits gets `null` again).
  - Middleware integration — `TestClient` GET: `http_request` record
    emitted once, `request_id` field equals `X-Request-Id` header
    equals `problem+json` body `request_id` on an error route.
  - Uvicorn integration — no duplicate handler output; `uvicorn.error`
    records on startup render as JSON.

---

## D28 — Config loading precedence and error handling

**Decision.** `Settings` loads values from four sources, applied in the
following priority (highest wins):

1. `init` kwargs — `Settings(bind_port=9000)` in code.
2. Environment variables — `SKYDIVE_`-prefixed, e.g. `SKYDIVE_API_KEY`.
3. `user_config_dir() / "config.toml"` — the TOML file D20 pinned.
4. Defaults on the model.

A **missing** TOML file is normal: defaults + env cover everything, the
first-run flow will create the file when the user picks a logbook
folder. A **malformed** TOML file (syntax error, wrong type for a field,
value outside `Field(ge=..., le=...)` range) raises on `Settings()`
construction — `tomllib.TOMLDecodeError` or `pydantic.ValidationError`
respectively — and the server never boots.

Unknown keys in the file are **ignored** (`extra="ignore"`). An older
binary reading a newer file, or a user's typo, does not crash.
Tightening this to `extra="forbid"` would require a new D-entry.

**Why this precedence.** Env > file lets an operator override a stale
value for a single run without editing the file, and keeps secrets
(`api_key` especially) out of plaintext disk storage for deployments
that prefer env-based secret injection. Init-kwargs > env gives tests
and the future CLI-arg slice a deterministic override channel without
touching the environment. These are the pydantic-settings library
defaults; the only custom piece is inserting the TOML source between
env and defaults.

**Why fail loudly on malformed files.** A silent fallback to defaults
would let a typo (e.g. `logbook_root = "~/NewLogboook"` with a triple-o)
silently point the app at a different folder and start writing jumps
there. Since the logbook folder is the system of record (D2, D10), a
wrong-folder boot is a data-loss-class bug. Failing at startup with a
file path and line number in the error is the smallest-blast-radius
option — the operator sees it immediately rather than discovering it
hours later when jumps are missing.

**Implementation.** `settings_customise_sources` on `Settings` returns
`(init, env, TomlConfigSettingsSource(..., toml_file=config_file_path()),
file_secret_settings)`. `config_file_path()` is a free function that
calls `user_config_dir()` fresh on every invocation, so tests can
monkeypatch `user_config_dir` to a `tmp_path` and have the new location
take effect on the next `Settings()` call.

`load_settings(**overrides)` accepts kwargs and forwards them to
`Settings(**overrides)`, then expands `~` in `logbook_root` so the rest
of the app never sees a tilde-prefixed path.

**`dotenv` support dropped.** pydantic-settings exposes a `dotenv`
source by default (reads `.env` in cwd). The D28 wiring explicitly
omits it from the source tuple — a `.env` file is not in D20's
contract, its location (cwd) is indeterminate for a packaged app, and
its precedence relative to the TOML file would need a second decision
we do not yet have motivation for.

**Alternatives considered.**

- *Roll our own TOML source* instead of pydantic-settings' built-in.
  Rejected: `TomlConfigSettingsSource` has existed since v2.3, handles
  the missing-file/malformed-file edge cases correctly, and bumping the
  library floor from `>=2.2` to `>=2.3` is cheaper than maintaining ~30
  lines of source code we would otherwise own.
- *File > env precedence.* Rejected: makes it impossible to override a
  disk value without editing it, and pushes secret management onto a
  plaintext file.
- *`extra="forbid"` on `Settings`.* Rejected for v0.1: forward-compat
  pain during upgrades. Revisit if a wrongly-named field silently
  losing an intended override becomes a real problem.
- *Search a list of file paths.* Rejected: D20 pins one path. Multiple
  search paths mean ambiguity at bug-report time ("which config.toml
  was read?"). Overrides belong in env.
- *Validate schema version in the file.* Deferred. Today `Settings` is
  small enough that pydantic's per-field validation is sufficient; if
  the config grows structured sections, a top-level
  `config_version = "1"` guard would pair well with D26's migration
  machinery.

**Consequences.**

- `pyproject.toml` floor: `pydantic-settings>=2.3`.
- `backend/config.py` gains `config_file_path()`,
  `settings_customise_sources`, and a `load_settings(**overrides)`
  signature.
- Public contract: the precedence rule and the file location (D20) are
  user-visible. Adding a fifth source later (e.g. CLI args) is additive
  at the top; changing the relative order of existing sources is
  breaking.
- Test coverage:
  - Defaults with missing file; empty file behaves like missing.
  - TOML overrides defaults (single + multiple values; Path coercion).
  - Env overrides TOML; init kwargs override env + TOML.
  - Malformed TOML raises `TOMLDecodeError`; wrong type raises
    `ValidationError`; out-of-range port raises `ValidationError`.
  - Unknown keys ignored.
  - `load_settings` expands `~` in `logbook_root`.

---

## D29 — `bootstrap_logbook`: idempotent folder setup

**Decision.** `backend/storage/bootstrap.py` exports
`bootstrap_logbook(root: Path) -> None`, an idempotent filesystem
primitive that makes `root` ready to hold jumps. It is called by
`main.py` after acquiring the lockfile (D9) and before `uvicorn.run`,
and is the same function a future first-run UX (pick-your-logbook
picker, deferred per D11/D14) will invoke when the user selects a
folder.

**What it creates or refreshes:**

- `<root>/` itself (and any missing parents), via `mkdir(parents=True,
  exist_ok=True)`.
- Every `SCHEMA.v*.xsd` shipped by the app (today: `SCHEMA.v1.xsd`;
  future: `SCHEMA.v2.xsd` alongside v1 per D18), loaded via
  `importlib.resources.files("backend.xml.schema")` and written via
  `atomic_write` (D10). **Always overwritten** — schema updates within
  a version are strictly additive per D18, so refreshing the file
  cannot invalidate older jumps.
- `<root>/README.md`, loaded from `backend/storage/templates/
  LOGBOOK_README.md` via `importlib.resources.files` and written via
  `atomic_write`. **Only if the target is missing** — preserves user
  edits on re-bootstrap.
- Subdirectories `jumps/`, `equipment/`, `.trash/`, via
  `mkdir(exist_ok=True)`. Idempotent by construction.

**What it deliberately does NOT create.**

- `settings.xml` — per-logbook preferences (D20). Its schema is not
  yet fixed by a decision; writing an empty stub here would commit to
  an implicit shape. The service layer reads it if present and uses
  defaults otherwise.
- `index.sqlite` — D26's territory. `storage/index.py:open_index`
  creates it with the current schema version on first open.
- `.logbook.lock` — D9's territory. Created by `lockfile.acquire` when
  the app boots, and `main.py` acquires the lock *before* calling
  `bootstrap_logbook` so the folder setup runs under mutual exclusion.

**Idempotency contract.** Running `bootstrap_logbook(root)` on a fresh
folder, an already-initialized folder, or a folder containing a
populated `jumps/` tree is safe and produces the same observable
state. The only externally-visible difference between the first call
and a subsequent call on an unchanged folder is the mtime of the XSD
files (rewritten with the same bytes).

**Error handling.** Errors propagate unmodified — `PermissionError` on
a read-only mount, `FileExistsError` when `root` points at an existing
plain file, `OSError` on disk full, etc. The caller decides how to
present them. `main.py` catches `OSError` around the call and exits
non-zero with `f"error: cannot set up logbook at {root}: {exc}"`,
mirroring its existing `LockError` branch.

**Why a separate module and not a service.** Bootstrap happens before
any `user_id` is known — it initializes the logbook itself, not
per-user data. D7 reserves `services/` for business logic that takes
`user_id` per D8; putting bootstrap there would force an artificial
`user_id` parameter for a pre-user operation. It is the same category
of primitive as `storage/lockfile.py` and `storage/trash.py` —
filesystem-level setup with no application logic above it.

**Why a template file, not an inline string.** `README.md` is
markdown with a code-block ASCII folder tree (backticks, pipes,
indentation). Embedding that as a Python constant is review-hostile
(escaping, visual noise) and hides the template from a non-Python
editor. A resource file loaded through `importlib.resources` is the
standard idiom, works unchanged across source/wheel/future
PyInstaller installs, and keeps the template searchable as plain
markdown in the repo.

**Logbook README vs project README.** D5 distinguishes two READMEs
that serve different audiences: the *project* `README.md` at the repo
root describes the software (installation, stack, principles); the
*logbook* `README.md` at the logbook root describes the on-disk
format (what the folders and files are, how to verify without the
app). The template shipped at `backend/storage/templates/
LOGBOOK_README.md` is the latter.

**Alternatives considered.**

- *Overwrite README on every bootstrap.* Rejected — users who edit the
  file to add notes about their specific logbook would lose those
  edits on every app upgrade. A future `--force` flag could overwrite
  explicitly; not needed until a user reports wanting it.
- *Preserve XSD on every bootstrap.* Rejected — the XSD is the
  machine-readable contract the app validates against. A user who
  edits it local is diverging from the ground truth; overwriting with
  the shipped bytes is the correct default.
- *Create `settings.xml` with an empty `<settings/>` root element.*
  Rejected — commits us to an implicit schema before we have defined
  the real one. A future D-entry fixes the schema *and* the bootstrap
  behaviour together.
- *Have `bootstrap_logbook` acquire the lock itself.* Rejected —
  `main.py` already holds the lock at call time, and double-acquire
  with `filelock` on the same path would deadlock. Tests would also
  become more complex.
- *Emit a `logbook_bootstrapped` log record per call.* Deferred.
  D27 infrastructure is in place and it would be a one-liner, but
  without an operational need, it is noise. Additive to add later if
  an incident surfaces a use case.
- *Also parse+validate each XSD during bootstrap.* Rejected — the
  project's test suite already guarantees shipped XSDs parse. Parsing
  at bootstrap pulls lxml into a filesystem primitive for no
  additional guarantee at runtime.

**Consequences.**

- New module: `backend/storage/bootstrap.py` with a single public
  function, `bootstrap_logbook(root: Path) -> None`.
- New resource package: `backend/storage/templates/` containing
  `__init__.py` and `LOGBOOK_README.md`.
- `pyproject.toml` `[tool.hatch.build.targets.wheel.force-include]`
  gains `"backend/storage/templates" = "backend/storage/templates"`
  so the template ships in the wheel. A future PyInstaller spec
  (D11) must include `backend/xml/schema` and
  `backend/storage/templates` in its `datas` section; same concern
  already applies to the XSDs and was out of scope pre-D29.
- `backend/main.py` calls `bootstrap_logbook(settings.logbook_root)`
  inside the lock's `try`/`finally`, and catches `OSError` around it
  to print a friendly message on setup failure.
- Test coverage: fresh-folder creation, XSD bytes match shipped
  source, README bytes match template, user-edited README preserved,
  populated `jumps/` and `equipment/` untouched, tampered XSD
  overwritten with shipped bytes, two-run idempotency, pre-existing
  subdirs don't error, deep missing parent paths created, root
  pointing at a file raises `OSError`.

---

## D30 — Attachments arrive on create as `multipart/form-data`

**Decision.** `POST /api/v1/jumps` uses `multipart/form-data` as its
sole request content type. The body carries:

- exactly one `jump` part, a `text/plain` or `application/json` field
  whose value is a JSON document matching `JumpCreate`;
- zero or more `files` parts, each a file upload whose part filename
  becomes the attachment filename on disk (after sanitization) and
  whose Content-Type becomes the attachment `content_type`.

The server computes each attachment's SHA-256 and size during the
streaming write — the client never supplies a claimed hash. Clients
that want to create a jump with no attachments send a multipart body
with only the `jump` field and an empty (or omitted) `files` list.

**Why.** D25 fixes the on-disk write ordering for create with
attachments (stream-hash-write each attachment, then write
`jump.xml`, then write `SHA256SUMS`). Getting the attachment bytes to
the service layer on the same call that creates the jump is the only
shape that actually exercises that ordering end-to-end. A
deferred-add alternative (JSON create, then separate
`POST /api/v1/jumps/{id}/attachments`) would route every file through
`update_jump`'s ordering instead, leaving D25 create step 2 as dead
prose and forcing a two-round-trip UX for the common "fill form +
drop videos + save" case.

D21 requires streaming rather than buffering; FastAPI's multipart
parsing with `UploadFile` already spools to disk (default 1 MiB spool
before spill), so chunked reads through the uploaded file give us a
streaming pipeline without any custom parser work.

Server-side hash computation (rather than client-claimed hash) keeps
the attachment `<sha256>` invariant by construction: the bytes that
reached the disk and the hash recorded in `jump.xml` came from the
same streaming pass. A client-supplied hash would either be ignored
(wasteful contract) or verified (adds a round-trip decision: accept
the bytes and then reject? reject pre-write? complicated crash
story). The D25 "agree by construction" wording in step 2 steers us
toward this.

**Out of scope for this D-entry.** `update_jump` (Phase 3.5) will
also accept multipart uploads for *new* attachments and a JSON-style
keep-list for existing ones. That shape gets its own D-entry when
Phase 3.5 lands; it shares the streaming-hash-on-write machinery
specified here.

**Alternatives considered.**

- *JSON-only POST + separate `POST /api/v1/jumps/{id}/attachments`.*
  Cheapest on the existing 18-test REST suite (they stay `json=...`).
  Rejected for v0.1 because (a) D25 create ordering step 2 would
  never run in the REST path — supersede-with-D25-amendment cost is
  higher than the test refactor cost — and (b) the single-user
  desktop UX is "one form, one save", not "save, then attach".
- *Both transports (multipart on create + post-create add endpoint).*
  Two code paths; twice the test surface; keeps the door open for
  "I forgot one" but that use case is identical to `update_jump` in
  Phase 3.5. Deferred: if a future user workflow demands add-later
  without a full update, we add the endpoint as an additive change.
- *JSON with base64-encoded attachments.* Rejected on D21 grounds:
  base64 forces the whole payload through memory before the server
  can even begin streaming to disk, and inflates wire size by ~33%.
  Fatal for a GoPro clip.
- *Client-supplied hash + server-verifies.* Rejected: either the
  client writes streaming code that computes the hash pre-upload
  (forcing a double read on the client) or the check happens after
  the bytes are already on disk, complicating the crash story. D25
  step 2's "agree by construction" reading steers us to
  server-computed-during-write.

**Consequences.**

- `backend/storage/filesystem.py` grows `atomic_write_stream(
  path, chunks) -> str`, the streaming sibling of `atomic_write`:
  writes chunks to a tmp file, fsyncs, `os.replace`s into place, and
  returns the hex SHA-256 computed during the write. The write path
  stays D10-atomic; the hash is the incidental byproduct needed by
  D25 step 2 to populate `<attachment>/<sha256>`.
- `backend/services/jump_service.py:create_jump` gains an `uploads:
  Iterable[Upload] | None = None` parameter where each `Upload`
  carries `filename`, `content_type`, and a chunk iterator. On each
  upload: sanitize filename → `atomic_write_stream` to the jump
  folder → record the returned hash and the on-disk size as an
  `Attachment` on the built `Jump` → only then serialize, XSD-
  validate, and write `jump.xml` (D25 step 3).
- `backend/api/jumps.py` POST handler takes `jump: str = Form(...)`
  and `files: list[UploadFile] = File(default_factory=list)`; it
  parses `jump` via `JumpCreate.model_validate_json`, wraps each
  `UploadFile` in a chunk-iterator adapter, and hands the list to
  `create_jump`. The 201 + Location + full-Jump-body response shape
  is unchanged.
- Duplicate `filename` within a single request (after NFC + sanitize)
  is a 422 `ValidationFailedError` with pointer
  `#/files/<index>/filename`. The filesystem cannot hold two files
  with the same name in one folder; rejecting pre-write is cleaner
  than letting the second `atomic_write_stream` clobber the first.
- Bad filename (forbidden char, Windows reserved, trailing dot) is a
  422 with the same pointer shape. The `Attachment` model-level
  `sanitize_filename` check still runs, but catching at the upload
  edge gives a clearer error location than a model-level message.
- The 18 existing REST tests in `test_rest_jumps.py` convert from
  `client.post(..., json=body)` to
  `client.post(..., data={"jump": json.dumps(body)})`. Mechanical
  refactor; semantics (201/409/404/422, Location header, problem+json
  envelope, X-Request-Id correlation) stay unchanged.
- OpenAPI: the POST operation advertises `multipart/form-data` as its
  request body. `response_model=Jump` on the success path and the
  RFC 9457 error responses are unaffected.
- D21's streaming requirement is satisfied: FastAPI's `UploadFile`
  spools to disk, `atomic_write_stream` consumes it in bounded-size
  chunks, and the bytes never all sit in Python memory at once.

---

## D31 — v0.1 `update_jump` is metadata-only; attachment editing deferred

**Decision.** In v0.1, `PUT /api/v1/jumps/{id}` accepts metadata
changes only: every field on `Jump` may be edited *except* the
`attachments` array, which is preserved unchanged from the existing
on-disk `jump.xml`. The request body is `application/json` matching
a `JumpUpdate` schema that mirrors `JumpCreate` (same fields; no
`attachments` field). `DELETE /api/v1/jumps/{id}` soft-deletes per
D19 and returns `204 No Content`.

Attachment editing via PUT (add new, remove existing, replace the
full set) ships in a later phase with its own D-entry pinning the
multipart transport. Until then, a client that wants to change a
jump's attachments deletes the jump, re-creates it, and re-uploads.

**Why defer attachment editing.**

- D25 §"Write ordering — `update_jump`" prescribes a four-step
  ordering (new attachments → new jump.xml → new SHA256SUMS → delete
  orphans) and a crash-row (`update_jump, after new jump.xml, before
  delete step`). Wiring that into a multipart PUT is real engineering:
  the transport has to distinguish "keep this existing file" from
  "here's a new file" from "drop this existing file", and the
  service has to verify every claimed-keep filename actually exists
  on disk, and has to teach the subprocess crash harness a new crash
  point for the orphan-delete step.
- None of that machinery blocks the user story we're shipping.
  "Edit a jump I logged wrong" is the primary update flow; "swap the
  FlySight file on a jump" is rare enough that delete-and-recreate
  is acceptable UX until the real endpoint lands.
- Shipping a thinner `update_jump` first means the crash story we
  *do* test (metadata edit → rename → index update) has focused
  coverage. A kitchen-sink PUT with attachment-edit semantics on day
  one would give us tests that are harder to reason about.

**Contract for the v0.1 PUT.**

Request body (matching `JumpUpdate`):

```json
{
  "jump_number": 851,
  "title": "4-way FS — recovered",
  "date": "2026-04-22",
  "dropzone": "Skydive Elsinore",
  "exit_altitude_m": 4000,
  "deployment_altitude_m": 900,
  "aircraft": "Twin Otter",
  "freefall_time_s": 55,
  "notes": "Funnel on exit, recovered at 3200 ft.",
  "equipment": { "canopy_id": "..." }
}
```

Response: `200 OK` + the canonical `Jump` (same body shape as POST
returns). `Location` header is not set because the resource URL is
unchanged — even if `jump_number` changed, `id` is what the URL
keys on (D4 stable UUID).

Errors:
- `404 Not Found` (`code=not_found`): no jump with that id.
- `409 Conflict` (`code=jump_number_conflict`): the requested
  `jump_number` is already used by a different jump (D23).
- `422 Unprocessable Entity` (`code=validation_failed`): Pydantic
  rejected the body, or the requested title produces an invalid
  folder name (D4).

**Service-layer write ordering (metadata-only case).**

1. Resolve the current jump (by `id`) from the index; 404 if
   missing. Parse its current `jump.xml` through the hardened parser
   (D2) to get the full current state including attachments.
2. Merge payload fields onto the current `Jump`. Validate the result
   via Pydantic; reject on any rule violation (422).
3. If the resulting `jump_number` differs from the on-disk one,
   scan `jumps/` for a collision on the new number (D23 prefix
   scan). Collision → 409.
4. Compute the new folder name (`jump_folder_name(jump_number,
   title)`). If the title fails sanitization, 422.
5. Serialize the updated `Jump` to XML; validate against the XSD
   (D2).
6. `atomic_write` `jump.xml` at the **current** folder path. This is
   the D25 update-ordering step 2 (new jump.xml) but with no new
   attachments to precede it and no orphans to trail it.
7. `atomic_write` `SHA256SUMS` at the current folder path from
   `from_jump_xml` (D25 step 3).
8. If the folder name changed, `os.rename(old_folder, new_folder)`.
   `os.rename` is POSIX-atomic; the target must not exist, which is
   guaranteed by step 3's collision check.
9. Update the index row (`jump_number`, `title`, `folder`,
   `updated_at`; `created_at` is preserved). D17: new timestamp is
   ISO-8601 UTC ms-precision `'Z'`.

**Why atomic_write jump.xml BEFORE the folder rename, not after.**

An `os.rename` crash window would leave `jump.xml` with stale bytes
at the new folder path. By writing `jump.xml` first (at the old
path), then renaming, we keep the on-disk state coherent at every
instant — the folder's jump.xml always reflects the jump, regardless
of what the folder is currently called. D4's asymmetric rule says
the folder name is cosmetic and the XML is canonical; this ordering
honors it.

**D6 signature-strip hook (reserved).** If `jump.xml` contains a
`<signature>` element, any update_jump call strips it before the
write. No prompt; the jump becomes unsigned. Not tested yet because
signing is deferred; mentioned here so the implementation includes
the strip site and a test can land additively once signing does.

**Delete semantics.** `DELETE /api/v1/jumps/{id}` calls
`storage.trash.soft_delete` (already implemented per D19) to move
the folder into `.trash/<timestamp>_<name>/`, then deletes the
index row. Returns `204 No Content`. Once deleted, `GET` returns
404 and the jump no longer appears in `list_jumps`. The `.trash/`
folder remains verifiable but is outside the D23 uniqueness
namespace — a fresh jump can reuse the same jump_number.

**Alternatives considered.**

- *Full PUT with attachment editing on day one.* Rejected for v0.1
  scope discipline. Shippable without, and the mini-contract here
  is what users will hit 95% of the time.
- *PATCH for partial updates.* Rejected for the usual reasons —
  RFC 7396 JSON Merge Patch doesn't handle nested list edits
  cleanly, and RFC 6902 JSON Patch is overkill for a single-user
  logbook. PUT-with-full-state is simpler to reason about and
  matches how POST works.
- *Hard delete instead of soft delete.* Rejected: D19 already
  enshrines soft delete; deviating in v0.1 would be a breaking
  change the day trash restoration ships.
- *Immutable jump_number.* Rejected because real-world jumpers
  occasionally re-number early jumps when they merge logbooks or
  correct an import error. The D23 uniqueness check handles the
  collision case cleanly.

**Consequences.**

- `backend/models/jump.py` gains `JumpUpdate` — same fields as
  `JumpCreate`; no `attachments`.
- `backend/services/jump_service.py` `update_jump` implements the
  ordering above; `delete_jump` is a two-liner calling
  `storage.trash.soft_delete` plus an index row removal.
- `backend/api/jumps.py` adds `PUT /api/v1/jumps/{jump_id}` and
  `DELETE /api/v1/jumps/{jump_id}` — JSON body for PUT per this
  D-entry, nothing for DELETE.
- `test_update_jump.py` and `test_delete_jump.py` land alongside
  the existing `test_create_jump.py`. Crash-path subprocess tests
  for update_jump's orphan-delete step land WITH the attachment-
  edit phase, not here (there are no orphans to delete in the
  metadata-only flow).
- D30 (multipart on create) stays as-is. The attachment-edit phase
  will add a second multipart endpoint — either PUT at the same
  path or `PUT /api/v1/jumps/{id}/attachments` — to be decided in
  that phase's D-entry.

---

## D32 — Audit timestamps (`created_at`, `updated_at`) live in `jump.xml`

**Decision.** `jump.xml` carries two optional elements —
`<created_at>` and `<updated_at>` — both `xs:dateTime` values in the
D17 canonical form: ISO 8601 UTC with millisecond precision and a
`'Z'` suffix (e.g. `2026-04-23T18:45:03.127Z`). The service writes
both on `create_jump` (equal values) and bumps `updated_at` only on
`update_jump`. `reindex_from_xml` reads these values to rebuild the
index. When either element is absent (third-party-authored XML or a
file predating D32), reindex falls back to the file's mtime and
emits a `reindex_timestamp_fallback` WARNING log.

**Why.** D3 pins SQLite as rebuildable from XML. If the index
timestamps aren't recoverable from XML, D3 is not actually honored —
wiping `index.sqlite` and reindexing would silently reset every
jump's `created_at` to "now", losing the audit trail. The only way
to keep D3 and have meaningful timestamps is to record them in the
XML.

Users care about these values. A rigger pulling up the logbook to
verify a reserve repack wants to know when that jump was logged —
not just when the jump happened (`<date>` is the jump date, not the
log-entry date). An import error that shows jumps in the wrong year
is easier to diagnose with a "first written at" stamp.

**Why xs:dateTime in UTC and not xs:date or an epoch int.** `xs:date`
throws away time-of-day, which matters when a user logs twenty jumps
in a boogie day and wants them ordered correctly by entry time.
Epoch seconds would be compact but human-hostile in a file the user
might open in a text editor. `xs:dateTime` in UTC is the
interoperable choice; D17 already mandates this form for API bodies
and log timestamps, so XML matching the same convention avoids a
third representation.

**Additive-to-v1 check.** D18 allows adding optional elements to
v1 within the namespace. New optional elements at the end of a
`<sequence>` don't invalidate existing files — they simply don't
appear. Existing handlers (like a third-party validator that knows
only v1) will continue to accept both new and old files.

**Consequences.**

- `SCHEMA.v1.xsd` grows two optional elements at the end of the
  `<jump>` sequence: `<created_at>` and `<updated_at>`, each
  `xs:dateTime`. They land AFTER `<signature>` and BEFORE
  `<generator>` to keep the element order "user content, then
  audit metadata, then write-provenance".
- `backend/models/jump.py` `Jump` gains `created_at` and
  `updated_at` as optional ISO-8601 UTC strings (same shape as the
  log timestamps produced by `_now_utc_iso`). `JumpCreate` and
  `JumpUpdate` do NOT expose them — clients never set these
  directly; the service is the only author.
- `backend/xml/serialize.py` writes both elements when present on
  the Jump, reads them when present on the XML.
- `backend/services/jump_service.py`:
  - `create_jump` stamps `created_at = updated_at = _now_utc_iso()`
    on the Jump before serialization.
  - `update_jump` preserves `created_at` from the existing jump and
    stamps `updated_at` fresh. Keeps the "first time this was
    logged" signal intact across any number of edits.
- `backend/scripts/reindex.py` / `backend/services/jump_service.py:
  reindex_from_xml` reads timestamps from each parsed jump. If both
  are present, use them as the index row's `created_at` /
  `updated_at`. If either is absent, use the file mtime of
  `jump.xml` for both, emit a WARNING log
  (`reindex_timestamp_fallback`) carrying the folder, and
  optionally rewrite `jump.xml` to stamp the values (TBD — likely
  no-write-on-reindex in v0.1 to keep reindex side-effect-free,
  deferred to a `--heal` flag in a later phase).
- `verify` is unchanged — timestamps are metadata, not integrity
  artifacts. A missing `<created_at>` is a warning on reindex, not
  a verify failure.
- Existing tests that assert on index `created_at == updated_at`
  on first insert still pass because `create_jump` writes both
  equal. Tests that assert `updated_at != created_at` after update
  still pass because `update_jump` bumps one and preserves the
  other.

**Alternatives considered.**

- *File mtime only, no XML fields.* Rejected — rsync, cp -r, git
  checkout, and cloud sync all stomp mtimes. The data would be
  worthless on a restored-from-backup logbook.
- *Leave them null at reindex; service writes NOW() on next
  update.* Rejected — a reindex-clean logbook shows every jump as
  "modified just now" regardless of history. That's noise
  disguised as fact.
- *Separate `.meta/jump-N.json` sidecars.* Rejected as "yet another
  file format in the folder". D3 + D25 are explicit that `jump.xml`
  is the single source of truth; audit metadata belongs there.
- *Add both `<created_at>` and `<updated_at>` as REQUIRED in the
  XSD.* Rejected — breaks D18's additive-only rule within a
  version, and would reject jumps hand-crafted by an author who
  wasn't aware of the fields.
- *Use Git log as the source of truth for timestamps.* Rejected —
  git is not a dependency of the logbook, just an occasional
  storage/sync tool for users who opt in. Many users will never
  commit their logbook.

---

## D33 — Equipment becomes the Rig Manager module; supersedes D14 §3

**Decision.** The equipment item in the D14 v0.1 scope list
("Add/edit containers, canopies, and AADs as separate entities.
Link them to jumps by reference. Track reserve repack dates and
AAD service intervals.") is replaced wholesale. In its place, v0.1
ships the Rig Manager: a module that models skydiving gear as six
first-class entities (Main, Reserve, AAD, Container, Rig, Jumper),
enforces rigger-only ownership of the container / reserve / AAD
assembly, tracks per-component wear and service cycles, and
captures jump-time rig state as a frozen snapshot in the jump
folder.

**Why.** Built like D14 originally described, "equipment" is a
thin row with kind / manufacturer / model / serial / DOM and two
timestamp fields. That shape is almost useful: it lets a jump
reference a canopy ID, but it cannot tell the jumper whether the
rig is legal to jump today (reserve repack calendar, AAD service
window, lineset wear), cannot compare wingloading against AAD
mode, and cannot preserve the composition of a rig across
mutation. Users end up hand-tracking repack dates and lineset
jumps in a notes field, which is exactly the pain the logbook is
supposed to eliminate.

Alex's direction on 2026-04-24 — "instead of building a
lightweight thing now let's just build it properly from the
start" — makes the case that the thin shape is strictly worse
than doing the full model once. Pulling the rig manager forward
lands one focused module instead of a placeholder that gets
rebuilt inside a year. The scope, architectural friction, and
phasing were analyzed in the 2026-04-24 rig-manager integration
review (now condensed in `docs/historical-reviews.md`) and
refined with Alex in a design session the same day. This
decision encodes the result.

**Scope (what v0.1 equipment now means).**

- Six entity types. Main (with nested current lineset and
  lineset history), Reserve, AAD, Container, Rig, Jumper.
  Per-kind Pydantic models and per-kind XSD elements — see D34.
- Component lifecycle. Each component is a first-class XML file
  with its own history. `assigned_rig_id` is nullable and every
  component is in zero or one rigs at any time. The jumper can
  swap the main anytime; the reserve, AAD, and container move
  only through a rigger repack event — see D37.
- Rig composition is mutable; rig identity is stable. A rig
  references four component IDs (`current_main_id`,
  `current_reserve_id`, `current_aad_id`,
  `current_container_id`) plus jurisdiction (USPA, CSPA, or
  both). Those four references rotate over the rig's life —
  the main on any jumper-initiated swap, the other three only
  at repack events (D37). The rig's identity — its UUID, its
  nickname (and the folder named after it, per D4's sanitize-
  and-rename pattern), its `<repack_history>`, its
  `<jurisdiction>` — is stable across all of those rotations.
  The 180/270 day repack clock lives on the rig, driven by the
  latest repack event's date. Historical jumps stay pinned to
  the composition that was on the rig at log time, via
  `rig-snapshot.xml` in each jump folder (D36) — a present-day
  rotation of components never silently mutates an old jump
  record.
- Jump-time snapshot. Every jump folder gains a
  `rig-snapshot.xml` alongside `jump.xml`, hashed into the same
  SHA256SUMS manifest. The snapshot freezes the rig and its
  components at log time and does not mutate if the underlying
  records change later — see D36.
- Reline math. Per-jump wear on the main's current lineset,
  using JYRO's `breaking_strength − exit_weight` budget and
  per-jump environment multipliers applied only on jumps
  actually made in that environment. Default environment flags
  live on the main and reset on reline.
- Counter projection. `jump_count`, `ride_count`,
  `repack_count`, `fire_count`, and lineset `consumed_lb` each
  decompose into a stored `count_initial` (editable, used for
  used-gear setup and manual correction) and a `count_derived`
  computed from jumps + repack events in the SQLite index.
  Display = initial + derived — see D35.
- AAD rules matrix. Pure-function lookup keyed on brand + model
  + DOM tier (Cypres 2 pre-2016, 2016, 2017+; Vigil II / 2+ /
  Cuatro with 10-yr battery; MarS m2 15 yr or 15 k jumps;
  retired models auto-red) — see D39.
- Wingloading-driven AAD mode nudge. `wl > 2.0` → high-speed
  mode, `1.0 ≤ wl ≤ 2.0` → standard, `wl < 1.0` → student.
  Per-brand mode names resolved in code.
- Exit-weight staleness. `exit_weight_updated_at` on the jumper;
  yellow prompt after 365 days prompting the user to confirm or
  update.
- Status colors. Green / yellow / red with a ±6 month service
  window and a 90% threshold for calendar and count limits.
  Every red surfaces "see your rigger" — the system is a tracker,
  not an airworthiness determination.

**Out of scope for v0.1 equipment** (explicit non-decisions; each
becomes its own later phase or decision):

- Rigger identity tracking. No rigger expiry, no rigger
  credentials record. The rigger is a free-text name on each
  repack event.
- DZ / rental fleet management. One jumper's personal gear only.
- The write flow for repack events. The XSD shape lands in v0.1
  per D38, but the service, REST endpoint, and UI that create
  repack events are deferred to a later phase (R.5). Users who
  need to record a repack before the flow ships can edit the rig
  XML by hand — the data remains self-describing and validated
  against the XSD.
- Notifications / push alerts. The UI surfaces status colors on
  open; there is no background job.
- Rigger-visit export (PDF / printable report).
- Wingsuit- and tandem-specific AAD mode logic.
- Inventory attachments (photos of the assembled rig, receipts,
  rigger documents). Components are flat single-file XMLs for
  v0.1; elevation to folder-with-attachments is additive later.

**Consequences.**

- **D14 §3 is superseded** by this decision. D14 §1 (log a jump),
  §2 (upload files to a jump), and §4 (basic stats) stand.
- **D14 §4 "Basic stats"** gains a dependency on this module:
  the "jumps by canopy" widget now resolves through
  `rig-snapshot.xml` for historical jumps, falling back to the
  live main if the rig-snapshot is absent (pre-v0.1-snapshot
  jumps) or if the widget chooses "current-state" semantics.
  The widget's visible surface does not change; only the data
  source does.
- **D22** (closed `EquipmentKind` enum) is narrowed. The Python-
  side single `Equipment` model is retired and replaced with
  per-kind models. The closed-enum rationale stays relevant at
  the XSD boundary and is restated in D34 for the new six-
  element shape.
- **Legacy code removal.** `backend/models/equipment.py`,
  `backend/services/equipment_service.py` (an empty placeholder),
  and the `<equipment>` / `<equipment_refs>` / `EquipmentKind` /
  `EquipmentRefsType` elements in `SCHEMA.v1.xsd` are removed.
  No equipment XML files exist on disk (Phase 3.6 ended without
  any written), so no migration is required. All references in
  `backend/api/rest.py`, `backend/api/openapi.py`,
  `backend/xml/serialize.py`, `backend/storage/index.py`,
  `backend/storage/bootstrap.py`, `backend/storage/templates/
  LOGBOOK_README.md`, and the relevant tests are scrubbed in the
  same slice (R.0.1).
- **XSD v1 extension.** New top-level elements land additively in
  `SCHEMA.v1.xsd` per D18: `<main>`, `<reserve>`, `<aad>`,
  `<container>`, `<rig>`, `<jumper>`. A `<rig_snapshot>` top-
  level element also lands (used only inside the jump folder).
  The repack-event nested structure lives within `<rig>`. No
  schema-namespace bump. Jump.xml's equipment reference changes
  from `<equipment_refs>` (removed) to `<rig_id>` +
  `<environment_flags>` + `<reserve_ride>` (added) — not in R.0,
  this ships in R.2.
- **Folder layout extends** as follows. Each path's rationale is
  elaborated in D34 (per-kind shape), D36 (jump snapshot), and
  D37 (assignment / inventory):
  ```
  logbook_root/
    jumps/                     # unchanged — adds rig-snapshot.xml in R.2
    rigs/<nickname>/           # folder per rig; matches jump pattern
      rig.xml
      SHA256SUMS
    inventory/                 # flat single-file per component
      mains/<uuid>.xml
      reserves/<uuid>.xml
      aads/<uuid>.xml
      containers/<uuid>.xml
    jumpers/<uuid>.xml         # flat single-file
    SCHEMA.v1.xsd              # extended
  ```
  Rigs get a folder-with-manifest so future attachments (seal
  photos, rigger documents) are additive. Components are flat
  files because they never carry attachments in v0.1; corruption
  detection falls to XSD validation + the hardened parser. No
  `events/` folder in v0.1 — repack events live nested in
  `rig.xml` per D38, and no other event types are designed for
  v0.1.
- **Rollout.** Five phased slices (R.0 decision records + entity
  models + create/get services; R.1 list/update/delete + REST;
  R.2 jump.xml integration + rig-snapshot.xml; R.3 counter
  derivation + index projections; R.4 AAD rules matrix +
  wingloading + status colors; R.5 repack event write flow).
  Detail in the 2026-04-24 rig-manager integration review
  (condensed in `docs/historical-reviews.md`).

**Alternatives considered.**

- *Ship D14 §3 as written (thin `Equipment` model), then build
  the rig manager as v0.2.* This was the 2026-04-24 rig-manager
  integration review's recommendation (condensed in
  `docs/historical-reviews.md`).
  Rejected in the 2026-04-24 design session: the thin shape
  cannot answer the user's first real question ("can I jump this
  rig today?"), and the work built on top of it would be
  discarded inside a year. Building the full module once, phased,
  avoids a placeholder-and-replace cycle.
- *Model equipment as user-defined freeform accessories without
  kind constraints.* Rejected. AAD rules, lineset budgeting, and
  wingloading all require typed access to brand, model, DOM, and
  size. A typed model is load-bearing for every feature above
  bare linkage.
- *Keep `jump.xml` as the single source of equipment truth
  (denormalize equipment fields onto each jump).* Rejected. A
  main is on many jumps and lives through many rigs; its history,
  counters, and reline events belong on the main itself, not
  smeared across jump files. The rig-snapshot per jump (D36) is
  denormalization for audit and historical accuracy; it does not
  replace the canonical per-entity XML.
- *Defer "basic stats" (D14 §4) past v0.1 as well.* Rejected.
  Basic stats are small, derivable from jumps + rig snapshots,
  and one of the two reasons a jumper opens the app (the other
  being "log a new jump"). They stay in v0.1.
- *Bump schema to v2 to get a clean shape without the retired
  `<equipment>` element in the namespace.* Rejected per D18 and
  the analysis doc §4.4. The equipment element is being removed
  before any file on disk uses it; there is nothing to preserve
  backward-compatibility with. Additive extension of v1 is
  correct.

---

## D34 — Per-kind component models; lineset nested on main

**Decision.** The Python-side single `Equipment` model is retired.
Each of the six rig-manager entities has its own Pydantic model
and its own XSD top-level element:

- `<main>` (with nested `<current_lineset>` and
  `<lineset_history>`) → `backend/models/main.py`
- `<reserve>` → `backend/models/reserve.py`
- `<aad>` → `backend/models/aad.py`
- `<container>` → `backend/models/container.py`
- `<rig>` → `backend/models/rig.py`
- `<jumper>` → `backend/models/jumper.py`

Lineset is not a first-class entity. It exists only as a nested
structure inside `<main>`: one `<current_lineset>` (required once
the main has been lined) and a `<lineset_history>` ordered list
of frozen prior linesets (archived on reline, never mutated
thereafter).

The closed-enum discipline from D22 is preserved at the XSD
boundary but moved: instead of a single `<kind>` enum inside a
generic `<equipment>` element, each top-level element has a
fixed name, making the "kind" determined by the element tag
rather than a child value. Misnaming an element (e.g. writing
`<mani>`) fails XSD validation just as misnaming a `<kind>`
value did.

**Why.** The spec carries field combinations that do not fit a
single shared shape:

- Main has size (sqft), DOM, status, `default_environment`
  (renamed from `default_environment_flags` 2026-04-28 R.0.2e —
  the value is a single `Environment` enum value, not a bit set,
  so "flags" was misleading), nested lineset, jump counters.
- Reserve has manufacturer-specific `repack_limit` /
  `ride_limit`, recert extensions, repack / ride counters.
- AAD has manufacturer, model, DOM, serial, mode,
  `is_changeable_mode`, jump counter, fire counter — service
  windows and EOL are computed from these facts by the
  pure-function lookup in D39, not stored on the AAD itself.
  **Amended in place 2026-04-28** (R.0.2c): the original wording
  said `brand`. Renamed to `manufacturer` for symmetry with main /
  reserve / container, where the same concept already uses that
  field name. D39's lookup keys on the same value regardless of
  the field's spelling; the rename is a one-place edit.
- Container has size, DOM, jump counter.
- Rig has jurisdiction and four component references, repack
  history.
- Jumper has `exit_weight_lb` + `exit_weight_updated_at`.

Putting all of these optional fields on a single `Equipment`
model would make "what is this object?" ambiguous to read,
noisy to type-check, and impossible to validate cleanly (the
"if `kind == main` then `size` is required" constraint lives
outside the model). Discriminated-union-at-the-type-level with
one XML element tag per kind at the schema level is the correct
shape when the underlying entities do not share a lattice of
fields.

Lineset is nested on main because its identity is only
meaningful in the context of a main. Alex's 2026-04-24 direction
confirmed this: "Lineset as nested element on main." On reline,
the old lineset is appended to `<lineset_history>` inside the
same `main.xml` file, frozen at its final `consumed_lb`. No
separate `inventory/linesets/` folder, no cross-entity ID
resolution, no orphan-lineset reconcile problem.

**Consequences.**

- Six model files replace `backend/models/equipment.py`. Each
  is self-contained with its own `Create` / `Update` variants
  where appropriate (matches the `JumpCreate` / `Jump` pattern
  from Phase 3.x).
- `SCHEMA.v1.xsd` gains the six top-level elements additively
  per D18. Shared nested shapes (lineset, repack-history entry,
  notes-log entry) live as named `<xs:complexType>` definitions
  near the top of the file so they are reusable across
  components.
- Common fields across components (`id`, `status`,
  `assigned_rig_id`, `notes_log`, the counter `*_initial`
  fields) live on a `ComponentBase` Pydantic class in
  `backend/models/_component_base.py`. The base holds only
  truly universal fields; kind-specific fields stay on the
  concrete model.
- The `status` enum (`active | retired | sold |
  out_of_service`) is closed at the XSD boundary per D22's
  pattern. Retired / sold / out_of_service components are
  hidden from swap dropdowns; the flag itself is preserved on
  the XML and surfaced read-side.
- Lineset nested shape:
  - `id` (UUID, for snapshot references from jump folders)
  - `line_type` (free text — V750, V550, HMA, Vectran, Dacron,
    and future types are added without schema changes)
  - `breaking_strength_lb` (float)
  - `install_date`
  - `install_exit_weight_lb` (float; snapshot for budget math,
    immutable after install)
  - `installed_by` (free text rigger name)
  - `consumed_lb_initial` + `consumed_lb_derived` per D35
- `default_environment` (see rename note above) lives on
  `<main>` (not on the lineset) and is the field that gets reset
  when a reline archives the current lineset into
  `<lineset_history>`.
- `<lineset_history>` entries are identical in shape to
  `<current_lineset>`. Reline does NOT mutate any field on the
  lineset being archived — its `consumed_lb_initial` is left
  unchanged (typically `0` for new install or the used-gear
  starting value). The projection (D35) continues to compute
  `consumed_lb_derived` for archived linesets too, accruing
  from the closed set of jumps whose `rig-snapshot.xml`
  referenced that lineset id. Total = initial + derived
  remains correct after reline because no new jump can ever
  reference an archived lineset; the derived value stabilizes
  on its own.
- Tests: per-kind round-trip test file for each new element
  (`test_main_roundtrip.py`, `test_reserve_roundtrip.py`,
  etc.), plus a combined `test_component_base.py` for the
  shared base.

**Alternatives considered.**

- *Keep a single `Equipment` model and move kind-specific
  fields into a typed `details` union.* Rejected — the union
  branches ARE the models; wrapping them in an outer
  `Equipment` adds indirection without saving code.
- *Lineset as a first-class entity under
  `inventory/linesets/<uuid>.xml`.* Rejected per Alex's
  direction. A separate entity would let a lineset migrate
  between mains, which is not a real-world workflow — lines
  are cut and installed for a specific canopy.
- *Free-form generic `Component` with runtime field validation
  only.* Rejected — loses static type safety and schema-level
  discriminability. XSD validation is meaningful precisely
  because the shapes are precise.
- *Single `<equipment>` element with per-kind
  `<xs:extension>` via XSD inheritance.* Rejected — XSD
  inheritance works but produces verbose XML with `xsi:type`
  attributes on every instance, readability suffers, and
  Pydantic still ends up with a discriminated union on the
  Python side. Net loss.

---

## D35 — Component wear counters are `initial + derived`

**Decision.** Every wear counter on a component is stored as two
fields:

- `<*_initial>` on the component XML — integer (or float for
  `consumed_lb`), editable, set at onboarding for used-gear
  setup and available for manual correction thereafter. Source
  of truth for "where did this counter start."
- `<*_derived>` in the SQLite index — projection rebuilt from
  jump XMLs and repack events (once R.5 introduces them) on
  every `reindex_from_xml`. Never persisted to the component
  XML. Source of truth for "how much has happened since."

Display value is `initial + derived`. The Pydantic model exposes
`*_initial` as a real field, `*_derived` as a computed property
read from the index at serialization time, and `*_total` as the
sum. REST responses carry all three so clients can reason about
their origin.

The counters covered by this pattern:

- `main.jump_count`
- `aad.jump_count`
- `container.jump_count`
- `reserve.repack_count`
- `reserve.ride_count`
- `aad.fire_count`
- `lineset.consumed_lb`

Reserve intentionally has NO `jump_count`, per the 2026-04-24
direction: reserves are not "jumped" in the counter sense, only
packed and (rarely) ridden.

**Why.** The 2026-04-24 rig-manager spec asks for counters that
are simultaneously (a) stored on the component as simple
integers, (b) auto-incremented on jumps and repacks, and (c)
manually editable for used-gear setup and correction. All three
at once fight the project's invariants:

- **D25 (jump-write is a single-folder atomic operation)**
  would break if auto-increment on jump touched `main.xml` +
  `aad.xml` + `container.xml` + main's nested lineset on every
  write. That is four-plus extra folders / files in one logical
  write, and the existing `folder_reconcile` recovery is
  per-folder. Either we invent a WAL-style multi-folder
  recovery protocol (a large piece of new infrastructure) or we
  accept silent counter drift on crash.
- **D3 (SQLite is rebuildable from XML)** would break if
  counters on the component were both stored AND re-derivable
  from jumps. On reindex, should we trust the stored value or
  recompute? Recomputing loses manual corrections; not
  recomputing means the index can drift.

The `initial + derived` split resolves both:

- D25 stays intact — jump-write touches only the jump folder.
  Counter changes propagate via the index projection, which is
  rebuilt from scratch on reindex anyway.
- D3 stays intact — `*_derived` is recomputed from source-of-
  truth XMLs (jumps + repack events) on reindex. `*_initial`
  is the stored value, always honored; `*_derived` is always
  computed. No ambiguity.
- Manual correction still works — the user edits `*_initial`,
  which is a one-file write to the component XML, and the
  projection continues to track events layered on top.
- Used-gear setup works cleanly — a jumper buying a reserve
  with 14 prior repacks enters `repack_count_initial = 14`,
  and subsequent R.5 repack events accumulate in
  `repack_count_derived`.

**Consequences.**

- Component XSDs declare only the `<*_initial>` fields.
  `<*_derived>` is not present in XML. The Pydantic model
  exposes both plus `*_total` via a computed property.
- The SQLite index gains per-kind projection tables:
  `main_wear` (main_id → jump_count_derived),
  `aad_wear` (aad_id → jump_count_derived, fire_count_derived),
  `container_wear` (container_id → jump_count_derived),
  `reserve_wear` (reserve_id → repack_count_derived,
  ride_count_derived),
  `lineset_wear` (lineset_id → consumed_lb_derived).
- `reindex_from_xml` rebuilds each projection by scanning jumps
  (from R.2 onward, once rig-snapshot.xml is written) and
  repack events (from R.5 onward). Pre-R.2, projections are
  always zero; display falls back cleanly to `*_initial`.
- Jump-write in R.2 does not touch any component XML. It
  writes the jump folder (jump.xml + rig-snapshot.xml +
  attachments + SHA256SUMS) and updates the index projections
  inside the same database transaction. If the DB write fails,
  the jump folder is still present on disk and will be picked
  up by reindex.
- Manual correction API: editing `*_initial` is part of normal
  component update (R.1). No separate endpoint, no separate
  event type. The user's view of "the counter" stays as a
  single number (`*_total`).
- For counters whose `derived` is always zero in v0.1 —
  `reserve.ride_count` and `aad.fire_count` (entered at
  repack, no auto source until R.5 at the earliest; Alex's
  direction is that they stay manual entry even then) — the
  pattern is still applied for uniformity. `*_derived` sits at
  zero until, and unless, a future decision wires it in.
- `lineset.consumed_lb` follows the pattern with
  `consumed_lb_initial` + `consumed_lb_derived` as floats (lb
  of line strength). On reline, the old lineset is moved to
  `<lineset_history>` without mutating any field; the new
  current lineset starts fresh with `consumed_lb_initial = 0`.
  Archived linesets remain in the `lineset_wear` projection;
  their `consumed_lb_derived` value naturally stabilizes
  post-reline because no future jump references them
  (rig-snapshot.xml on each historical jump pins it to
  whatever lineset was current at log time, per D36).
- CLI escape hatch: `backend/scripts/recompute_counters.py`
  forces a projection rebuild without a full reindex. Calls
  the same projection code; useful if index drift is
  suspected after an out-of-band edit.

**Alternatives considered.**

- *Store a single editable counter; accept drift on crash
  mid-write.* Rejected — violates D3 (if SQLite is rebuildable,
  counters must be too) and produces silent drift that users
  cannot easily detect or self-correct.
- *Derive counters purely; no stored component-level count.*
  Rejected — used-gear setup has no jump history to derive
  from, so the jumper's only option would be to log N fake
  jumps or carry a ghost "starting value" event. Both are
  worse than a clean `*_initial` field.
- *Store counters and re-derive on a schedule, with manual
  overrides captured as "adjustment events".* Rejected as
  over-engineered for v0.1. The `initial + derived` split
  gives the same guarantees with fewer moving parts. If
  auditability of corrections becomes a requirement, adding
  adjustment events is additive.
- *Multi-folder atomic write (touch each component XML on
  jump write).* Rejected — requires new multi-folder recovery
  infrastructure superseding D25, for a workload (12–24 jumps
  per week at peak) that does not need it.
- *Store `*_initial` in XML but keep `*_derived` also in XML,
  recomputed on a background job.* Rejected — two writable
  places for the same semantic value is a drift risk. The
  index is the correct home for projections.

---

## D36 — Frozen rig state lives in `rig-snapshot.xml` inside the jump folder

**Decision.** Every jump folder created from R.2 onward contains
two authoritative XML files:

- `jump.xml` — the jump record (existing, per D2, D25, D32).
- `rig-snapshot.xml` — a frozen, denormalized copy of the rig
  and its four components (Main with nested lineset, Reserve,
  AAD, Container) and the Jumper as they were at log time.

Both files are hashed into the jump folder's single
`SHA256SUMS` manifest. Both participate in `verify_jump` and
`folder_reconcile`. The snapshot is immutable after jump
creation; updating the jump record (per D31's metadata-only
update surface) does not rewrite the snapshot.

**Why.** The canonical per-entity XMLs (`rigs/<nickname>/
rig.xml`, `inventory/mains/<uuid>.xml`, etc.) hold CURRENT
state. They evolve — relines, jumper sells a canopy, AAD gets
serviced, container retires. A jump that referenced only
`rig_id` would, two years later, resolve to a rig whose
composition has changed: the canopy installed when the jump was
made is no longer the current canopy on that rig, and the
historical record would silently mutate.

Alex's 2026-04-24 direction is explicit: "when a jump is
recorded it should take a frozen snapshot of the rig you are
jumping and store it in the jump folder." The design session
also confirmed the snapshot lives in the jump folder and is
hashed into the jump's manifest for the same tamper-evidence and
corruption-detection guarantees `jump.xml` has today.

This is the "denormalize for audit, normalize for state" split.
Current state lives on the entity XMLs (source of truth for
"what is this rig today"). Historical state lives in each jump
folder (source of truth for "what was I jumping on jump #427").
The two never drift because the snapshot is immutable once
written.

**Integrity posture.** Including the snapshot in `SHA256SUMS`
preserves the D25 guarantee that the jump folder is a self-
contained, tamper-evident, crash-recoverable unit. A silently-
corrupted `rig-snapshot.xml` is detected by the same manifest
check that protects `jump.xml`.

**Consequences.**

- `SCHEMA.v1.xsd` gains a `<rig_snapshot>` top-level element.
  It contains denormalized copies of the nested shapes already
  defined for main, reserve, aad, container, and jumper.
  Additive in v1 per D18.
- `rig-snapshot.xml` contains, at minimum:
  - Rig id, rig nickname at snapshot time, jurisdiction, rig
    `last_repack_date`.
  - Full denormalized copy of Main (with its current lineset;
    lineset_history is NOT snapshotted — only the lineset
    actually in use on this jump).
  - Full denormalized copy of Reserve, AAD, Container.
  - Full denormalized copy of Jumper, including the
    `exit_weight_lb` used for wingloading and lineset math.
  - Snapshot timestamp (D17 ISO-8601 UTC with milliseconds).
  - Counters as of snapshot time (the `*_total` value, frozen
    — neither `initial` nor `derived` alone is meaningful
    outside the rig-snapshot's context).
- Jump-write flow in R.2 produces the snapshot by reading the
  referenced rig + its components from the logbook and
  serializing into the snapshot format. A read-only set of
  reads against current state, followed by a single-folder
  atomic write. D25 preserved.
- `verify_jump` extends to also validate `rig-snapshot.xml`
  against the XSD and verify its hash against `SHA256SUMS`.
  `folder_reconcile` treats a missing snapshot the same way it
  treats a missing `jump.xml` — a corrupted folder to flag.
- Historical queries ("jumps by canopy", "what gear was on
  jump #427") resolve through `rig-snapshot.xml`. Live queries
  ("what rig am I about to jump") resolve through the entity
  XMLs. The two are independent — which is the point.
- Counter projections (D35) read from jump folders via
  rig-snapshot.xml references, not from jump.xml. This keeps
  jump.xml small and lets rig-manager-specific integration
  evolve on the snapshot shape without ever touching
  jump.xsd.
- Update semantics: per D31, `update_jump` is metadata-only in
  v0.1, so snapshots are written once on create and never
  rewritten. A later phase that extends `update_jump` to cover
  gear corrections would need to decide whether a re-snapshot
  is warranted; v0.1's answer is "no — the snapshot records
  gear-as-it-was, independent of later metadata edits."
- Attachment file paths inside the jump folder remain per D30
  (multipart-on-create). `rig-snapshot.xml` is NOT treated as
  an attachment — it is a first-class file like `jump.xml`,
  written during create, hashed, and not reachable via the
  attachment API.
- Size: denormalized four components + jumper is a few KB of
  XML per jump. Negligible on disk; readable in any text
  editor — preserves the "data outlives the app" value from
  D2.

**Alternatives considered.**

- *Extend `jump.xml` with a nested `<rig_snapshot>` element
  instead of a separate file.* Rejected — `jump.xml`'s XSD
  grows large, every change to component shapes ripples into
  jump.xsd, and a third-party validator that knows jump.xsd
  has to also know every component shape. Separating the file
  keeps jump.xsd small and lets the snapshot shape evolve
  independently.
- *Snapshot UUIDs + DOMs only; fall back to entity XML for
  everything else.* Rejected — if a component is later retired
  or deleted, the historical jump loses the ability to resolve
  its fields. The full denormalized snapshot is insurance
  against every future storage change, including component
  deletion or migration.
- *No snapshot; always resolve through current entity state.*
  Rejected per Alex's explicit direction and the silent-
  historical-mutation concern.
- *Snapshot in a sidecar folder outside the jump folder
  (`snapshots/<jump-id>.xml`).* Rejected — breaks the D25
  self-contained-jump-folder invariant. A user who copies a
  jump folder elsewhere should get the snapshot with it.
- *Skip hashing the snapshot in SHA256SUMS.* Rejected per the
  2026-04-24 session discussion. The single-manifest
  guarantee is load-bearing for D25; creating an exception
  weakens corruption detection for marginal savings.
- *Snapshot the entire `lineset_history` too.* Rejected —
  historical linesets do not affect jumps made on the current
  lineset. Snapshotting only the active lineset keeps the
  snapshot file size bounded.

---

## D37 — Component assignment and swap rules

**Decision.** Each component (Main, Reserve, AAD, Container)
carries an `<assigned_rig_id>` element on its XML:

- Absent (null) — the component is in inventory and available
  to assign. Appears in the inventory screen and in swap
  dropdowns for its type.
- Set to a rig UUID — the component is currently on that rig.
  The rig's `current_<type>_id` must match, and vice versa.

At all times, every component is in zero or one rigs. The
service layer enforces this invariant: a component cannot be
assigned to a rig while `assigned_rig_id` is already set, and a
rig cannot reference a component whose `assigned_rig_id`
disagrees with the rig's id.

Swap rules, by component type:

- **Main.** The jumper swaps the main anytime through a swap
  operation (jumper-facing API in R.1). The operation is
  atomic from the caller's perspective: the outgoing main's
  `assigned_rig_id` is cleared, the incoming main's is set to
  the rig's id, and the rig's `current_main_id` updates. The
  `feedback_small_increments` R.1 slice will land the
  multi-file atomic sequence and its crash-reconcile step.
- **Reserve, AAD, Container.** These change only through a
  repack event (R.5). There is no jumper-facing swap operation
  for them. The repack flow, when it ships, does three things
  in one operation: records the repack (appending to
  `<repack_history>` per D38), optionally replaces one or more
  of reserve / AAD / container, and re-stamps the rig's
  repack-clock driving date. Until R.5 ships, users who need
  to change a reserve / AAD / container edit rig.xml and the
  component XMLs by hand — the data remains valid per XSD.

**Why.** Assignment lives on the component, not the rig,
because:

- "Which rig is this component on" has at most one answer.
  Storing the answer on the component makes the invariant
  trivially enforceable (a single field with a uniqueness
  check at assignment time) rather than requiring scans across
  all rigs on every write.
- Components retain their own history regardless of which rigs
  they have lived on. When a jumper buys a new container and
  moves their reserve across, the reserve's counters, notes,
  and history travel with it — which is how the physical
  world works.
- Inventory queries are the fast path: "all available mains" is
  `SELECT * FROM inventory_mains WHERE assigned_rig_id IS
  NULL AND status = 'active'`. Rig composition is the slower
  path: resolve four IDs to four rows, done once per rig view.

Swap rules map to physical reality. A jumper cannot legally
re-pack their own reserve or swap an AAD between rigs — those
are rigger actions documented at the rigger's seal. The main
canopy is the jumper's to swap. Encoding this at the service
layer prevents the UI and REST surface from offering operations
the jumper cannot legally perform.

**Consequences.**

- `<assigned_rig_id>` is an optional top-level element on
  each component XSD (UUID pattern, enforced at write time).
  Lives on `main.xml`, `reserve.xml`, `aad.xml`,
  `container.xml`. Not on `jumper.xml` — jumpers do not
  belong to rigs.
- `create_component` services (R.0.3) default
  `assigned_rig_id = None`.
- `create_rig` (initial creation in R.0.3; multi-component
  swap flow refined in R.1) takes four component IDs,
  validates that each referenced component exists and is
  currently unassigned, sets each component's
  `assigned_rig_id` to the new rig's id, then writes the rig.
  Failure at any step leaves the disk in a recoverable state
  (at worst, some components are re-marked-available on the
  next reconcile).
- `swap_main` (R.1) validates that the outgoing main is on the
  rig, the incoming main is currently unassigned and
  `status = "active"`, then atomically updates both component
  files and the rig file. Crash-reconcile follows the pattern
  of `rename_jump_folder`: a reconcile step on startup detects
  partial-swap states and resolves them.
- `delete_rig` (R.1) clears `assigned_rig_id` on all four
  referenced components (returning them to inventory) before
  deleting the rig folder. Components are never cascade-
  deleted — rig deletion is a decomposition, not a
  destruction.
- Retiring, selling, or marking out-of-service a component
  that is currently on a rig returns `409 conflict` with
  `code = "component_in_use"` (per D16 RFC 9457). The
  component must be detached (via swap, repack, or delete_rig)
  before retirement.
- Inventory screen query paths in R.1 REST:
  `GET /inventory/mains?available=true` →
  `assigned_rig_id IS NULL AND status = 'active'`.
  Mirror routes for reserves, AADs, containers.
- Swap dropdown query is the same, filtered by type; exactly
  matches the rig-manager UI expectation ("only available
  components of this type appear").
- Attempted double-assignment (component already on a
  different rig) returns `409 conflict` with
  `code = "component_already_assigned"` and the existing rig's
  id in the `errors` array per D16.
- Jumpers have no assignment relationship to rigs. One jumper
  per logbook for v0.1 practically; the Jumper entity supports
  multiple records for future expansion per D33, but
  assignment of jumper-to-rig is implicit (the jumper is the
  logbook owner).

**Alternatives considered.**

- *Assignment lives on the rig only, computed on component
  reads.* Rejected — enforcement requires full scans across
  rigs on every component write, and a direct
  `assigned_rig_id` field makes uniqueness a trivial service-
  layer check.
- *Jumper can swap any component.* Rejected per the physical
  rigger-vs-jumper role split. The system should not offer
  operations the jumper cannot legally perform. Misuse would
  create silent airworthiness fiction.
- *No explicit assignment field; derive from rig-reference
  reverse lookup.* Rejected — makes "component in inventory"
  a negative query (expensive, error-prone) and makes
  enforcement of "zero or one rig" harder.
- *Allow a component to be on multiple rigs simultaneously
  for "shared gear" workflows.* Rejected — not a real-world
  workflow for personal gear, and v0.1 is explicitly single-
  jumper per D33. Shared-reserve setups in student fleets
  are explicitly deferred.

---

## D38 — Repack event shape (schema now; write flow deferred to R.5)

**Decision.** The rig XSD defines a nested `<repack_history>`
element inside `<rig>`:

```
<rig>
  ...
  <repack_history>
    <repack>
      <date>2026-04-10</date>
      <rigger>Jean Dupont</rigger>
      <jurisdiction_seal>both</jurisdiction_seal>
      <notes>Full inspection, new cypres battery.</notes>
    </repack>
    ...
  </repack_history>
  ...
</rig>
```

`<jurisdiction_seal>` is a closed enum per the D22 pattern:
`USPA | CSPA | both`. The `<notes>` element is optional free
text.

The full shape lands in R.0.2 so rig.xml can hold a repack
history from day one. The WRITE FLOW — a service method plus
REST endpoint that creates a new entry, updates related
component counters per D35, and re-stamps the rig's repack-
clock driving fields — is deferred to R.5. Until R.5 ships,
users with an existing rig set its initial repack history
either through `create_rig` at onboarding (which accepts an
initial `repack_history` list) or by hand-editing `rig.xml`,
which remains a valid v1 XML file.

Reserve ride count (`reserve.ride_count_initial`) and AAD fire
count (`aad.fire_count_initial`) are manually entered on the
respective component, per Alex's explicit direction: "Reserve
ride and aad fire are added at the repack no need to log them
automatically." They are NOT captured inside the repack event
structure. The repack event records rigger administrative
action (date, who, seal, notes). The consequences of the
repack to the reserve or AAD (a ride, a fire, a battery swap)
are reflected on the component itself via a normal update to
`*_initial`.

**Why.** Landing the XSD shape without the write flow separates
two concerns with different schedules and different risk
profiles:

- The XSD is cheap, testable in isolation, and unblocks
  `rig.xml` round-trip tests in R.0.2.
- The write flow must atomically update `rig.xml`, potentially
  recompute derived counters (per D35), and interact with any
  component whose `assigned_rig_id` is being rotated at the
  same moment (per D37). That is a full service slice with its
  own tests and failure modes — none of which need to be
  solved to land the static data model.

Keeping ride and fire counts off the repack record matches
Alex's model: the repack is a rigger administrative event, not
a component-state record. Putting those counts inside the
repack structure would duplicate what already lives on the
component and create a drift risk between "the repack that
documented the ride" and "the reserve's ride_count."

**Consequences.**

- `<repack_history>` is an ordered list (oldest first). The
  latest entry's `<date>` drives the rig's next-repack-due
  fields: when `jurisdiction == "USPA"`, `next_usp_due =
  latest.date + 180d`; when `jurisdiction == "CSPA"`,
  `next_cspa_due = latest.date + 270d`; when
  `jurisdiction == "both"`, both clocks display.
- `Rig.repack_history` is exposed on the Pydantic model as
  `list[RepackEntry]`. `RigCreate` accepts an initial
  `repack_history` (default empty). `RigUpdate` in R.1 does
  NOT allow modifying `repack_history` — that is a repack-
  flow-only mutation, deferred to R.5.
- Until R.5 lands, a jumper with a freshly-repacked rig who
  uses the service layer alone sees "next repack due: never"
  on a rig with empty `repack_history`. To see the clocks,
  they set the initial repack history via `RigCreate` at
  onboarding, or hand-edit `rig.xml` afterwards. This
  limitation is documented in `LOGBOOK_README.md` and in the
  phase handoff notes.
- The R.5 flow, when implemented, atomically appends a new
  entry to `<repack_history>`, bumps the reserve's
  `repack_count_derived` via the index projection (per D35),
  and recomputes the rig's next-due fields. If the same
  repack also swaps components, that goes through the D37
  swap path within the same service transaction.
- `<repack_history>` entries are append-only in the R.5 flow.
  Users who mis-enter a repack date go through a specific
  `repair_repack` operation (also R.5) that logs the
  correction. v0.1 does not need entry-level edit in the
  REST surface; hand-editing `rig.xml` is acceptable for the
  pre-flow period.
- Container inspections, AAD service events, and other
  rigger-touched records do NOT live in `<repack_history>`.
  Per the 2026-04-24 discussion, inspection-style records
  live as `<notes_log>` entries on the respective components.
  The repack event is narrow on purpose: it is the thing
  that drives the rig-level clock, nothing else.

**Alternatives considered.**

- *Repack events as first-class XML files under
  `events/repacks/<date>-<uuid>.xml`.* Rejected — full event
  sourcing is unwarranted for v0.1's volume (≤ 4 repacks per
  rig per year). A nested list inside `rig.xml` keeps the
  canonical per-rig XML self-contained and matches the
  "data outlives the app" value: a rigger opening `rig.xml`
  in a text editor sees the full repack history inline.
- *Record reserve ride + AAD fire inside the repack event,
  with auto-increment on save.* Rejected per Alex's
  direction. Counter values on the component are the jumper-
  facing display; duplicating them in the repack record
  creates drift risk.
- *Include rigger certification / seal number on the repack
  event for auditability.* Rejected — Alex's 2026-04-24
  direction was explicit: "don't worry about rigger expiry
  dates." Free-text rigger name is sufficient for v0.1.
- *Ship the R.5 write flow in R.0.* Rejected — the flow has
  its own tests, failure modes, and interacts with D35's
  projection and D37's component swaps. Separating the XSD
  landing from the flow landing respects
  `feedback_small_increments`.

---

## D39 — AAD rules live as a pure-function lookup in `backend/services/aad_rules.py`

**Decision.** AAD airworthiness rules — service windows, end-of-
life dates, jump-count limits, retirement flags, and mode
recommendations — live as a pure-function lookup in
`backend/services/aad_rules.py`. XML stores the FACTS about an
AAD (manufacturer, model, DOM, serial, current mode, counters);
code STORES AND VERSIONS the rules that interpret those facts.

**Field-name note (2026-04-28, R.0.2c):** the AAD's maker is
spelled `manufacturer` on the model and in the XSD, matching
main / reserve / container. Earlier text in this entry uses
`brand` for the same concept — that wording is historical and
the implementation in `aad_rules.py` keys on `manufacturer` per
D34's amendment. When R.4 lands the implementation, the
function-parameter and fact-dict-key names use `manufacturer`.

The rules cover, at minimum:

- **Airtec Cypres 2, by DOM tier.**
  - DOM ≤ 2015-12-31: 12.5-year life, mandatory service at 4
    and 8 years, ±6 month window.
  - DOM in 2016: 12.5-year life, service at 4 and 8 years
    recommended (voluntary after 2016).
  - DOM ≥ 2017-01-01: 15.5-year life, service at 5 and 10
    years recommended.
- **Advanced Aerospace Designs Vigil II / 2+ / Cuatro:** 20-
  year life, no scheduled service, 10-year factory battery
  replacement (not field-replaceable per IATA rules).
- **MarS m2:** 15 years or 15,000 jumps, whichever first. No
  scheduled service.
- **Retired models (Airtec Cypres 1, Advanced Aerospace
  Designs Vigil 1, Aviacom Argus):** auto-red — "Out of
  service — do not use. Contact your rigger."

Mode recommendations by wingloading, resolved per-brand in the
same module:

- `wl > 2.0` → high-speed mode (Cypres Speed, Vigil Xtreme,
  MarS m2 Canopy Piloting).
- `1.0 ≤ wl ≤ 2.0` → standard mode (Cypres Expert, Vigil Pro,
  MarS m2 Professional).
- `wl < 1.0` → student mode (all three brands).

Wingsuit- and tandem-specific mode recommendations are deferred
per D33.

The module exposes pure functions that take Pydantic models or
primitives and return values — no I/O, no mutation,
deterministic:

```
compute_status(aad: AAD, today: date) -> Status
recommend_mode(jumper: Jumper, main: Main, aad: AAD) -> Mode | None
is_retired_model(brand: str, model: str) -> bool
service_windows(aad: AAD) -> list[ServiceWindow]
end_of_life(aad: AAD) -> date
```

**Why.** Manufacturers change rules. Airtec updated Cypres 2
service intervals for 2017+ units; Advanced Aerospace Designs
extended the Cuatro's life cycle; any manufacturer could issue
an advisory tomorrow. If those rules were encoded in XSD or
per-AAD XML, a change would require either a schema bump
(violating D18) or a bulk rewrite of every AAD file in every
logbook.

Putting the rules in code lets them change through normal
source control. Users upgrade the app; existing AAD XMLs remain
valid; the recomputed status reflects the new rules immediately
on next read. This is D7 ("thin REST, logic in services")
applied to the AAD interpretation layer.

Pure functions without I/O give:

- Deterministic unit tests per rule (one test per tier, per
  brand, per model).
- Easy property-based testing ("an AAD is never both red and
  green simultaneously"; "the yellow window is ±6 months
  wide").
- No dependency on storage layer — the functions can be
  called from the REST layer, the reindex job, the dashboard
  query, or a future CLI, with identical results.

**Consequences.**

- `backend/services/aad_rules.py` houses the lookup table and
  the pure functions. The table is a constant data structure
  (e.g. `dict[tuple[Brand, Model], ModelRules]`). Test
  coverage validates every row and every tier boundary.
- The AAD Pydantic model (`backend/models/aad.py`) carries
  facts only: `brand`, `model`, `dom`, `serial`, `mode`,
  `is_changeable_mode`, `jump_count_initial`,
  `fire_count_initial`, plus the common component fields
  (`id`, `status`, `assigned_rig_id`, `notes_log`). It does
  NOT carry service dates or EOL — those are computed.
- Service dates and EOL are exposed on READ as computed
  fields on the `AAD` response (either via Pydantic
  `computed_field` or a wrapping response model). `GET
  /inventory/aads/<id>` includes computed fields alongside
  raw fields so clients never reimplement the rules.
- Tests land in `backend/tests/test_aad_rules.py`. A
  parameterized test per brand / model / tier combination;
  fixtures include boundary dates (last day of 2015, first
  day of 2016, first day of 2017) to verify tier edges.
- Adding a new model (e.g. Cypres 3, when it ships) is a
  single PR: entry in the lookup table, tests for the new
  rows, optional Brand/Model enum extension per D22's
  additive pattern. Existing AAD XMLs remain valid.
- The wingloading mode-mismatch UI nudge in R.4 calls
  `recommend_mode(jumper, main, aad)` and compares to
  `aad.mode`. Info-only per the 2026-04-24 discussion — no
  red/yellow color, just a suggestion with "consult your
  rigger".
- Retired-model auto-red is a special case of
  `compute_status`: the function returns red unconditionally
  for retired models, independent of DOM or counters.
- Rule changes are tracked in git history, not in
  `DECISIONS.md`. A rule update is code, not architecture.
- Mode string validation at AAD write time consults the
  rules module to reject modes that do not exist for the
  given brand/model.

**Alternatives considered.**

- *Encode rules in XSD (e.g. per-model element
  constraints).* Rejected per the update-frequency argument.
  A schema change per manufacturer update is a D18 violation
  and a terrible user experience.
- *Store rules on each AAD XML record.* Rejected — every AAD
  would carry a copy of the rules for its model. A policy
  update would require rewriting every user's AAD files, and
  divergence between user files and the "real" policy is
  inevitable.
- *Put rules in a configuration file loaded at runtime.*
  Rejected — loses git-log provenance of why a rule changed,
  and adds a loading step that is easy to get wrong. Code is
  the right home.
- *Compute status in the UI / REST adapter layer.* Rejected
  — multiple clients (REST, CLI, reindex, future SPA) would
  each need their own copy of the logic, which guarantees
  drift.
- *Express rules as a rules-engine DSL.* Rejected as over-
  engineered. The rule set is small (four brands × a
  handful of models × two or three tiers); Python `match`
  statements and plain functions are easier to read and
  test.

---

## D40 — v0.1 rig-component fields are jumper-editable; rigger-only enforcement deferred

**Decision.** In v0.1, the rig-edit and component-edit UI surfaces
treat all four component slots on a rig (Main, Reserve, AAD,
Container) as freely-editable by the jumper. The "rigger-only" lock
on Reserve / AAD / Container that D37 ratifies as the eventual
posture is **not enforced at the UI or service layer in v0.1**. It
ships in a later version, alongside (or after) the R.5 repack-event
write flow.

This supersedes D37's swap-rule enforcement in the v0.1 surface
area. D37's underlying contract — components carry
`<assigned_rig_id>`, the rig references components by ID, the data
model preserves the rigger boundary in XML — stays exactly as
written. What changes is the *runtime enforcement timeline*: the
service layer accepts component edits on Reserve / AAD / Container
without requiring a repack event in v0.1, and the UI exposes them
as editable fields with no rigger-only chrome.

**Why.** The rigger-only constraint is correct in the long run
(real airworthiness records are the rigger's, not the jumper's), but
in v0.1 there is no inventory yet, no repack flow yet (deferred per
D38), no rigger account yet, and no realistic way for a new user to
populate their rig without typing the reserve / AAD / container they
own into the form. The 2026-04-27 design session settled this:
"make it easy to use initially; we can look into locking those
fields later in another version." Forcing users through a locked
surface backed by a not-yet-built repack flow would either send
them to a text editor (D37's current pre-R.5 fallback) or block
them entirely. Both are worse v0.1 UX than provisional editability.

The deferral pattern matches D38, which lands the repack-event
*schema* in v0.1 but defers the *write flow* to R.5. D40 extends
that timeline by one phase: until the repack flow exists as the
official path for changing rigger-managed components, the UI does
not pretend it exists.

**Scope (v0.1 posture).**

- Rig edit (View 5c): all four component selectors are free-edit
  dropdowns or free-text fields. No 🔒 badges, no
  rigger-managed zone, no "Start repack…" button (or it appears
  disabled with a "ships in R.5" tooltip — UI-side preference).
- Component edit (View 5): all kinds editable by the jumper,
  including Reserve repack date, AAD service date, AAD mode.
- Rig-snapshot (D36): unchanged. The snapshot still freezes
  whatever the rig's components were at jump time. The jumper's
  ability to edit components later does not retroactively rewrite
  historical jumps because of the snapshot mechanism.
- `<repack_history>` (D38): still a nested element on `<rig>`,
  still settable on `RigCreate` for initial-setup, still not
  mutable via UI in v0.1.

**Consequences.**

- D37's swap-rule section becomes aspirational for v0.1. The
  service layer in R.1 (component CRUD + rig CRUD) treats all
  four component types uniformly: edits accepted, no rigger
  precondition. The `<assigned_rig_id>` invariant from D37 is
  still enforced — a component can be on at most one rig — but
  the *kind*-keyed swap restriction is not.
- The "Start repack…" affordance from the design exploration is
  not wired in v0.1. If the UI surfaces it at all, it points at
  R.5 ("ships in v0.2 or later").
- A future D-entry (call it D-locking, when it lands) reverses
  this: re-introduces the lock, defines the migration story for
  existing v0.1 rigs whose components were edited freely, and
  pins the enforcement layer (service layer? REST layer? both?).
- The v0.1 README and onboarding wording acknowledge this — a
  small note like "Reserve/AAD/Container are editable today;
  later releases will gate edits behind a repack event" — so the
  user is not surprised when the lock arrives.
- Pre-React mockup (since deleted; see `docs/historical-reviews.md`
  for context): View 5c rendered all four components as editable;
  View 5 (per-kind equipment edit) rendered all fields editable
  for all kinds. No 🔒 chrome.

**Alternatives considered.**

- *Honour D37 as written; ship locked UI with no rigger flow.*
  Rejected per the design session — locks the user out of any
  in-app way to set up their gear, forcing them to text-edit XML
  for first-run population.
- *Honour D37 as written; pull R.5 (repack write flow) into
  v0.1.* Rejected — R.5 is its own service slice with its own
  tests and crash semantics, and D33 already pinned the phase
  ordering. Pulling it in would expand v0.1 scope significantly.
- *Edit D37 in place to remove the rigger-only language.*
  Rejected per CLAUDE.md §4 — decisions supersede with a new
  entry, not in-place edits, so the trail of *why* the project
  chose to defer is preserved. D37's long-run posture is also
  still correct, just on a different timeline.
- *Add a UI toggle ("rigger mode") that selectively locks the
  fields.* Rejected as premature scope. v0.1 is single-jumper
  per D33; there is no rigger account, no role abstraction, and
  building a toggle for a feature that doesn't yet exist is
  speculative.

---

## D41 — `track` endpoint adopts files already on disk into a jump's manifest

**Decision.** A new narrow endpoint, `POST /api/v1/jumps/{id}/attachments/track`,
ingests files that already exist in a jump folder into `jump.xml`'s
`<attachments>` element and the folder's `SHA256SUMS`. The request body
is `{ "filenames": ["video.mp4", "..."] }` — the names of files in the
jump folder that the user wants tracked. The server reads each file
from disk, computes the SHA-256, infers a content type from the
extension when the standard library knows it, appends the entries to
the existing `<attachments>` (without disturbing existing entries),
re-validates `jump.xml` against the XSD, atomic-writes it, then
regenerates `SHA256SUMS` via `from_jump_xml`. Idempotent: re-tracking a
filename that's already tracked is a no-op (no duplicate `<attachment>`
entries, no rewrite when nothing would change).

**Why.** The Jump Detail UI (rendered via `GET /api/v1/jumps/{id}/files`)
already shows files the user dropped into the folder via the OS file
manager — they appear as untracked. The natural next step is to let
the user say "yes, count this as an attachment" without leaving the
app or hand-editing `jump.xml`. A dedicated narrow endpoint for this
specific verb is simpler than reaching for D31's full PUT-based
attachment editing surface (which also has to handle remove + replace
+ multipart-upload-on-edit, plus the D25 crash-harness orphan-delete
step). Track-only doesn't have those concerns: the file is already on
disk, the existing entries don't need to move, the only mutation is
appending to `<attachments>` + rewriting the manifest.

**Why a separate endpoint, not a body verb on PUT.** Keeping `PUT
/api/v1/jumps/{id}` metadata-only (D31) keeps that contract simple:
PUT replaces metadata, never touches the attachment array. Track is a
sub-resource action against `attachments/`, semantically a `POST`
("create new attachment records from existing files"). REST clients
that don't care about tracking ignore this endpoint entirely.

**Scope.** D41 covers exactly one operation: adopt existing on-disk
files into the manifest. It does NOT cover:

- Removing tracked attachments (still deferred per D31).
- Uploading new files to an existing jump (still deferred per D31).
- Replacing an existing attachment's bytes (still deferred per D31).

When the full D31 attachment-edit phase ships, it can either subsume
this endpoint or coexist with it; tracking-without-upload remains a
useful narrow operation that doesn't require streaming bytes through
the API.

**Contract.**

Request:

```
POST /api/v1/jumps/{id}/attachments/track
Content-Type: application/json
{
  "filenames": ["late-add.mp4", "exit-photo.jpg"]
}
```

Response: `200 OK` + the canonical `Jump` (same shape as `GET
/api/v1/jumps/{id}`), with the new `<attachments>` entries included.

Errors:

- `404 Not Found` (`code: jump_not_found`) — wrong user or id, same
  as `GET`.
- `422 Unprocessable Entity` (`code: filename_not_in_folder`) — one
  of the requested filenames doesn't exist in the jump folder.
  `errors[]` carries per-filename pointers `#/filenames/<i>`.
- `422 Unprocessable Entity` (`code: filename_invalid`) — the
  filename fails D4 sanitization (control characters, Windows
  reserved names, etc.). Same per-index pointer shape.

**Crash semantics.** Same as `update_jump`'s metadata flow (D25
"after new jump.xml, before reconcile"): `jump.xml` is written
atomically with its new `<attachments>` content, then `SHA256SUMS` is
regenerated. A crash between the two leaves the folder with an updated
XML but a stale manifest; `folder_reconcile` heals it on the next
read. The hashed bytes on disk don't move, so the worst case is a
manifest temporarily out of sync with the canonical XML — which is
exactly D25's existing recoverable state.

**Frontend integration (informational).** The Jump Detail modal's
untracked file rows get a per-file "Track" button (and, when there's
more than one, a "Track all untracked" affordance). Clicking it sends
the appropriate `filenames` array, the modal re-fetches `/files`, and
the row moves from the untracked group to the tracked group with a
fresh `sha256`.

**Alternatives considered.**

- *Track-on-read (auto-ingest any folder file on every `GET
  /jumps/{id}`).* Rejected — silent mutation on read violates the
  D2 invariant that `jump.xml` is the source of truth. The user has
  to opt in.
- *Make track part of D31's full PUT-based attachment editing.*
  Rejected — D31's flow is large enough that pulling track into it
  delays a useful, narrow operation. The `track` endpoint's behavior
  is a strict subset of what D31 will eventually offer; nothing
  in D41 prevents D31 from later subsuming it.
- *Compute SHA-256 lazily on first read.* Rejected — `jump.xml`'s
  `<attachments>` is supposed to be authoritative; storing entries
  without a sha256 weakens the integrity guarantees in D6's signing
  story (signed `jump.xml` must include all attachment hashes).
- *POST `/files/{filename}/track` (per-file URL).* Rejected for v0.1 —
  the bulk-track shape (array of filenames in one request) is more
  ergonomic for "track all untracked" UX without sacrificing the
  per-filename request capability (just send a one-element array).

---

## D42 — `POST /jumps/{id}/attachments` adds new uploads to an existing jump

**Decision.** A new endpoint, `POST /api/v1/jumps/{id}/attachments`,
accepts `multipart/form-data` with one or more `files` parts and
appends them to an existing jump's `<attachments>` element. Same
multipart shape as `POST /api/v1/jumps` (D30) — every part is
streamed via `atomic_write_stream` with SHA-256 computed during the
write, so the sha256 in the updated `jump.xml` agrees with the bytes
on disk by construction.

The service flow mirrors create_jump's attachment handling, scoped
to an existing folder:

1. Resolve the jump from the index (404 if not found / wrong user).
2. Sanitize every upload filename via `_sanitize_upload_filenames`
   (D30 — D4 character rules, NFC normalize, reject duplicates
   within the request).
3. Reject any filename that already exists in the jump's
   `<attachments>` (this is add-only — replacing an existing
   attachment is still deferred per D31).
4. Reject any filename that already exists on disk in the folder
   (untracked drop-ins must be ingested via D41's track endpoint,
   not silently overwritten).
5. Stream each upload through `atomic_write_stream` to
   `<folder>/<filename>`.
6. Append `Attachment` entries to `jump.attachments`, bump
   `updated_at`.
7. Re-validate `jump.xml` against the XSD (D2).
8. `atomic_write` the new `jump.xml`.
9. Regenerate `SHA256SUMS` from the new XML (D25 manifest order).
10. Bump the index row's `updated_at`.

Returns the canonical `Jump` with the new attachments appended.

**Why narrow.** D31 deferred *general* attachment editing — add,
remove, replace — together because the remove + replace cases force
the orphan-delete crash row in D25 ("after new jump.xml, before
delete step"). Add-only sidesteps that entirely: the only crash state
we can produce is "stream-write succeeded, jump.xml not yet updated",
which leaves an untracked file in the folder. That's already the
state D41's track endpoint handles cleanly, and `verify` already
flags it. No new D-entry crash row needed.

D41 covers the "drop-in via OS file manager, then track" half of
attachment management on an existing jump; D42 covers the "add via
the app's UI" half. Together they fill the gap between
create-time uploads (D30) and the still-deferred remove + replace
operations (D31).

**Why same multipart shape as D30.** Pywebview, browser, curl, and
any other client can reuse their existing multipart code. There's
nothing about a sub-resource POST that demands a different transport
than the parent resource. Keeping them aligned means the React
LogJumpModal's create-mode file picker and the JumpDetailModal's
add-attachment button construct identical FormData objects.

**Crash semantics.** The only mid-write crash state is what D25 §B
already names ("after first attachment, before jump.xml") — folder
has the new file but `jump.xml` and `SHA256SUMS` still reference the
pre-call state. On next read:

  * The orphan file shows up as untracked in `GET
    /api/v1/jumps/{id}/files`.
  * `folder_reconcile` does not touch it (the manifest is internally
    consistent with the un-updated `jump.xml`).
  * `verify` flags it as `extra_file`.
  * The user can ingest it via D41 `track` to complete the operation.

No automatic cleanup, matching D25's posture: XML on disk is truth,
half-written state is recoverable, never silently destructive.

**Errors.**

  * `404 Not Found` (`code: jump_not_found`) — wrong user or id.
  * `409 Conflict` (`code: filename_already_attached`) — one of the
    requested filenames is already in `<attachments>`. Use D41's
    `track` flow if the user wants to manage existing attachments.
  * `409 Conflict` (`code: filename_in_folder`) — the filename
    sanitized to a name already on disk in the folder. Use D41's
    `track` to adopt that existing file, or rename + re-upload.
  * `422 Unprocessable Entity` (`code: validation_failed`) — D30's
    sanitization errors with per-file pointers `#/files/<i>/filename`.

**Alternatives considered.**

- *Pull D31's full edit-via-PUT into v0.1.* Rejected — same reasoning
  as D31 itself. The remove + replace cases bring orphan-delete
  crash handling that's a focused phase on its own.
- *Reuse `update_jump` (PUT) with attachments in the body.* Rejected
  per D31's contract — PUT stays metadata-only so frontend code can
  reason about it without thinking about transport (PUT is JSON,
  POST `/attachments` is multipart, no overlap).
- *POST to a per-file URL like `/jumps/{id}/attachments/{filename}`.*
  Rejected — the bulk shape (one POST, N files) is already what D30
  uses on create and what the React file picker produces. Splitting
  per-file would make multi-file uploads N round-trips.
- *Auto-track files dropped into the folder on every read.*
  Rejected per D41's reasoning — silent mutation on read violates
  D2. The user has to opt in.

---

## D43 — `DELETE /jumps/{id}/attachments/{filename}` removes one attachment

**Decision.** A new endpoint, `DELETE /api/v1/jumps/{id}/attachments/{filename}`,
removes a single attachment from a jump. The service:

1. Resolves the jump + folder.
2. Confirms the filename is currently in `<attachments>` (404 if not —
   untracked drop-ins are out of scope; the user removes those via
   the OS file manager).
3. Rebuilds the Jump model with that attachment filtered out.
4. XSD-validates the new XML.
5. `atomic_write` the new `jump.xml`.
6. Regenerates `SHA256SUMS` from the new (smaller) `<attachments>`.
7. `os.unlink` the file from disk.
8. Bumps the index row's `updated_at`.

Returns `200 OK` + the updated `Jump` (same shape as `add_attachments`
and `track_files` — frontend doesn't need a refetch).

**Why narrow.** D31 deferred general attachment editing (add, remove,
replace) together because remove + replace force the orphan-delete
crash row D25 names. Delete-only doesn't actually need a new crash
row — read on.

**Crash semantics.** With the ordering above (jump.xml first,
manifest second, file unlink last), every mid-write crash lands in a
state already named by D25:

  * Crash after step 5 (jump.xml updated, manifest stale, file still
    on disk): on next read, `folder_reconcile` regenerates the
    manifest from the new XML claims. The orphaned file is now an
    untracked drop-in — visible via `GET /api/v1/jumps/{id}/files`
    with `tracked: false`, flagged by `verify` as `extra_file`. The
    user can re-`track` it via D41 or delete it from the file
    manager.
  * Crash after step 6 (jump.xml + manifest both updated, file still
    on disk): same untracked-drop-in state, just one I/O later.
  * Crash after step 7: the file is gone, `jump.xml` and manifest
    are consistent — clean state.

So the only "weird" recovery path is the orphan, and it's
indistinguishable from a file the user dragged in via Finder. The
existing tooling (D41 track, D37 list, verify) already handles it.
No new crash harness row needed.

**Why hard delete, not soft delete.** D19 soft-deletes whole jumps
to `.trash/<timestamp>_<name>/` because losing 200 jumps to a
mis-click would be catastrophic. A single attachment is a smaller
loss and the user can typically recover from Time Machine /
equivalent. Adding soft-delete here would need:

  * A defined location for trashed attachments (per-jump?
    logbook-wide?).
  * A restore flow.
  * A clean way to handle re-uploading a filename whose previous
    bytes still live in trash.

None of that is v0.1 critical. If user feedback demands it, a future
D-entry can move trashed attachments to `.trash/<jump-id>/<filename>`
without changing this endpoint's contract — the trash hop becomes an
implementation detail of step 7.

**Errors.**

  * `404 Not Found` (`code: jump_not_found`) — wrong user or id.
  * `404 Not Found` (`code: attachment_not_found`) — the filename
    isn't in the jump's `<attachments>`. Untracked drop-ins are out
    of scope here; remove those via the file manager.
  * `422 Unprocessable Entity` (`code: validation_failed`) — the
    filename failed sanitization (e.g. URL contains a path
    separator). Per-field pointer at `#/filename`.

**Frontend integration (informational).** Each tracked attachment
row in the Jump Detail modal grows a trash icon next to the Open
button. First click arms (icon turns red, label "Confirm"); second
click sends the DELETE. Untracked rows don't show the trash button —
those files aren't part of the canonical record so there's nothing
for D43 to remove (the user uses the OS file manager).

**Alternatives considered.**

- *Pull this into D31's full attachment-edit phase.* Rejected — D31's
  full surface (add, remove, replace, multipart-PUT, orphan-delete
  ordering) is meaningfully larger than just this. Shipping delete
  now unblocks user feedback on the most common operation.
- *Soft-delete attachments to `.trash/`.* Rejected per the
  scoping argument above. Cheap to add later if it turns out users
  need it.
- *Keep delete deferred entirely.* Rejected — the user-facing gap
  (no in-app way to remove a misplaced attachment) creates UX
  friction that pushes users out to the file manager, which then
  leaves jump.xml referencing a missing file (a `verify` issue).
  Better to have an in-app delete that keeps the canonical record
  consistent.

---

## D44 — Dropzone is a first-class entity; per-jump `<dropzone_id>` references it

**Decision.** A dropzone is a first-class XML record under
`logbook_root/dropzones/<uuid>.xml`. Each jump optionally references
one via `<dropzone_id>` in `jump.xml`. The dropzone carries enough
information to identify itself (`name`, `city`, `province`, `country`)
plus the field that feeds wear math (`environment`).

**Shape.**

```
<dropzone xmlns="https://skydive-logbook.example.com/schema/v1">
  <id>{uuid}</id>
  <name>Parachutisme Adrénaline</name>          <!-- required, free text -->
  <city>Saint-Jérôme</city>                      <!-- required, free text -->
  <province>QC</province>                        <!-- optional, free text -->
  <country>CA</country>                          <!-- required, ISO 3166-1 alpha-2 -->
  <environment>clean_grass</environment>         <!-- closed enum, see D45 -->
  <aircraft>                                     <!-- optional, 0..n entries -->
    <plane>
      <model>Twin Otter</model>                  <!-- required free text -->
      <tail_number>C-FXYZ</tail_number>          <!-- optional free text -->
    </plane>
    <plane><model>Cessna 208 Caravan</model></plane>
  </aircraft>
  <notes>...</notes>                             <!-- optional, free text -->
  <created_at>...</created_at>                   <!-- D32 canonical UTC ms -->
  <updated_at>...</updated_at>
</dropzone>
```

**Aircraft list (added 2026-04-28).** A dropzone may declare a fleet
of aircraft typically jumped at that DZ. Each entry is a free-text
``<model>`` (required, the plane name as the jumper would say it —
"Twin Otter", "Cessna 208 Caravan") plus an optional ``<tail_number>``
(N12345 / C-FXYZ / etc.) for the user's own bookkeeping.

  * Cardinality: 0..many. The element is omitted entirely when the
    DZ has no planes recorded (byte-stable round-trip).
  * Free text on both fields — no enum, no validation against an
    aircraft registry. v0.1 is a self-hosted personal logbook;
    cross-checking against an external registry is out of scope.
  * Listed only in the full ``Dropzone`` shape, not in
    ``DropzoneSummary`` — the picker doesn't need the fleet, only
    the full GET does.
  * Surfaces in the LogJumpModal as a typeahead on the AIRCRAFT
    field once a DZ is linked: the input becomes a combobox seeded
    with that DZ's planes (model + tail number formatted as
    ``"Twin Otter (C-FXYZ)"``). The user can still type any
    freeform aircraft string; the field stays exactly as before
    when no DZ is linked.

`environment` is a closed XSD enum: `clean_grass | dust_sand_salt | desert`.
The closed-enum discipline mirrors D22 / D34 — adding a fourth
environment is an XSD change, not a runtime config tweak, so the
wear-math contract stays auditable. Free text would let users
invent values that silently fall outside the multiplier table.

**Storage.** Flat single-file per dropzone, like jumpers and
components (D33). No attachments in v0.1; the path can be elevated
to a folder-with-manifest later additively. SQLite gets a
`dropzones` index table mirroring `(id, name, city, country,
environment, updated_at)` so the DZ picker on the jump form is
O(rows) at SQLite speed without per-row XML reads (D3 pattern).
Bumping `INDEX_SCHEMA_VERSION` to 5 triggers D26 drop-and-reindex.

**Per-jump reference.** `jump.xml` gains two siblings to `<aircraft>`:

  * `<dropzone_id>` — optional UUID. When present, the wear math
    uses that DZ's `environment` as the per-jump environment
    fallback. When absent, the math falls back to the main's
    `default_environment` (D33/D34; renamed from
    `default_environment_flags` 2026-04-28).
  * `<packed_in_poor_conditions>` — optional boolean (default
    `false`). Captures Peelman's second modifier: a packjob done
    on a windy day, dragging the canopy through dirt or salt
    spray, accrues +0.20 lb of wear regardless of the jump's
    environment.

Both are additive on `SCHEMA.v1.xsd` per D18 — no namespace bump.

**Service surface (R.D.1 / R.D.2).**

  * `POST /api/v1/dropzones` — create (JSON only; no multipart).
  * `GET /api/v1/dropzones` — list (from index).
  * `GET /api/v1/dropzones/{id}` — full record.
  * `PUT /api/v1/dropzones/{id}` — full replace.
  * `DELETE /api/v1/dropzones/{id}` — soft-delete to
    `.trash/dropzones/<timestamp>_<uuid>/<uuid>.xml` per D19.
    Existing jumps that reference the deleted DZ keep their
    `<dropzone_id>` — the reference resolves to "deleted dropzone"
    in the UI, the wear math falls back to the main's default
    flags. No cascade.

**Errors.** RFC 9457 problem+json per D16. New codes:

  * `dropzone_not_found` (404)
  * `validation_failed` with field pointers (422) — `country` not
    ISO 3166-1, `environment` not in enum, etc.

**UI integration (informational).**

  * Settings → Dropzones — list / add / edit / soft-delete.
  * LogJumpModal — DZ picker dropdown (typeahead by name+city);
    selecting one auto-fills the per-jump environment. The picker
    also offers "Quick-add new DZ" to avoid context-switching.
  * Per-jump environment override — collapsed-by-default
    "Advanced" disclosure on LogJumpModal exposing the env enum +
    `packed_in_poor_conditions` checkbox. Most jumps will use the
    DZ default; the override is for the edge case (e.g. a sand
    patch jumped at a normally-grass DZ).

**Why per-DZ environment, not just per-jump.** Jumpers don't
re-think the environment every jump — a DZ has a stable surface
character ("Saint-Jérôme is grass"). Encoding it once on the DZ
record and inheriting it onto each jump means the user fills the
field zero times for 95% of jumps. The per-jump override exists
for the 5% where the jumper actually wants to deviate.

**Why a closed enum and not free text.** D45's wear math is a
lookup on the env value. Free text would let users type "sandy"
or "Sand" or "sand and dust" and silently fall outside the table,
which would either crash the math or hide a bug behind an
else-branch. Three values cover Peelman's article verbatim
(clean / dust-sand-salt / desert) and keep the surface small
enough to display in a single radio group.

**Alternatives considered.**

  * *Per-jump environment only, no DZ entity.* Rejected — repeats
    data entry on every jump and provides no place to record DZ
    location for the "where have I jumped" stats view.
  * *DZ as a tag (free string).* Rejected — typos partition the
    same DZ into multiple entries, breaking stats grouping.
  * *Pull DZ list from a hosted online registry (DZone, USPA).*
    Rejected for v0.1 — privacy posture is self-hosted and offline,
    no external dependency. A future import can land additively.
  * *Embed `environment` directly on the jump and skip the DZ
    record.* Rejected — loses the location data the stats view
    will want, and forces re-entry of the env on every jump.
  * *Cascade-delete jumps when their DZ is trashed.* Rejected —
    deleting a DZ shouldn't touch the jump record. The reference
    becomes a dangling UUID; the UI surfaces it as "deleted
    dropzone" and the wear math falls back to the main's default
    env. Cascade would conflate two unrelated user intents (clean
    up DZ list vs. delete jumps).

---

## D45 — Line wear is Peelman's lb-budget plus additive environment / RDS / packing deltas

**Decision.** Lineset wear per jump is computed as a 1 lb baseline
plus three additive deltas — environment, RDS, and packing
conditions. This encodes Julien Peelman's published formula
verbatim:

```
consumed_lb_per_jump
  = 1.0
    + env_delta(jump)
    + (0.15 if main.has_rds else 0.0)
    + (0.20 if jump.packed_in_poor_conditions else 0.0)
```

The lineset's running consumption is the sum of
`consumed_lb_per_jump` over every jump whose `rig-snapshot.xml`
referenced this lineset's `id` (D35 — `consumed_lb_derived`),
plus `consumed_lb_initial` for used-gear setup.

**Environment deltas.**

| Environment value     | env_delta |
|-----------------------|-----------|
| `clean_grass`         | 0.00      |
| `dust_sand_salt`      | 0.20      |
| `desert`              | 0.25      |

Worked examples:

  * Clean grass, no RDS, normal packjob: `1.0 + 0.0 + 0.0 + 0.0 = 1.00 lb`
  * Dust/sand/salt DZ, RDS canopy: `1.0 + 0.20 + 0.15 + 0.0 = 1.35 lb`
  * Dust/sand/salt DZ, RDS, packed at the same dusty DZ:
    `1.0 + 0.20 + 0.15 + 0.20 = 1.55 lb`
  * Desert DZ, RDS, packed in poor conditions (worst case):
    `1.0 + 0.25 + 0.15 + 0.20 = 1.60 lb`

**RDS.** A boolean field on `<main>` (`<has_rds>`). When true,
every jump on this main's lineset gets a flat +0.15 lb delta.
This is Peelman's first modifier, verbatim. Lives on the canopy
because RDS is a physical property of the rig assembly that
applies to every jump on it; it is not per-jump and not per-DZ.

**Packing in poor conditions.** A boolean field on the jump
(`<packed_in_poor_conditions>`). Captures Peelman's "packing in
dusty / sandy / salty conditions" as a per-packjob event (a
windy DZ day, a beach demo, a packjob on a dusty mat) that
applies regardless of where the jump itself happened. When true,
the jump's lineset wear is incremented by +0.20 lb.

**Resolution order for the per-jump environment.** The wear math
needs a single environment value per jump. It's resolved as:

  1. The jump's explicit `<environment>` override, if present
     (the LogJumpModal "Advanced" knob).
  2. Else the referenced `<dropzone>`'s `environment` field,
     resolved via `<dropzone_id>`.
  3. Else the main's `default_environment` (D33/D34; renamed from
     `default_environment_flags` 2026-04-28).
  4. Else `clean_grass` (the safe baseline; never crash on
     missing data).

A reindex recomputes `consumed_lb_derived` from these resolved
values and the closed set of jumps whose rig-snapshot referenced
the lineset. The per-jump resolved value is **not** stored in
the index — it falls out of the snapshot + the current dropzone /
main records each reindex. If a DZ's environment gets corrected
later ("we paved the runway, it's not desert anymore"), the next
reindex picks it up; historical jumps recompute against current
DZ truth, which is the right behavior because the wear hasn't
actually changed retroactively, only our knowledge of it.

**Status thresholds.** Peelman's published wear-and-breaking-
strength graph draws three pink horizontal lines at residual
strength 200 / 150 / 100 lb. We adopt the same thresholds for
status colors:

  * Green: residual ≥ 200 lb (`breaking_strength_lb − total_consumed ≥ 200`)
  * Yellow: 100 lb ≤ residual < 200 lb
  * Red: residual < 100 lb (Peelman: "lines should be replaced")

Where `total_consumed = consumed_lb_initial + consumed_lb_derived`.

These are absolute thresholds, not relative to exit weight,
because they reflect the strength of the line itself and not
the load on it. A 400-lb V400 consumed down to 90 lb residual is
unsafe regardless of whether it's flying a 130-lb jumper or a
220-lb jumper.

**Visual-inspection caveat.** The status color is a tracker, not
an airworthiness determination — same posture as D33's rig
status colors. Every red surfaces "see your rigger" copy. The
math is a tool to know when to look; it is not a permission to
keep jumping because the math is green. PD's official position
remains "have a rigger inspect lines on schedule"; this module
makes that schedule proactive instead of reactive.

**Sources.**

  * Julien Peelman, JYRO. *They're Probably Still Good… Maybe.*
    PIA Symposium presentation. Recorded talk:
    https://www.youtube.com/watch?v=eyy5d1LbIY4
  * Alethia Austin. *How Many Jumps Are Left in Your Lines?*
    Skydive Mag, summarizing Peelman's formula and modifiers.

**Why additive, not multiplicative.** Peelman's article phrases
the modifiers as "+15%" and "+20%" — grammar that maps to
addition of percentage-point deltas, not chained percentage
multiplications. Compounding them (1.15 × 1.20 = 1.38) drifts
above his published numbers without him saying so. Additive
(1.15 + 0.20 = 1.35) matches his text verbatim and keeps the
worst-case ceiling at 1.60 instead of 1.725 — a less aggressive
curve that errs on the side of trusting the published data
rather than amplifying it.

A second reason: additive deltas make the math interpretable in
the UI. "This jump cost 1.35 lb because it was sandy (+0.20) and
RDS (+0.15)" reads as a checklist of contributions. A
multiplicative version would have to show "+38%" with a
footnote explaining how 15% and 20% combined non-linearly,
which is harder to reason about.

**`dust_sand_salt` env vs `packed_in_poor_conditions` flag — not
redundant.** They are conceptually different wear sources and
can independently apply on the same jump. The env delta captures
ambient abrasion (canopy dragged through dust on opening, fine
particles on the surface). The packing flag captures grit packed
*into* the canopy at packing time (a windy day, a beach demo, a
sandy mat) regardless of where the jump itself happened. A
jumper packing on a dusty mat at a clean-grass DZ accrues +0.20
from the packing flag and 0.00 from env. A jumper at a
dust/sand/salt DZ who packed indoors before driving to the
field accrues +0.20 from env and 0.00 from packing. Both checked
is the "packed at the dusty DZ I'm jumping at" case, where the
wear is genuinely double-sourced and the +0.40 is correct.

**Why these modifiers and no others.** Peelman publishes exactly
two modifiers (RDS +15%, dust/sand/salty packing +20%). Adding a
`desert` ambient delta was Alex's call (2026-04-27) to handle
the gap in Peelman's data — he doesn't publish a separate number
for ambient desert conditions vs grass with a sandy packjob. We
keep desert at +0.25, one notch above dust-sand-salt, rather
than inventing a larger gap. If user data later shows desert DZ
linesets wearing meaningfully faster, the delta can be tuned in
a future D-entry without changing the formula's shape.

**Why absolute lb thresholds, not relative.** Two reasons. First,
Peelman's graph publishes absolute thresholds — 200 / 150 / 100
lb residual — which ties our display to recognizable industry
numbers. Second, line strength is a property of the line, not
the load: a canopy with 90 lb residual is unsafe regardless of
who flies it. A jumper with a 1.0 wingloading on a worn V750 is
not in less danger than a 2.0 jumper on the same lineset — the
lines fail at the same load.

**Alternatives considered.**

  * *Multiplicative modifiers (1.0 × 1.20 × 1.15 × 1.20 = 1.66).*
    Rejected per the additive-vs-multiplicative argument above —
    Peelman's text is additive; chaining drifts above his
    numbers and obscures the per-modifier contribution in the UI.
  * *Use a finer-grained env delta table (clean / coastal /
    sandy / coral / desert).* Rejected — Peelman publishes one
    modifier (1.20) for the broad "non-clean" bucket; inventing
    finer grades would extrapolate beyond his data. Three values
    keep the model close to source.
  * *Make the deltas user-tunable in Settings.* Rejected for
    v0.1 — the wear math is a contract; per-user tuning
    fragments the model and makes "your lineset has X jumps
    left" incomparable across users. Future scope if demand
    exists.
  * *Compute thresholds relative to exit weight (e.g. red when
    residual < 1.5× exit_weight_lb).* Rejected per the
    line-strength-not-load argument above. Exit weight is
    already in the formula via the lineset's
    `install_exit_weight_lb`, captured in `breaking_strength_lb
    − install_exit_weight_lb` = starting budget. The thresholds
    bound the *remaining* budget, not the load.
  * *Store the resolved environment per jump in `jump.xml` (not
    just on the DZ).* Rejected — it duplicates a value already
    derivable from the DZ reference plus the optional override,
    and means a DZ environment correction does not propagate to
    historical jumps at reindex.

---

## D46 — Lineset wear seed is jumps-not-pounds; exit weight is live-read from the active jumper

**Decision.** Two changes, supersedes D34 and D45 on the affected
fields:

  1. `Lineset.install_exit_weight_lb` is **removed**. D45's
     starting-budget formula reads `exit_weight_lb` live from the
     active `Jumper` record at status-compute time, not from a
     snapshot stored on the lineset.
  2. `Lineset.consumed_lb_initial: float` is **renamed** to
     `Lineset.jumps_on_lineset_initial: int` and changes type
     accordingly (`xs:nonNegativeInteger` in the XSD).
     D45's wear math reinterprets the seed as a count of pre-
     logbook jumps on this lineset, not a pre-spent lb budget.

**Why exit weight moves off the lineset.** Exit weight is a
property of the *jumper*, not of the install event. Snapshotting
it at install time means a canopy passed to a different jumper
carries a stale weight forever, and a jumper's own weight changes
(real-life weight gain/loss) silently orphan the snapshot from
current truth. Live-read is consistent with D45's "DZ env
correction at reindex shifts historical wear" stance — we
recompute against current truth rather than freezing past beliefs.

**Why the seed becomes a jump count.** Riggers, when seeding used
gear, know "X jumps on the current lineset", not "X lb of budget
already consumed". The old `consumed_lb_initial` required the user
to mentally apply Peelman's per-jump multiplier (~1.0–1.6 lb) to
their hand-counted jump number, then enter the product. That's a
unit-conversion the model should do, not the user. The field name
should match the user's mental model.

**Math impact (supersedes D45 lines on `install_exit_weight_lb` /
`consumed_lb_initial`).**

```
starting_budget_lb = breaking_strength_lb − jumper.exit_weight_lb     # live read
total_consumed_lb  = (jumps_on_lineset_initial × 1.0) + consumed_lb_derived
residual_lb        = breaking_strength_lb − total_consumed_lb
```

The `× 1.0` factor on the seed is the **baseline assumption** for
pre-logbook jumps where env / RDS / packing data is unavailable.
It encodes "treat each migrated jump as a clean-grass, no-RDS,
clean-packjob jump" — Peelman's baseline. This intentionally
under-counts wear for migrated jumps that were actually flown in
dust/sand/RDS/poor-packing conditions; the remediation is that the
status thresholds (200 / 100 lb residual) sit well above zero, so
the under-count gets caught long before the lines actually fail.

`consumed_lb_derived` (D35) is unchanged — it accumulates the full
Peelman per-jump formula over every logbook jump whose
`rig-snapshot.xml` references this lineset's id.

Status thresholds (200 / 100 lb residual, D45 §"Status thresholds")
are unchanged.

**Posture: this is an inspection-suggestion tool, not an
airworthiness call.** The widget's job is to nudge the jumper
toward a rigger inspection on a reasonable schedule. It is
explicitly *not* an authoritative verdict on whether the lineset is
safe to jump. Same posture as D33's status colors and D45's "see
your rigger" copy. Approximation is acceptable here in a way it
would not be acceptable for the on-disk model itself; missing 30%
of pre-logbook wear because we couldn't reconstruct env data is
fine, because the status colors are conservative and the only
action they prompt is "go talk to your rigger".

**Multi-jumper.** v0.1 is single-jumper (D33) so "live-read from
the active jumper" is unambiguous — there is exactly one jumper
record. When multi-jumper lands (currently deferred), the D-entry
that introduces it must specify which jumper's exit weight is read
for D45 budgets — almost certainly "the rig's primary owner" but
out of scope here.

**Consequences.**

  - Pydantic `Lineset` model: drop `install_exit_weight_lb`;
    rename `consumed_lb_initial: float` → `jumps_on_lineset_initial:
    int` (with `ge=0` matching the XSD `xs:nonNegativeInteger`).
  - Main XSD `LinesetType` complex type: drop the
    `<install_exit_weight_lb>` element; rename
    `<consumed_lb_initial>` (xs:decimal) →
    `<jumps_on_lineset_initial>` (xs:nonNegativeInteger).
    Schema-version bump per D18.
  - Main XML serialize / deserialize updated for the new shape.
  - `MainCreate` / `MainUpdate` payloads accept the new shape;
    Pydantic `extra="forbid"` rejects the old field names with 422.
  - SQLite index column for `lineset_consumed_lb_derived` is
    untouched — the derived value is the result of D45 wear math,
    not the seed. Reindex bumps `INDEX_SCHEMA_VERSION` per D26 and
    rebuilds.
  - Frontend `AddComponentModal` drops the INSTALL EXIT WEIGHT
    field. CONSUMED renames to JUMPS ON LINESET (integer input).
  - Frontend `ComponentDetailModal` exposes the lineset fields in
    its Edit body for mains, so existing components can have their
    lineset metadata corrected post-hoc.

**Pre-v0.1 break.** No real users yet. Existing on-disk
`main.xml` files containing `install_exit_weight_lb` or
`consumed_lb_initial` will fail XSD validation at load. We accept
the clean break in lieu of writing migration code for a single
developer's test logbook.

**Alternatives considered.**

  * *Snapshot exit weight at install on Lineset (preserve install-
    time history).* Rejected — D45's reindex-against-current-truth
    posture would shift the snapshot under the user anyway, and
    Alex explicitly wanted live read. The "this lineset was
    installed for a 180-lb jumper" historical fact, if needed
    later, can be reconstructed from `jump.xml` + the jumper's
    weight history (D33 keeps `exit_weight_updated_at` per the
    Jumper model).
  * *Keep both `jumps_on_lineset_initial` and
    `consumed_lb_initial` (compute one from the other).*
    Rejected — two fields encoding the same fact invite drift;
    pick the field that matches the user's mental model.
  * *Make the per-migrated-jump baseline (1.0 lb) configurable
    per lineset.* Rejected — adds knobs to a tool whose stated
    purpose is "approximate enough to suggest an inspection".
    Per-migrated-jump precision is not worth the UI complexity;
    if a user wants sub-1.0 baseline tracking they can leave
    `jumps_on_lineset_initial = 0` and let the live wear math run.
  * *Snapshot `exit_weight_at_install_lb` as an optional
    informational field, separate from the budget math.*
    Rejected — adds a field that is never read by anything, just
    to record a historical curiosity. If the use case appears
    later, a future D-entry can re-introduce it.

---

## D47 — Jumper credentials, attachments, and tandem currency

**Decision.** The v0.1 Profile surface gains a credential record on the
jumper: federation memberships (CSPA, USPA, "other"), federation
Certificates of Proficiency (CSPA Solo/A/B/C/D, USPA A/B/C/D),
federation ratings (closed enum per known org, free-text for "other"),
manufacturer-issued tandem instructor ratings (UPT Vector, UPT Sigma,
Strong Dual Hawk, "other"), and government-issued aviation medicals
(Class III). Each credential carries the user-entered expiry / issued
date and may reference a single attachment (PDF or image of the card).
Tandem ratings additionally carry a manual `currency_reset_at` date
that lets the jumper dismiss the "not current" warning after a
supervised re-currency jump. Currency for tandem ratings is a derived
projection over the jump index, scoped by manufacturer rule.

**Why.** Skydiving is regulated by parallel credential streams
(federation, manufacturer, government) and every one of them expires
and needs re-confirmation. The logbook is the natural home for the
record because the jumper already opens it on every flight. The
expiry-warning case Alex flagged ("a month before, remind me to
renew") only works if the expiry dates live next to the jump activity
and the cards. Recording the credentials also unlocks future scope
(rig snapshots could carry the TI rating active at log time; printable
rigger handoff sheets could pull the card; export to a national
federation could become a one-click flow). v0.1 surfaces the data and
the warning only — no automation.

The shape (five parallel collections rather than one polymorphic
`<credential>` with a `kind` discriminator) was chosen so that XSD
validation is exhaustive without xsi:type tricks, so each collection
gets its own closed enum at the right scope, and so the on-disk shape
stays self-describing to a human editor (D5).

**Scope.**

### Five parallel credential collections on `<jumper>`

The current `JumperContent` complex type (D33: id, name, exit weight,
exit_weight_updated_at, audit timestamps) gains five optional sibling
collections plus an attachments registry. Each collection is itself
optional and elides when empty, keeping a freshly-created jumper file
compact:

```xml
<jumper>
  <id>…</id>
  <name>…</name>
  <exit_weight_lb>…</exit_weight_lb>
  <exit_weight_updated_at>…</exit_weight_updated_at>

  <memberships>     <!-- 0..n federation memberships -->
    <membership>…</membership>
  </memberships>

  <cops>            <!-- 0..n Certificates of Proficiency / licenses -->
    <cop>…</cop>
  </cops>

  <ratings>         <!-- 0..n federation ratings -->
    <rating>…</rating>
  </ratings>

  <tandem_ratings>  <!-- 0..n manufacturer tandem instructor ratings -->
    <tandem_rating>…</tandem_rating>
  </tandem_ratings>

  <medicals>        <!-- 0..n government-issued aviation medicals -->
    <medical>…</medical>
  </medicals>

  <attachments>     <!-- 0..n attachment records (cards, medical certs) -->
    <attachment>…</attachment>
  </attachments>

  <created_at>…</created_at>
  <updated_at>…</updated_at>
</jumper>
```

Cardinality is 0..n on each collection. A jumper holding both CSPA and
USPA memberships gets two `<membership>` entries; a jumper holding both
UPT Vector and UPT Sigma TI ratings gets two `<tandem_rating>` entries.

The same `JumperContent` complex type is reused inside `<rig_snapshot>`
per D36. Per the existing D36 pattern (`MainContent`'s
`lineset_history`), the snapshot writer simply does not populate the
credential collections. The XSD tolerates either shape; the per-context
invariant is enforced by the writer, not the schema.

### `MembershipType` — federation membership card

Fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | UUID | yes | Stable identifier; survives renames. |
| `org` | OrgEnum | yes | Closed enum: `CSPA`, `USPA`, `OTHER`. |
| `org_other` | string (1..120) | only when `org=OTHER` | Free-text federation name. |
| `member_number` | string (1..40) | yes | As shown on the card. |
| `expiry_date` | xs:date | yes | User-entered from card. CSPA = anniversary cycle, USPA = calendar year (Jan 1 – Dec 31), other = whatever the federation prints. |
| `card_attachment_id` | UUID | optional | Reference into `<attachments>` collection on the same jumper. |
| `notes` | string | optional | Free text. |

Sources for the enum and cycle facts: cspa.ca / Become Certified
(https://www.cspa.ca/en/learn-skydive/get-certified) and USPA
Membership FAQ
(https://www.uspa.org/experienced-skydivers/uspa-membership/membership-faq).

### `CopType` — federation Certificate of Proficiency / license

The term "CoP" follows CSPA's canonical usage (Certificate of
Proficiency); USPA uses "license" but the data shape is the same.

Fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | UUID | yes | |
| `org` | OrgEnum | yes | `CSPA`, `USPA`, `OTHER`. |
| `org_other` | string | only when `org=OTHER` | |
| `level` | CSPACopLevel \| USPACopLevel \| string | yes | Per-org closed enum (see below); free text when `org=OTHER`. |
| `issued_date` | xs:date | yes | CoPs do not expire by date. They become "null and void" if currency lapses (USPA SIM 3-1, CSPA MOI). v0.1 does not compute CoP currency — that is a future slice; we record the issued date so the warning machinery can be added additively later. |
| `card_attachment_id` | UUID | optional | |
| `notes` | string | optional | |

`CSPACopLevel` closed enum: `solo`, `a`, `b`, `c`, `d`. CSPA writes
these as "Solo Certificate" / "A CoP" / "B CoP" / "C CoP" / "D CoP";
the lowercase letter form is the canonical short code on cspa.ca's
own URLs (`/en/cop`, `/en/learn-skydive/get-certified/b-cop`, etc.).

`USPACopLevel` closed enum: `a`, `b`, `c`, `d`. USPA SIM 3-1
(https://www.uspa.org/sim/3-1).

### `FederationRatingType` — federation-issued rating

Fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | UUID | yes | |
| `org` | OrgEnum | yes | |
| `org_other` | string | only when `org=OTHER` | |
| `code` | CSPARatingCode \| USPARatingCode \| string | yes | Per-org closed enum; free text for "other". |
| `expiry_date` | xs:date | yes | User-entered; per-rating renewal cycles vary (most are annual). |
| `card_attachment_id` | UUID | optional | |
| `notes` | string | optional | |

`CSPARatingCode` closed enum (CSPA cspa.ca/en/ratings/* + Currency
page, all confirmed 2026-04-29):

```
c1                      Coach 1
c2                      Coach 2
c3_wingsuit             Coach 3 — Wingsuit
c3_canopy_piloting      Coach 3 — Canopy Piloting
c3_freefly              Coach 3 — Freefly
c3_canopy_formation     Coach 3 — Canopy Formation
cdc                     Competition Development Coach
jm                      Jump Master (full)
jmr                     Jump Master Restricted (freefall only)
gci                     Ground Control Instructor
ssi                     Skydiving School Instructor
pffi                    Progressive Free Fall Instructor
sse                     Skydiving School Examiner
lf                      Learning Facilitator
rigger_a                Rigger A
rigger_a1               Rigger A1
rigger_a2               Rigger A2
rigger_b                Rigger B
rigger_instructor       Rigger Instructor
rigger_examiner         Rigger Examiner
ejr                     Exhibition Jump Rating (annual currency, not a permanent rating)
```

CSPA Tandem Instructor is **deliberately omitted** from this enum.
Tandem ratings are manufacturer-issued, not federation-issued, and
live in `TandemRatingType` (next section). CSPA does not appear to
publish a Tandem Instructor rating program in publicly accessible
materials (cspa.ca was searched 2026-04-29 — no public Tandem
Instructor page, currency rule, or course materials found).

`USPARatingCode` closed enum (USPA SIM § 6 + uspa.org/IRM, confirmed
2026-04-29):

```
coach                   Coach
affi                    AFF Instructor
iad_i                   IAD Instructor
sl_i                    Static Line Instructor
ti                      USPA Tandem Instructor (recognition rating; requires manufacturer rating)
coach_examiner          Coach Examiner
affi_examiner           AFF Instructor Examiner
iad_examiner            IAD Instructor Examiner
sl_examiner             Static Line Instructor Examiner
ti_examiner             Tandem Instructor Examiner
course_director         Course Director (any discipline; the discipline is captured in notes)
iecd                    Instructor Examiner Course Director
pro                     PRO rating (exhibition jumps; SIM § 7)
sta                     S&TA — Safety & Training Advisor (USPA appointment, not a rating per se; modeled as a rating with an annual expiry tied to the March 31 reappointment cycle for simplicity)
```

USPA-issued TI is included even though the manufacturer rating is
modeled separately, because USPA enforces its own currency overlay
(15 tandems / 12 months + 1 / 90 days + annual seminar — confirmed
from instructorsacademy.com / USPA IRM extracts) on top of the
manufacturer rule.

### `TandemRatingType` — manufacturer tandem instructor rating

Fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | UUID | yes | |
| `system` | TandemSystem | yes | Closed enum (next paragraph). |
| `system_other` | string | only when `system=OTHER` | |
| `expiry_date` | xs:date | yes | User-entered from card; reflects the manufacturer's annual recertification cycle. |
| `card_attachment_id` | UUID | optional | |
| `currency_reset_at` | xs:date | optional | The dismiss-the-warning manual override. When set within the manufacturer's currency window, the calculator treats the jumper as current regardless of recent jump activity. |
| `notes` | string | optional | |

`TandemSystem` closed enum (the values are tandem rig-system tokens,
not manufacturer names — UPT makes both Vector and Sigma; the rating
is scoped to the system, not the company, hence the type name):

```
upt_vector              UPT Vector tandem system
upt_sigma               UPT Sigma / Sigma II tandem system
strong_dual_hawk        Strong Enterprises Dual Hawk Tandem
other                   free-text system name in system_other
```

UPT splits Vector and Sigma as separate ratings (skydiveratings.com
"Sigma/Vector Tandem Rating", confirmed 2026-04-29) — they share the
same instructor-side training but require separate endorsement.

Why no `<medical_attachment_id>` on `TandemRatingType`: a jumper holds
one Class III medical at a time and it covers all their tandem
operations. The medical is its own collection (`MedicalType` below).

### `MedicalType` — government-issued aviation medical

Fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | UUID | yes | |
| `kind` | MedicalKind | yes | Closed enum: `class_iii`. v0.1 only models the class commonly required for tandem operations (FAA Class III for US, Transport Canada Class III for CA, foreign equivalents accepted by the manufacturers). Other classes can be added additively. |
| `issuing_authority` | string (1..120) | yes | Free text — user types whoever printed the card. |
| `expiry_date` | xs:date | yes | User-entered from the medical certificate. |
| `card_attachment_id` | UUID | optional | |
| `notes` | string | optional | |

Per UPT (xcelskydiving.com TICC / UPT FORM-267), Strong
(certificationunlimited.com strong-tandem), and CARs Part IV (cited
unverified — see "Could not confirm" below): tandem instructors must
hold a current Class III medical or a foreign equivalent at the time
of jumping, but USPA and the manufacturers do not enforce continuous
medical validity on the rating itself. The logbook records the
expiry; the warning surfaces in the UI when within 30 days of expiry
or already expired.

### Jumper attachments — folder and file layout

Each jumper gets a folder under `logbook_root/jumpers/<id>/` with a
`jumper.xml` plus an `attachments/` subfolder, mirroring the existing
rig folder pattern (D33). This is a layout change for jumpers — the
current shape is a flat single file at `logbook_root/jumpers/<id>.xml`.

Migration: `bootstrap_logbook` (D29) and the jumper service detect the
flat shape, move `<id>.xml` → `<id>/jumper.xml` atomically (per D10
`atomic_write`), and create the empty `attachments/` subfolder. No
file content changes.

```
logbook_root/
  jumpers/
    <jumper_id>/
      jumper.xml
      SHA256SUMS
      attachments/
        <attachment_uuid>__<safe-filename>.<ext>
```

Each `<attachment>` element under `<jumper>/<attachments>` reuses the
existing `AttachmentType` (filename / sha256 / size / content_type)
plus an additional `id` field of type UUID so credentials can
reference attachments by id rather than by filename. Filename
sanitization rules from D4 / sanitize_filename apply unchanged.

Why folder-with-manifest rather than flat-file-only: D33's rig folders
already established the convention; mirroring it here keeps
`bootstrap_logbook` and `verify` symmetric. SHA256SUMS lets the
attachment bytes participate in integrity verification.

### Tandem flag on `<jump>` — the jump-side input to the currency calculator

`<jump>` gains an optional boolean child:

```xml
<is_tandem>true</is_tandem>
```

Semantics: "I jumped this as the Tandem Instructor." Absent ≡ `false`.

The currency calculator counts jumps with `is_tandem=true` falling
within the manufacturer's window. v0.1 does not split by manufacturer
(a jumper rated on both Vector and Sigma flies tandems on the rig
they have that day; jumps don't carry the rig's tandem-system
identity). If a future requirement needs per-system counts, an
additional optional field can land additively without breaking
existing data.

### Currency calculator — service-layer pure function

`backend/services/credential_currency.py:compute_tandem_currency(
jumper, system, jump_index_view) -> CurrencyState` returns:

```python
@dataclass
class CurrencyState:
    is_current: bool
    reason: str               # human-readable, e.g. "3 of 3 in 90 days, 25 of 25 in 365 days"
    expires_on: date | None   # earliest-failing window's lower bound
    rule_source: str          # "UPT FORM-267" | "Strong Currency Requirements"
```

Per-system rules:

| System | Rule | Source |
|---|---|---|
| `upt_vector`, `upt_sigma` | ≥ 3 tandems in last 90 days **AND** ≥ 25 tandems in last 365 days | UPT Sigma Tandem Operations Manual; UPT FORM-267 ("Tandem Instructor Re-Certification Form"). https://uptvector.com/wp-content/uploads/2025/12/Man016-Rev0-Sigma-Tandem-Operations-Manual.pdf |
| `strong_dual_hawk` | ≥ 1 tandem in last 12 months → "current". Within 90 days → fully current. 90 d – 6 mo → "needs SOP/EP review + 1 supervised". 6 mo – 12 mo → "needs SOP/EP + 1 with current TI". 12 mo+ → "needs Examiner recurrency seminar". | https://strongparachutes.com/Tandem/Instructors/Currency-Requirements |
| `other` | rule unknown; calculator returns `is_current=None` and surfaces "rule not modeled — please confirm with your card / your DZ" | — |

The manual override:

```
If tandem_rating.currency_reset_at is set within the last 90 days
(UPT) or the last 12 months (Strong), is_current = True regardless
of jump activity.
```

The reset only changes the UI suppression; the underlying jump-derived
counters remain visible in the detail view so the user can see what
the calculator is reading.

### REST endpoints (additive)

~16 new endpoints under `/api/v1/jumpers/{id}/`: POST/DELETE for
`attachments`; POST/PUT/DELETE for each of `memberships`, `cops`,
`ratings`, `tandem-ratings`, `medicals`; PATCH for
`tandem-ratings/{id}/currency-reset`; GET for
`tandem-ratings/{id}/currency` → `CurrencyState`. Full surface listed
in the OpenAPI spec.

The existing `PUT /api/v1/jumpers/{id}` continues to replace the
identity fields (name, exit weight) only; credential collections
route through the dedicated endpoints so each gets its own validation
surface and hint channel (D24).

### Attachment endpoint shape

`POST /api/v1/jumpers/{id}/attachments` accepts `multipart/form-data`
identical to the jump-attachment endpoint (D30): the file part plus a
JSON metadata part. Returns the new attachment's UUID. The credential
endpoints accept the attachment UUID; a missing-attachment reference
fails validation with RFC 9457 error (D16).

**Out of scope for D47 (explicit non-decisions).**

- **CoP currency** (the "60 days for A, 90 for B/C/D" USPA rule that
  voids a license). Recorded only as `issued_date`; the calculator
  for "is your license valid right now" lands additively when the
  user asks for it.
- **USPA TI overlay rule enforcement.** USPA's 15/12 + 1/90 + annual
  seminar rule is recorded as a separate `FederationRatingType` entry
  with `code=ti` and a user-entered expiry; v0.1 does not compute the
  USPA-overlay currency from jump activity. Manufacturer currency is
  enough for the v0.1 warning use case.
- **Federation-specific currency rules for non-tandem ratings**
  (CSPA C1's 10 coaching contacts + 25 jumps; PFFI's 10 PFF jumps,
  etc.). User enters the expiry date; jump-derived currency is a
  future slice.
- **Push notifications, calendar export, federation API integration,
  digital wallet card.** All deferred.
- **Annual instructor seminar tracking.** The seminar drives
  recertification but is not a credential per se. v0.1 captures it
  implicitly (the user updates the rating's expiry date when they
  attend).
- **Multi-jumper.** D33 already deferred this; D47 keeps the same
  posture (one jumper file, but the entity supports n).
- **Rigger ratings as separate from `FederationRatingType`.** CSPA
  Rigger A/A1/A2/B sit in the rating enum for v0.1. Splitting them
  into their own entity (with manufacturer-of-rig-trained-on, expiry,
  etc.) is a future slice if the data outgrows the current shape.
- **Tandem manufacturer fees / rig-owner permissions.** UPT requires
  some recertifications to be signed by a Sigma/Vector rig owner;
  this is an organizational fact, not a logbook fact.

**Could not confirm (cited as such in code comments).**

1. **Class III medical validity cycle under CARs Part IV** (Transport
   Canada). The exact age-banded rule (e.g., 5 yr under 40 / 2 yr over)
   was not retrievable from public sources during research. v0.1 stores
   the user-entered expiry; no app-side computation.
2. **CSPA Tandem Instructor program existence.** Not found on cspa.ca;
   may live in the Coach & Instructor Manual (member-area). If a
   future review of the CIM confirms a CSPA-side TI rating, it lands
   additively.
3. **CSPA membership cycle (anniversary vs calendar).** Alex confirmed
   from personal experience that CSPA runs anniversary; cspa.ca did
   not corroborate this in public materials. Stored as user-entered
   `expiry_date` so either cycle is captured.
4. **USPA SIM § 6-1 verbatim text** for tandem currency. Inferred from
   instructorsacademy.com and the USPA IRM extracts; the SIM itself is
   at uspa.org/SIM but the relevant section was not directly quotable
   in research. The schema records the rule via `FederationRatingType`
   with `code=ti`; v0.1 doesn't compute the USPA-overlay rule.

**Consequences.**

- **XSD v1.x bump.** Additive changes per D18: new simple types
  (`OrgEnum`, `CSPACopLevel`, `USPACopLevel`, `CSPARatingCode`,
  `USPARatingCode`, `TandemSystem`, `MedicalKind`), new complex
  types (`MembershipType`, `CopType`, `FederationRatingType`,
  `TandemRatingType`, `MedicalType`, extended `AttachmentType` with
  optional `id`), new optional elements on `JumperContent` and on
  `<jump>` (`is_tandem`). No breaking change to existing files.
- **Pydantic models.** New sub-models in `backend/models/jumper.py`;
  `Jumper` gains five list fields and an `attachments` list. New
  `is_tandem: bool | None` field on `Jump`.
- **Storage layout migration.** `bootstrap_logbook` (D29) detects flat
  `jumpers/<uuid>.xml` and moves to `jumpers/<uuid>/jumper.xml` +
  `attachments/` + `SHA256SUMS`. Idempotent; safe to re-run.
- **Service layer.** New `credential_service.py` covering CRUD for
  the five collections. New `credential_currency.py` (pure function
  per-manufacturer dispatch).
- **REST surface.** ~16 new endpoints (listed above). All emit
  RFC 9457 errors per D16; all use the `_hints` channel per D24
  where useful (e.g., "expiry is within 30 days" hint on credential
  GET).
- **SQLite index extension.** Two new tables: `jumper_credentials`
  (denormalized projection across all five collection kinds for
  expiry-warning queries) and a small change to `jumps` (one new
  column: `is_tandem` boolean, indexed). Per D3, both are derivable
  from XML — `reindex` rebuilds them.
- **Frontend (deferred to Phase F per phasing in this entry).**
  `Profile.jsx` gains five collection editors (memberships, CoPs,
  ratings, tandem ratings, medicals) plus an attachment picker. Each
  row chips its expiry with green / yellow (within 30 d) / red
  (past). A separate "Tandem currency" widget per tandem rating shows
  derived state and the manual reset button.
- **Test surface.** New roundtrip tests for each complex type, crash
  tests for the migration, currency-calculator tests with synthetic
  jump histories (fixtures cover: fully current, lapsed 90 d, lapsed
  365 d, reset within window, reset outside window, system=other).

**Alternatives considered.**

- *Single polymorphic `<credential>` element with a `kind`
  discriminator.* Rejected: XSD validation of variant fields per
  kind requires xsi:type, which breaks the "self-describing to a
  human editor" property of D5 — a mid-file `xsi:type="MembershipType"`
  is meaningfully harder to read than `<membership>`. Five sibling
  collections also let each closed enum live at the right scope.
- *Separate XML files per credential* (one file per membership,
  rating, etc.) *rather than nested under the jumper.* Rejected:
  the credential record is part of the jumper's identity. v0.1
  ships single-jumper; even at multi-jumper scale, owning the
  credentials inside the jumper's folder keeps the
  one-folder-per-jumper invariant clean. Rig snapshots already
  prove that nested elements at write time work fine (D36).
- *Computed currency state stored in XML.* Rejected: violates D3
  (SQLite is the only derivable-from-source store). The reset date
  is the only piece of currency state that is not derivable from
  jumps + clock; it lives in XML. Everything else is computed.
- *Tandem system enum as free-text string.* Rejected: the warning
  calculator dispatches on system, so the value must be a known
  token. Free text would require parsing or a lookup table outside
  the schema. Closed enum with `OTHER` escape hatch is cleaner.
- *Tandem-specific medical attached to `TandemRatingType` rather
  than its own `MedicalType` collection.* Rejected: a jumper holds
  one current Class III medical at a time and it covers all their
  tandem operations across manufacturers. Modeling it once is
  correct and keeps the file compact.
- *Computing CoP "null and void" status in v0.1.* Rejected as out
  of scope; D47 records `issued_date` so the future slice that
  computes it is purely additive on the calculator side, no schema
  change.
- *Storing the warning lead time (1 month) in config.* Considered
  and rejected for v0.1: a hardcoded constant in the calculator is
  simpler. Promotion to config is additive if Alex later wants
  per-credential lead times.

---

## Deferred (explicit non-decisions for v0.1)

- **FlySight CSV/folder parsing.** Files stored as-is; parsing
  extracted metrics is a future module (§D14).
- **Digital signatures on jump.xml.** Schema seam reserved (§D6);
  key management and signing UX designed separately.
- **Multi-user accounts.** Service seam in place (§D8); storage
  prefix, auth, and UI are future work.
- **Import from Paralog / JumpTrack / etc.** Not in v0.1. Paralog
  CSV is likely the first importer.
- **Video thumbnails / transcoding.** Videos stored as-is.
- **Mobile app.** Out of scope. The REST API makes one buildable
  later by anyone.
- **Always-on headless server mode** (API without the desktop
  window). Out of scope for v0.1; the REST API is available only
  while the app is open.
- **Automatic app updates.** v0.1 is manual re-download from the
  releases page.
- **Attachment editing via PUT.** v0.1 `update_jump` is metadata-only
  per §D31. Adding, removing, or replacing attachments on an
  existing jump is not supported by the API — delete-and-recreate
  is the workaround. A dedicated phase post-v0.1 pins the multipart
  transport and the D25 update-ordering end-to-end with a crash
  harness row for the orphan-delete step.

---

## D48 — v0.1 is loopback-only; the OpenAPI bearer-auth scheme is dropped

**Decision.** The REST API has no authentication surface in v0.1. The
default `bind_host = "127.0.0.1"` (D20) is the entire access-control
story: only processes on the same machine can reach the API, and v0.1
is a single-user desktop app shipped to that exact deployment. The
`bearerAuth` security scheme that previously appeared in
`backend/api/openapi.py` is removed, and the `api_key` field on
`Settings` (`backend/config.py`) is dropped along with the
`config.toml.example` block documenting it.

**Why.** The scheme was on paper without any code enforcing it: no
middleware checked the `Authorization` header, `get_user_id()`
returned `"default"` regardless of inbound headers, and `api_key`
from `Settings` was never read. A spec that advertises an auth
defense the code does not deliver is worse than no spec at all — a
user who edited `bind_host = "0.0.0.0"` (e.g. to log jumps from a
phone on the same Wi-Fi) would believe their endpoints were
authenticated when they were in fact wide open on the LAN.

The audit options were:

- *(A)* drop the scheme — match the spec to what the code actually
  enforces.
- *(B)* wire the middleware now — implement Bearer-token enforcement
  before any LAN exposure ships.
- *(C)* document the gap with a deferral D-entry and keep the spec.

This decision picks (A). v0.1 commits to loopback-only as a
deployment posture; LAN exposure is out of scope (D14 §Deferred). A
spec that promises only what the code delivers is the safer
contract.

**Consequences.**

- `backend/api/openapi.py` no longer registers a `bearerAuth`
  security scheme. `/openapi.json` therefore lists no
  `securitySchemes`. Third-party SDK generators produce clients
  with no auth boilerplate, matching reality.
- `backend/config.py` removes `api_key: str | None = None` from
  `Settings`. The TOML source ignores any leftover `api_key = "..."`
  line (`SettingsConfigDict(extra="ignore")`), so users with a
  pre-D48 `config.toml` see no error — the value is silently
  unread.
- `config.toml.example` removes the `api_key` documentation block.
- `backend/tests/test_config.py` drops the three `api_key` tests
  (default-None, TOML-sourced, env-overrides-TOML). The remaining
  precedence tests cover `bind_port` and `log_level` end-to-end so
  the env > TOML > defaults invariant from D20/D28 is still pinned.
- `get_user_id()` continues to return `"default"` per D8: dropping
  the bearer scheme does not change the single-user identity
  story. When multi-user lands, D8 is the entry point, not D48.

**Re-add path.** When LAN exposure (or multi-user, or any non-
loopback deployment) enters scope, a new D-entry pins:

1. The middleware that reads `Authorization: Bearer <key>`,
   compares against a configured token, and rejects 401 when the
   bind host is not loopback and the header is missing or wrong.
2. The configuration shape — whether `api_key` returns to
   `Settings`, lives in a separate secret store, or is generated
   on first-run.
3. The OpenAPI security scheme — re-added at that point as the
   *advertised* form of an enforcement that actually exists in
   code.

The successor D-entry supersedes D48 on the day the middleware
ships; D48 stays in place to document the transitional posture.

**Alternatives considered.**

- *(B) Wire the middleware now.* Rejected: adds a feature surface
  v0.1 does not need. Bearer-token enforcement without a server
  binding to anything other than loopback is dead code with a
  configuration-error trap (a misset `api_key` could lock the
  loopback user out of their own logbook). Better to ship the
  enforcement and the deployment shape together.
- *(C) Deferral D-entry while keeping the spec.* Rejected: a spec
  that documents an auth guarantee the code does not enforce is
  strictly worse than a spec that tells the truth. The OpenAPI
  surface is read by SDK generators, by humans evaluating the
  project, and by future maintainers; every one of those readers
  is better served by an honest spec.

**References.**

- D8 — `user_id` is a parameter from day one; default `"default"`.
- D14 §Deferred — multi-user accounts are post-v0.1.
- D20 — config paths; `bind_host` lives in user-config TOML.
- D28 — config-loading precedence (env > TOML > defaults).

---

## D49 — Cloud-sync folders (Dropbox / iCloud / OneDrive) are supported best-effort

**Decision.** A logbook root located inside a cloud-sync folder is
**supported best-effort**, not unsupported. The XML data is safe by
construction (D2 hardened parser + D10 atomic writes + D5 SHA-256
manifest). The SQLite index may need rebuilding when the user opens the
logbook on a second machine after a sync; the D26 drop-and-reindex flow
that landed in `backend/main.py:main` (the wired-up reindex per the
2026-04-29 audit's ARCH-1 slice) makes that recovery automatic on the
next start.

We do not detect cloud-sync paths at bootstrap, do not warn users at
startup, and do not switch SQLite away from WAL. The position is "the
data is safe; the index will heal itself."

**Why.** A self-hosted, single-user, laptop-resident logbook is exactly
the use case where users want their data backed up and portable across
machines. Cloud-sync folders are the most common user-reachable
mechanism. Forbidding them would push users into ad-hoc workarounds
(symlinks, manual copies, scheduled `rsync`s) that we cannot reason
about, and would contradict the project's "you own your data" framing
(README §Principles).

The known incompatibility is between SQLite WAL mode and cloud-sync's
file-replication semantics. SQLite's WAL documentation
(https://www.sqlite.org/wal.html §8) is explicit:

> WAL does not work well over a network filesystem. ... All processes
> using a database must be on the same host computer; WAL does not
> work on a network filesystem.

Dropbox, iCloud Drive, OneDrive, and Google Drive each replicate the
three WAL files (`index.sqlite`, `index.sqlite-wal`,
`index.sqlite-shm`) independently. On the destination machine, the
replicated WAL may be newer than the database; SQLite's recovery may
either succeed (most cases) or fail loudly (some cases). Either way,
the worst observable outcome is bounded: D3 makes the index
rebuildable from XML, D26's drop-and-reindex flow runs automatically
on a `user_version` mismatch, and ARCH-1's wiring runs reindex
synchronously. The user sees a startup that takes one extra second
on a fresh sync; the data is intact.

The XML files themselves are safe under cloud sync because each is
written atomically (D10) and verified by `SHA256SUMS` (D5). A partial
sync of a single jump folder that drops `SHA256SUMS` heals on the
next read via `folder_reconcile` (D25). A partial sync that drops
`jump.xml` produces an "incomplete folder" state that `verify` flags
and `reindex` skips.

**Consequences.**

- `README.md`, `docs/architecture.md` §Concurrency-and-sync, and the
  forthcoming first-run UX (audit INFRA-6) name cloud sync as
  supported best-effort with this caveat. Until the first-run UX
  lands, the documentation in those two files is the surface where
  the position is communicated.
- No code change ships with this decision. The D26 reindex wiring
  done in the 2026-04-29 audit's ARCH-1 slice is the load-bearing
  recovery path; D49 records the policy that depends on it.
- `journal_mode = WAL` stays. Switching to `journal_mode = DELETE`
  would trade a tiny perf benefit (smaller cloud-sync footprint) for
  a real perf cost on every write that the majority of non-cloud
  users would pay. WAL is right for the local-first single-user
  case; cloud-sync is the rare-but-supported edge.
- `verify` is the recommended pre-flight on a freshly-synced logbook
  before heavy use. The CLI exists at
  `python -m backend.scripts.verify`; the future Settings → Verify
  button (audit F-UX) will surface the same.

**Re-evaluation triggers.** The next D-entry that touches this posture
should fire if any of the following changes:

- A user reports actual data loss (not index loss) traceable to
  cloud-sync replication. Today's atomic-write + manifest design
  makes this near-impossible; if it happens, it's evidence the
  invariants need tightening.
- SQLite ships a WAL mode that's network-filesystem-safe (no current
  signal of this).
- Multi-user lands (D14 §Deferred → future D-entry). Cross-user
  sharing changes the lock + sync story materially.
- The first-run UX (INFRA-6) gains a cloud-sync detector that warns
  on known sync paths. That work would supersede this entry's "we
  don't detect" clause, not the underlying support posture.

**Alternatives considered.**

- *(a) Local-FS only; refuse to start in a sync folder.* Rejected:
  too restrictive for a self-hosted laptop tool. Detection is
  imperfect (Dropbox / iCloud / OneDrive each have different path
  conventions, users mount sync providers in custom places, and
  `xattr` markers vary by OS); a false-positive refusal would lock
  out a real user. The cost is high, the benefit (avoiding a
  one-second reindex on first remote open) is low.
- *(b) Cloud-FS safe via `journal_mode = DELETE`.* Rejected: WAL is
  the right journal mode for single-process local-first writers, and
  every user would pay the perf cost so the rare cloud user could
  avoid a once-per-machine reindex.
- *(d) Hybrid — detect known paths and warn at startup, but allow.*
  Plausible. Out of scope for this D-entry because the warning UX
  lives in INFRA-6's first-run picker; bundling the two would
  conflate "what's the position?" with "how does the UI surface
  it?". When INFRA-6 lands, a successor D-entry can pin the warn-
  before-allow refinement.

**References.**

- D2 — XML on disk + hardened parser. The XML data is safe regardless
  of journal mode.
- D3 — SQLite is rebuildable from XML. The index loss class is
  recoverable, not catastrophic.
- D5 — `SHA256SUMS` integrity envelope; catches partial folder sync.
- D9 — Single-instance lock is per-machine; cloud sync does not
  coordinate locks across machines, hence "do not run on two
  machines simultaneously."
- D10 — Atomic writes via `os.replace`; cloud-sync replicates the
  post-rename state, never a partial write.
- D25 — Crash-state semantics include the recovery paths that
  cloud-sync edge cases land in.
- D26 — Drop-and-reindex on `user_version` mismatch; the heal flow
  for index drift.
- ARCH-1 (2026-04-29 audit slice) — wired `reindex_from_xml` into
  startup so D26's recovery runs automatically.
- SQLite WAL documentation §8 — https://www.sqlite.org/wal.html
- 2026-04-23 forward-review §A11 + §D1 — the audit findings that
  surfaced the gap and canvassed the three options.

---

## D50 — Intra-process writes are serialised by an in-process writer lock

**Decision.** All multi-step write operations in the service layer
acquire a single in-process service lock before they begin, and
release it after the index update. **Reads of resources whose folder
names can change under a concurrent write also acquire the lock**
(see §"Reads that synchronise" below). The lock is a
`threading.RLock` — re-entrant on the same thread, so cross-service
write composition (`rig_service.create_rig` calling
`main_service.set_assigned_rig_id` etc.) does not deadlock. `asyncio.Lock`
semantics aren't needed — FastAPI sync handlers already run on a
threadpool, and the lock is per-process, never crossing event loops.

*Amended 2026-04-29 (ARCH-4 implementation slice).* The original
draft of this entry called for a plain `threading.Lock` and stated
that "reads run unsynchronised." The implementation work surfaced
two corrections:

  1. **RLock, not Lock.** `rig_service.create_rig` is a decorated
     write that calls `main_service.set_assigned_rig_id` (also a
     decorated write) per the D37 cross-entity validation flow. With
     a non-reentrant Lock, the same thread re-acquires the lock
     inside the assigner and deadlocks. RLock recognises the same
     thread and lets it proceed; cost is one extra thread-id check
     per acquisition.
  2. **Reads of rename-prone resources synchronise too.** The §A7
     race is reader-vs-writer (a `get_jump` mid-`update_jump`
     between `os.rename` and the index UPDATE — see "Why" below).
     A writer-only lock leaves a reader that holds no lock free to
     observe the intermediate state. The pragmatic fix is to take
     the same lock on the reads of resources whose folder names
     can be renamed by a concurrent write — `get_jump`,
     `list_jump_files`, `get_rig`, `list_rigs`. Reads of resources
     keyed by stable UUIDs (jumpers, dropzones, inventory components)
     do NOT need synchronisation: their folders / files don't get
     renamed, so the §A7 race shape doesn't apply.

This sits one level below D9 (the per-machine `filelock` that prevents
two app instances from racing). D9 covers inter-process; D50 covers
intra-process — the threadpool concurrency Starlette dispatches sync
route handlers onto.

**Why.** The 2026-04-23 forward-review §A7 named a specific race in
`update_jump`:

  step 6: `atomic_write(current_folder / JUMP_XML_NAME, ...)`
  step 7: `atomic_write(current_folder / MANIFEST_NAME, ...)`
  step 8: `os.rename(current_folder, new_folder)`   (when title changed)
  step 9: `UPDATE jumps SET folder = ?, ...`

Between steps 8 and 9, a concurrent `get_jump` on a different worker
thread reads the index row (still pointing at the *old* path), tries
to read `<old_folder>/jump.xml`, and gets `FileNotFoundError`. The
folder no longer exists at the old path — it was just renamed.
FastAPI sync handlers run on Starlette's threadpool (default 40
concurrent threads); a polling client on the same desktop session
can hit this window in practice.

D9's lockfile serialises *processes*, not *threads within a process*.
Nothing in v0.1's services holds intra-process state — every operation
opens its own SQLite connection, every read parses XML fresh — so the
race is not a coherence issue (no in-memory state to inconsistent),
it's a transient-error issue (a 500 or 404 visible to a polling
client between two valid states).

The same race exists, in milder form, on every multi-step write
(`create_jump`, `delete_jump`, `track_files`, `add_attachments`,
`delete_attachment`, dropzone CRUD, jumper CRUD, rig CRUD).
`update_jump` is the most visible because its rename step changes the
folder path the index tracks.

**Why a writer lock and not reorder-without-lock.** Reordering
(e.g. update the index *before* renaming, so concurrent reads always
land at a coherent path) is technically correct for some cases but
fragile: every multi-step write becomes its own ordering puzzle, every
new write path is a potential D25 row gone wrong. A single explicit
serialisation point at the service layer is harder to subtly break
and easier to reason about.

**Where the lock lives.** `backend/services/_write_lock.py` exposes
a module-level `WRITER_LOCK = threading.RLock()` and a
`@with_writer_lock` decorator. Each public service write function
gets the decorator. The four rename-prone reads named above
(`get_jump`, `list_jump_files`, `get_rig`, `list_rigs`) also get the
decorator. The other reads (`get_jumper`, `get_dropzone`,
`get_main` / `_reserve` / `_aad` / `_container`, the corresponding
`list_*`, `list_jumps`, `verify`, `reindex`) do not acquire it —
their resources don't rename, so the §A7 race shape doesn't apply.

The decorator approach keeps the call-site pattern uniform across
every service module (jumps, dropzones, jumpers, rigs, components)
and makes it grep-able for review: any service write missing
`@with_writer_lock`, or any rename-prone read missing it, is a
review smell.

**Performance posture.** A single-user desktop app sees one writer
at a time in normal use; the lock is uncontended ~99% of the time
and adds negligible latency. The remaining cases (a user clicks
"Save" twice, or a polling client coincides with an edit) serialise
correctly. There is no plausible workload in v0.1 where writer
contention is a bottleneck — if it ever becomes one, that's evidence
the app outgrew single-process and needs a multi-process or
async-storage shape, which is itself a successor D-entry.

**SQLite ``busy_timeout``.** ``open_index`` sets ``PRAGMA
busy_timeout = 250`` on every connection (audit CODE-3 → CODE-7,
landed 2026-04-29). Under this D-entry's writer-lock policy,
``SQLITE_BUSY`` is structurally unreachable — the in-process lock
serialises every multi-step write before it touches the database, so
no two writers can ever race on a connection. The pragma is
defensive: if a future slice ever holds two write connections in
flight (a background reindex, an async migration, an analytics-style
side process), the 250ms timeout makes the contention graceful
instead of immediate. Cost is zero today.

**Scope of the lock — what it covers, what it doesn't.**

Covered (must be inside the lock):

- The XML-write phase of every multi-step service operation.
- The manifest-regeneration phase.
- The folder-rename phase (where applicable).
- The index INSERT / UPDATE / DELETE that closes the operation.

**Reads that synchronise** (the §A7 amendment, 2026-04-29):

- `jump_service.get_jump` — folders rename on title or jump_number
  edit; a read mid-rename can observe the §A7 inconsistent state.
- `jump_service.list_jump_files` — same folder-resolution path.
- `rig_service.get_rig` — rig folders rename on nickname edit.
- `rig_service.list_rigs` — same folder-resolution path.

These reads acquire the same RLock that writers do. Single-user
desktop scale (D14, D20) makes the contention practically zero —
the lock is uncontended ~99% of the time. Python's standard library
has no read-write lock primitive; rolling one would be net-negative
maintenance for a workload where writer-blocks-reader serialisation
is fast enough.

Not covered:

- Reads of resources keyed by stable UUIDs that don't rename:
  `get_jumper`, `list_jumpers`, `get_dropzone`, `list_dropzones`,
  `get_main` / `get_reserve` / `get_aad` / `get_container` and
  their `list_*` siblings, `verify`, `reindex`. The §A7 race shape
  (folder name changes mid-read) doesn't apply, so these stay
  unlocked and observe the index directly.
- `list_jumps` (the JumpsLog list query). Reads from the SQLite
  index only, no XML parse, no folder-resolution — no race surface.
- The Pydantic validation that precedes a write. Validation is a
  pure-CPU step; doing it under the lock would serialise it
  unnecessarily.
- The pre-write filename sanitization in `_sanitize_upload_filenames`.
  Pure-CPU; pre-flight only.

The lock is acquired *just before* the first disk write of the
operation and released *immediately after* the index commit. The
specific entry/exit points are documented in each decorated
function's docstring.

**Crash semantics under the lock.** D25's crash-state table is
unchanged. The lock is a serialisation primitive, not a transactional
primitive — a process crash mid-operation produces the same
"incomplete folder" / "manifest stale" / "index drift" states D25
already covers. The lock just guarantees that on recovery, no
*concurrent* operation was interleaving its own steps with the
crashed one.

**What this entry does NOT change.**

- D9 stays — the per-machine lockfile prevents two app instances
  from writing to the same folder concurrently.
- D26 stays — the index schema versioning + drop-and-reindex flow
  is orthogonal to write serialisation.
- D25 stays — crash-state semantics are unchanged.
- The `update_jump` step ordering stays — the lock wraps the
  ordering, doesn't replace it.

**Implementation status.** Landed in the 2026-04-29 ARCH-4 slice
(audit condensed in `docs/historical-reviews.md`). The
`backend/services/_write_lock.py` module exposes
`WRITER_LOCK = threading.RLock()` and `with_writer_lock`. 44 public
service writes carry the decorator across `jump_service`,
`dropzone_service`, the four inventory services
(`main` / `reserve` / `aad` / `container`), `rig_service`,
`jumper_service`, and `jumper_credential_service`. The four
rename-prone reads (`get_jump`, `list_jump_files`, `get_rig`,
`list_rigs`) also carry the decorator per the §A7 amendment above.
Two integration tests (`test_concurrent_writes.py`) pin the
end-to-end behaviour: a 20-iteration writer/reader race that would
have produced transient `FileNotFoundError`s without the lock now
runs clean, and two writers updating different jumps in parallel
both finish without lost-update.

**Alternatives considered.**

- *(a) Reorder index update before folder rename in `update_jump`.*
  Closes the specific §A7 race for `update_jump` only; doesn't help
  any other multi-step write. Adds a special-case ordering rule that
  every future multi-step write must remember to follow. Rejected:
  fragile.
- *(b) Per-resource locks (one lock per jump, one per dropzone, ...).*
  Plausible at scale, overkill here. v0.1's contention surface is
  one user clicking through one UI; a single global writer lock is
  dramatically simpler than a sharded scheme and the perf cost is
  negligible.
- *(c) Migrate every service write to async + an `asyncio.Lock`.*
  Out of v0.1 scope. FastAPI sync handlers + threadpool dispatch is
  the current shape; the rewrite to async would touch every route
  + every service function. A future D-entry can revisit when there's
  evidence the threadpool model is the bottleneck.
- *(d) Database-level serialisation (BEGIN IMMEDIATE on every write).*
  SQLite gives this for free, but it doesn't cover the
  filesystem-rename half of the race. Necessary but not sufficient.
- *(e) No lock, accept the transient-error edge, document it.*
  Rejected: the §A7 window is reproducible on a polling frontend,
  and "your save sometimes 500s mid-rename" is a bad enough UX that
  the lock's near-zero cost is worth paying.

**References.**

- D7 — Service layer owns logic; thin REST adapter. The lock lives
  at the service boundary because that's where multi-step writes
  cohere.
- D9 — Single-instance lockfile (inter-process). D50 sits below
  D9 in the lock hierarchy: D9 says "one app at a time", D50 says
  "one writer at a time within that app".
- D25 — Crash-state semantics. Unchanged by D50.
- D26 — Drop-and-reindex; runs at startup before the writer lock
  has any work to serialise.
- 2026-04-23 forward-review §A7 + §D3 — the audit findings that
  named the race and the open question.
- 2026-04-29 tech-debt audit ARCH-4 — the implementation slice
  that puts D50 into code.
- Starlette `run_in_threadpool` — https://www.starlette.io/concurrency/
- Python `threading.Lock` semantics —
  https://docs.python.org/3/library/threading.html#threading.Lock

---

## D51 — Pyright type-checking is enforced in CI; strict for production, basic for tests

**Decision.** Pyright is a third gate alongside `pytest` and
`ruff check backend` on every push and PR. Production code paths
(`backend/services/`, `backend/storage/`, `backend/models/`,
`backend/api/`, `backend/scripts/`, `backend/observability/`, plus
`backend/main.py` and `backend/config.py`) are type-checked at
**strict** mode. Tests (`backend/tests/`) and the lxml typed
boundary (`backend/xml/`) run at **basic** mode with per-folder
overrides for the diagnostic families that lxml's incomplete stubs
or pytest fixture patterns surface as noise. The configuration lives
in `[tool.pyright]` in `pyproject.toml`.

**Why type-checking at all.** D2 commits to "every field exists in
three synchronized places: the Pydantic model (runtime + API shape),
the XSD (file-format contract), the SQLite index (query
performance)." A type checker won't verify the XSD or the SQL
schema — but it will verify that every service function reading a
`Jump` is using the field names the Pydantic model actually defines,
which catches the "renamed a field, missed three call sites" class
of bug that ruff doesn't see. Type errors are also the cheapest
class of bug to surface — caught by `pyright` in milliseconds, no
test required.

**Why Option 2 — strict for production, basic for tests.** The
audit's 2026-04-29 baseline measured 3760 pyright-strict errors
across the backend: 2608 in `backend/tests/` (almost entirely
fixture-typing patterns where pyright cannot narrow
`**dict_with_mixed_values` per-kwarg), 828 in `backend/xml/` (lxml's
incomplete stubs cascade through every `etree.X` access), and
~418 in production code (the meaningful surface). The Option 2
shape is the configuration that catches every real bug in production
while not paying type-annotation tax on test fixtures and on the
boundary with an upstream-incomplete library.

**The lxml boundary.** `lxml.etree` ships incomplete type stubs —
the C-extension surface is opaque to pyright, which sees
`etree._Element` as `Unknown` and cascades that through every
caller. Inside `backend/xml/` the per-folder override silences the
diagnostics that fire on every `etree.X` access. The module's
**public surface is typed with `XMLElement = Any` and
`XMLSchema = Any`** aliases (defined in `backend/xml/validator.py`)
so callers in `backend/storage/`, `backend/services/` and elsewhere
see a usable type rather than a cascading `Unknown`. Callers that
need element manipulation use the public helpers
(`namespace_of(...)`, `parse(...)`, `validate(...)`) rather than
importing `lxml.etree` directly — keeping the lxml use behind the
boundary.

**Why basic for tests.** Test fixtures use a `**kwargs` spread
pattern (`Jumper(**fixture_dict)`) where pyright cannot narrow the
dict's value types per parameter. The runtime works correctly —
pydantic validates each field — but pyright sees `Argument of type
Any | date cannot be assigned to parameter ...` for every field.
159 of the 181 test errors at strict were this single pattern. Tests
also import lxml directly in a handful of XSD-credential tests
where the typed boundary doesn't apply. Both classes of error are
annotation-effort that doesn't catch real bugs. Basic mode silences
the noisy `reportUnknown*` family while keeping the bug-catching
diagnostics (`reportArgumentType` for non-fixture call sites,
`reportOptionalMemberAccess`, `reportOperatorIssue`) active in test
files outside the per-folder override.

**Why basic for `backend/scripts/launch_desktop.py`.** Pywebview
ships no type stubs at all — every `webview.X` access is Unknown.
The launcher has 50+ such uses. The file gets a per-file
`# pyright: basic` pragma that downgrades it to basic-mode
checking. Real bugs (argument-type, operator misuse) still fire
under basic; the noise from missing pywebview stubs is silenced.
Per-line `# pyright: ignore[reportMissingImports]` covers the
`import webview` lines themselves since basic still reports those.

**Suppression policy.**

- Every suppression is a per-line ``# pyright: ignore[<rule-name>]``
  comment. Never blanket file-level disables (the per-file
  ``# pyright: basic`` for `launch_desktop.py` is the one named
  exception, justified above).
- Never use ``# type: ignore`` (the mypy form). Mypy isn't in our
  toolchain; the comment would be a confusing artifact.
- Every suppression carries a one-line reason next to the rule name.
  Two patterns:
  - `# pyright: ignore[reportPrivateUsage]  # deliberate cross-
    service helper (jumper_credential_service ↔ jumper_service)`
  - `# pyright: ignore[reportUnusedFunction]  # registered via
    @app.exception_handler / @app.get`
- A suppression that keeps growing (the same rule keeps tripping in
  the same module) is a smell — the right fix is usually a type
  alias, a Protocol, or a small architectural cleanup that removes
  the cascade at its source. The 2026-04-29 rollout took this path
  twice: introducing `XMLElement = Any` in `validator.py` removed
  the 828-error lxml cascade; introducing `Sequence[object]` for
  the credential-collection iteration removed 7 errors.

**Bump policy.** The pyright floor in `pyproject.toml` is pinned
to `>=1.1.400`. Pyright's release cadence is fast; bump
deliberately when a new feature lands that we want and verify the
existing suppressions still resolve to the same rules. The CI step
runs `uv run pyright backend` on every matrix cell — type-checking
is platform-independent (pyright reads `pythonVersion = "3.11"`
from pyproject regardless of the runtime), but running on every
cell catches platform-specific stub differences (Windows-only
types in stdlib, macOS-specific signatures) that a single-cell
run would miss.

**Consequences.**

- `pyproject.toml` `[tool.pyright]` block carries the Option 2 shape
  (top-level `typeCheckingMode = "basic"`, `strict = [...]` allow-
  list, `executionEnvironments` per-folder overrides for `xml/`
  and `tests/`).
- `backend/xml/validator.py` defines and exports `XMLElement`,
  `XMLSchema` type aliases plus `namespace_of(element) -> str`
  helper.
- `backend/xml/serialize.py` and downstream consumers
  (`backend/storage/`, `backend/services/`) use the aliases instead
  of `etree._Element`. Direct `from lxml import etree` outside
  `backend/xml/` is a review smell — the typed boundary is the
  right shape.
- CLAUDE.md §7 documents the green-light triple
  (`pytest` + `ruff check backend` + `pyright backend`).
- CI runs `uv run pyright backend` on every matrix cell after
  ruff and before pytest.
- ~30 dataclass / Pydantic field defaults converted from
  `Field(default_factory=list)` to `[]` (Pydantic v2) or
  `field(default_factory=list[T])` (dataclass) so pyright sees
  concrete element types instead of `list[Unknown]`.
- Two genuinely-dead helpers removed (`_jumper_xml_path` and
  `_rig_folder`) — pyright surfaced both as `reportUnusedFunction`.

**Re-evaluation triggers.**

- lxml ships complete stubs upstream. Today the `backend/xml/`
  override is a pragma against an upstream gap; if `lxml-stubs` or
  lxml's bundled stubs ever cover the surface we use, drop the
  override and tighten `XMLElement` from `Any` to the real type.
- pywebview ships type stubs. Today the `launch_desktop.py`
  per-file `basic` pragma is a pragma against the same gap; if
  pywebview ever ships proper stubs, remove the pragma and tighten.
- A suppression cluster grows past ~5 instances of the same rule
  in the same module. That's the threshold where a type alias /
  Protocol / refactor is cheaper than the suppression maintenance.
- The fixture-typing limitation in pyright is fixed upstream
  (unlikely soon — it's a fundamental dict-spread inference
  problem). If it ever lands, tests can be flipped to strict and
  the per-folder override removed.

**Alternatives considered.**

- *(Option 1) Strict everywhere, including tests + xml/.* Multi-day
  cleanup. Would require writing a thin local stub for the lxml
  surface we use (~200 lines of stub code) and rewriting every
  fixture pattern to use TypedDict or named-args construction. The
  upside (a strict-everywhere policy) doesn't pay for itself
  against real-bug-detection (which Option 2 already has, in
  production code where the bugs hide).
- *(Option 3) Strict for production AND tests; lxml at boundary.*
  Same lxml treatment as Option 2 but tests at strict. Roughly a
  half-day longer than Option 2; would introduce ~159
  ``# pyright: ignore[reportArgumentType]`` comments in test files,
  most for the dict-spread pattern. Annotation churn without a
  bug-catching benefit; rejected.
- *(mypy instead of pyright)* Mypy is the older, more familiar
  Python type-checker. Pyright is faster (Microsoft TypeScript-team
  authored, written in TypeScript), generally stricter, and has
  better support for modern Python typing features (PEP 695
  type-statement, ParamSpec, TypeVarTuple). The performance
  difference matters at our test-cycle latency: pyright on the
  whole backend completes in ~2 seconds; mypy in 8–10. For a
  pre-commit / CI tool, that delta compounds. Pyright wins.
- *(Defer indefinitely)* Initial framing in the audit was Phase 5
  (post-v0.1). Alex pulled it forward to "now" with the explicit
  "clean code from the start" framing. Deferring would have meant
  every new module added between now and the rollout would also
  need a pyright cleanup pass; doing it once at the current size
  is cheaper than doing it later at a larger size.

**References.**

- 2026-04-29 tech-debt audit DEP-2 and pyright rollout report
  (both condensed in `docs/historical-reviews.md`).
- Pyright configuration docs — https://microsoft.github.io/pyright/
- Pyright diagnostic settings table —
  https://github.com/microsoft/pyright/blob/main/docs/configuration.md#diagnostic-settings-defaults
- D2 — XML on disk + Pydantic models as the synchronized field surface.
- D15 — Python 3.11+ floor (mirrored in `pythonVersion = "3.11"`).

---

## D52 — Unsigned binaries for v0.1; signing deferred until revenue or scale

**Decision.** v0.1 ships **unsigned on every platform** — no Apple
Developer ID, no Authenticode certificate, no notarization. Per-
platform install instructions in the release notes and README tell
users how to bypass Gatekeeper and SmartScreen on first launch. A
*Check for updates* button in Settings hits the GitHub Releases API;
the user clicks through to download the new version manually. There
is no in-app binary replacement and no automatic update path (D14).

**Why.**

- The project is pre-revenue with a single maintainer. Apple Developer
  Program is $99/yr, Authenticode OV is $200–$400/yr, EV with HSM is
  ~$500/yr. None of those costs are justified before the project has
  any users.
- Self-distribution via GitHub Releases lets us ship without paid
  certificates as long as the per-platform "open this anyway"
  instructions are documented prominently — leading the README, not
  buried in docs/build.md.
- The Gatekeeper / SmartScreen UX is genuinely worse for non-technical
  users. We accept that explicitly as v0.1 cost-of-doing-business,
  document it loudly, and let the population of beta testers self-
  select around it.
- Auto-update (the silent in-place replacement) is deferred per D14.
  The *Check for updates* button is a different feature — it tells
  the user a new version exists and opens the release page; no
  binary replacement, no privileged install, no signing required.
  When real auto-update lands later, *its own* signature scheme
  (Sparkle-style EdDSA) can sign update channels without paying for
  OS-level signing — see "Re-evaluation triggers" below.

**Consequences.**

- The PyInstaller spec keeps `codesign_identity=None` and
  `entitlements_file=None`. A future signed-build workflow injects
  these via env vars (`SIGNING_IDENTITY`, `ENTITLEMENTS_PATH`).
- The README and CHANGELOG lead with the unsigned-binary caveats;
  release notes repeat them. Quiet wording would re-create the
  problem the documentation is meant to solve.
- macOS users right-click → Open the first time; subsequent launches
  work normally. Windows users click *More info* → *Run anyway* on
  SmartScreen. Linux users `chmod +x` and run.
- A new Settings panel surfaces the *Check for updates* button via
  `GET /api/v1/updates/check` against
  `Settings.update_check_repo`. Unset, the endpoint returns 503
  `update_check_disabled` and the button is hidden.

**Re-evaluation triggers.** This decision flips when any of:

- The project has revenue (sponsorships, donations, paid features)
  that absorbs the certificate cost.
- The user base reaches a scale where Gatekeeper friction becomes
  the dominant support burden.
- Real auto-update lands (D14 deferred). Auto-update must be signed
  — either at the OS level (Apple Developer ID + notarization,
  Authenticode) *or* at the app level (Sparkle-style EdDSA). The
  successor decision picks one.
- The project pursues App Store / Microsoft Store distribution.
  Both stores enforce signing as a condition of publication.

**Alternatives considered.**

- *(Sign from day one)* The senior-dev posture argued for signing
  macOS at minimum ($99/yr) because the Gatekeeper UX is the worst
  of the three. Rejected on cost-discipline grounds — pre-revenue
  recurring spend is the wrong shape for an alpha. Revisit when any
  of the re-evaluation triggers fire.
- *(Self-signed certificate)* Not trusted by any platform; same
  user-facing UX as unsigned. No benefit.
- *(Ad-hoc signed on macOS)* Produces a binary that runs on the
  build machine but fails Gatekeeper on any other Mac. Not actually
  distributable.
- *(Skip the "Check for updates" button entirely)* Rejected — users
  on an unsigned, manual-install desktop app especially need a way
  to learn that a security or correctness fix has shipped. The
  button is the minimum-viable update channel.

**References.**

- D11 — single-binary packaging via PyInstaller.
- D14 — automatic in-app updates deferred for v0.1.
- D48 — loopback-only deployment posture (related: no auth surface).
- 2026-04-30 finish-open-items report (condensed in
  `docs/historical-reviews.md`).
- docs/build.md — per-platform build commands and signing command
  reference (used when a re-evaluation trigger fires).

---

## D53 — Jump field additions: jump_types, landing accuracy, packer, group

**Decision.** Extend `<jump>` with the following optional, additive
elements, appended at the tail of the existing sequence per D18
(additive within v1):

- `jump_types` — closed enum, multi-value. Initial values:
  `fun_jump`, `coaching`, `instructing`, `camera`, `organizing`,
  `coached`, `instructed`. (Partially superseded by D61: `fun_jump`
  was renamed to `regular_jump` pre-production, and the log-jump
  modal now default-selects it.) Stored as a nested-element list
  following the existing `<attachments>` pattern. Zero-or-more;
  absent ≡ unset (no implicit default at the model layer — the
  field is informational, not load-bearing for currency math; the
  modal-side preselect is a UX-layer default).
- `landing_distance_m` — `xs:decimal` ≥ 0 (meters per D12). Magnitude
  only.
- `landing_direction` — closed enum: `overshoot` | `undershoot`.
  On-target landings leave both this and `landing_distance_m` absent.
- `packed_by` — UUID reference to a `<person>` record (D54). **Absent
  ≡ self-packed** by convention; the logbook owner is not a Person
  record.
- `group_size` — `xs:positiveInteger`. The headline jumper count for
  the load.
- `group_members` — list of UUID references to `<person>` records
  (D54). `len(group_members) ≤ group_size` is **not** enforced —
  users may log "5-way" with only two friends in their People
  registry. The two fields capture different facts (cardinality vs.
  who-by-name); either may be set without the other.

The existing `discipline` field stays free-text and unchanged in this
slice. Closed-enum-ifying it (matching the D22/D34/D47 closed-enum
discipline) is a separately scoped follow-up. The existing
`is_tandem` boolean (D47, Phase B.4) does **not** join the
`jump_types` enum: it carries currency-math semantics (UPT/Strong
windows) that the jump_types field deliberately does not.

**Why.**

- USPA-style logging (D14, item 1) treats *role/purpose* and
  *flying style* as separate facets. The pre-existing `discipline`
  field captures the latter (angle, tracking, belly, freefly, …);
  the new `jump_types` captures the former (fun, instructional,
  camera, organizing). Conflating them was the original design
  mistake — a camera flyer on an angle is one jump with two facets,
  not a forced choice.
- Multi-valued `jump_types` matches the real shape of jumps:
  camera + organizing on the same load is common; coaching while
  shooting video is common. A single-valued field forces a
  fictional pick.
- Tandem stays on `is_tandem` because the manufacturer-currency
  calculator (D47, Phase E) reads it. Adding `tandem` to
  `jump_types` would create two sources of truth for the same fact.
- `landing_direction` as an enum (rather than a signed
  `landing_distance_m`) is more legible in XML for a human reader
  and harder to mis-key. Overshoot/undershoot is the dimension
  users train against; left/right rarely lands as a numeric metric
  and can live in `notes` for now.
- `packed_by` UUID + absent-≡-self keeps the common case (you
  packed your own canopy) zero-effort. A free-text packer-name
  fallback is deferred — if hired packers at unfamiliar DZs become
  a real friction, a `packer_name: xs:string?` lands additively per
  D18.
- `group_size` and `group_members` are independent because logging
  "the 7-way I was on" is faster than naming six people, but
  naming people is the higher-value record once they're in the
  registry. Each field has a job; coupling them would force-pick
  one.

**Consequences.**

- Additive XSD change at the tail of `<xs:complexType>` for
  `<jump>` (D18). Existing jump.xml files validate unchanged.
- `Jump` / `JumpCreate` / `JumpUpdate` Pydantic models grow matching
  optional fields. New `JumpTypeKind` and `LandingDirection`
  `StrEnum`s in `models/jump.py` follow the project's enum pattern
  (D47).
- SQLite index v8 caches `landing_distance_m`, `landing_direction`,
  and a comma-joined `jump_types` string for filter speed.
  `group_size`, `group_members`, and `packed_by` may stay XML-only
  initially; index columns can land additively as soon as a
  list-view filter needs them.
- Reindex must handle pre-D53 jump.xml files: missing fields are
  valid, decoded as `None` / empty list. A round-trip test guards
  this.
- `packed_by` and `group_members` carry references to People
  records that may not exist in the registry yet (hand-edited XML,
  half-imported logbook). The service layer treats unresolved
  references as soft-warnings (display "Unknown person
  <short-uuid>") rather than fail. Strict resolution is a UI
  concern, not a data-integrity invariant.
- The frontend log form gains: a multi-select chip input for
  `jump_types`, paired number+enum input for landing, a Person
  picker (with quick-add) for `packed_by`, a number input for
  `group_size`, and a Person multi-select for `group_members`.
  Surfaced in Phase 3 of the planning roadmap.

**Alternatives considered.**

- *Single-valued `jump_type`*. Rejected — real jumps are
  multi-faceted; forcing one tag drops information. Promoting from
  single to multi later would be a breaking schema change in v1.
- *Bundle `jump_types` into `discipline`*. Rejected — `discipline`
  answers "how you flew", `jump_types` answers "what was the jump
  for". Conflating produces strings ("angle-camera-organizing")
  that no statistical query can usefully decompose.
- *Add `tandem` to `jump_types`*. Rejected — `is_tandem` already
  exists and is read by the currency math (D47). Two sources of
  truth for the same fact is a bug factory.
- *Per-AFF-level enum (`aff_l1`…`aff_l8`) plus `coached`*. Rejected
  for v0.1 — too much enum surface, and AFF level structures vary
  across federations. If per-level breakdown becomes a real query,
  it lands additively as either a `level: xs:string?` companion or
  new enum values per D18.
- *Signed `landing_distance_m` (negative ≡ undershoot)*. Rejected —
  less legible in XML, and "0 m on-target" vs "0 m undershot" is
  ambiguous. Magnitude + direction enum is unambiguous.
- *Side dimension (left/right/center) on the landing*. Rejected as
  overspecified for v0.1; users who care can use `notes`.
- *Free-text `packer_name` instead of UUID ref*. Rejected — defeats
  the consistency goal that motivated D54. The fallback option
  lands additively if needed.
- *Derive `group_size` from `len(group_members)`*. Rejected — users
  log group size faster than they type six names, and the two
  fields capture different facts.

**References.**

- D2 — XML on disk is source of truth; all writes through XSD
  validation.
- D12 — wire/XML units use suffixed integer/decimal field names.
- D14 — v0.1 scope; "jump type" is item 1.
- D18 — XSD versioning; additive within v1.
- D22 / D34 / D47 — closed-enum discipline pattern.
- D47 — `is_tandem` boolean and tandem currency math.
- D54 — People entity (the target of `packed_by` and
  `group_members`).
- D55 — settings.xml (controls which of these fields the UI
  surfaces).

---

## D54 — People entity for group members and packers

**Decision.** Add a new entity type at
`logbook_root/people/<uuid>.xml` (flat single-file layout, mirroring
D44 Dropzone — Person carries no attachments in v0.1, so the
folder-per-entity shape used by Jumper would add complexity without
benefit) with a deliberately small shape:

- `id` — UUID, stable, file-system identity (the folder is the
  UUID).
- `name` — required, 1..120 chars, NFC-normalized (D4).
- `notes` — optional free text.
- `created_at` / `updated_at` — D32 audit timestamps.

A single People registry serves both `<jump>/<group_members>`
(jumpers you flew with) and `<jump>/<packed_by>` (people who packed
your canopy). The same Person can be referenced from both contexts;
no role tag distinguishes "friend" from "packer". The logbook owner
is **not** a Person record — `packed_by` absent ≡ self-packed (D53).

Resolution is **soft**: jump-side references that don't resolve to
an existing Person record are displayed as `Unknown person
<short-uuid>` rather than treated as a validation error. This keeps
a hand-edited or half-imported logbook loadable; integrity is a UI
concern, not a data invariant.

**Why.**

- Consistency. The original ask was a dropdown for group-member
  names so "John Doe" and "John D." don't drift across a career's
  worth of jumps. A real entity with a stable UUID is the only way
  to survive a rename — string-based autocomplete leaves old jumps
  stuck on the old spelling.
- Single registry. A packer is often also a friend; a friend often
  packs occasionally. Splitting Packer and Friend into separate
  entities forces duplicated records and ambiguous edits ("did I
  update both?"). One entity, no role tag, is simpler.
- Precedent. D33 (Jumper entity, the user's own identity) and D44
  (Dropzone entity) both established UUID-keyed entities under
  `logbook_root/<entity>/<uuid>/`. People follows the same shape,
  keeping the on-disk layout legible and the storage-layer code
  reusable.
- Cost is modest. A no-frills entity (id + name + notes) lands in
  roughly the same footprint as the smaller existing entities; the
  picker UI is small enough to share with the dropzone picker.

**Consequences.**

- New top-level XSD type `PersonRecord` with a corresponding
  `<xs:element name="person">` root for `person.xml`. Atomic-write
  (D10), XSD-validate (D2), NFC-normalize (D4) all apply.
- New service module `backend/services/people_service.py` with CRUD.
- New REST endpoints: `POST /api/v1/people`, `GET /api/v1/people`,
  `GET /api/v1/people/{id}`, `PUT /api/v1/people/{id}`,
  `DELETE /api/v1/people/{id}`.
- SQLite index v9 gains a `people` table (id, name, notes,
  created_at, updated_at) plus a join-helper view for "jumps with
  person X". `reindex` rebuilds it from `person.xml` files (D3).
- Delete semantics: deleting a Person leaves stale UUIDs on
  jump.xml records. Per the soft-resolution rule above, those
  references render as `Unknown person <short-uuid>` until the user
  either re-creates a Person with the same UUID or edits the
  affected jumps. A hard-delete that rewrites every referencing
  jump.xml is rejected for v0.1 — multi-file rewrites cross D10's
  atomic-per-file boundary, and bulk "merge person A into B" lands
  in its own slice if needed.
- The frontend gains a small People management view (list / add /
  edit) plus a reusable picker component used by the LogJumpModal.
- v0.1 ships single-jumper (D33), so there is no "is this Person
  also a Jumper?" question. If multi-jumper lands later, the
  question becomes "does Jumper subsume Person?" — the answer is
  probably yes, and the migration is "promote Person → Jumper if
  you start logging jumps from their POV". That deferred decision
  does not change today's choice.

**Alternatives considered.**

- *Autocomplete-from-strings* (option a in the planning
  conversation). `group_members: list[str]`, dropdown built from
  `SELECT DISTINCT` against the index. Cheaper (~50 LoC vs. ~250
  LoC) but typo-fragile and rename-hostile: changing "John D." to
  "John Doe" leaves every existing jump stuck on the old spelling.
  Rejected because the consistency goal that motivated the dropdown
  is exactly the case strings handle worst.
- *Separate Packer and Friend entities*. Rejected — same person
  often plays both roles; two records produce ambiguous edits.
  Single entity, no role tag.
- *Reuse the Jumper entity for everyone*. Rejected — Jumper carries
  the user's own credentials, exit weight, and currency state (D33,
  D47). Friends and packers don't have or need any of that;
  loading the Jumper schema for every friend's record is wrong.
- *Hard-delete (rewrite every referencing jump.xml)*. Rejected for
  v0.1 — cross-file rewrites violate D10's atomic-per-file model,
  and the soft-resolution UX is acceptable. Bulk "merge person"
  can land in its own slice if friction emerges.

**References.**

- D2 — XSD-validated XML on disk.
- D3 — SQLite is rebuildable index, never authoritative.
- D4 — NFC normalization, safe filenames.
- D10 — atomic_write for all persisted writes.
- D14 — v0.1 scope (this expansion is justified by the dropdown
  consistency need surfaced in 2026-04-30 planning; tracked as a
  small, recorded scope expansion).
- D32 — audit timestamps on entity records.
- D33 — Jumper entity (precedent for UUID-keyed entity).
- D44 — Dropzone entity (precedent for UUID-keyed entity).
- D53 — Jump field additions (the consumers of People references).

---

## D57 — Remove `landing_direction`, `group_size`, and per-jump `environment` from the jump model

**Status:** Reified 2026-05-12 alongside the Phase 1 LogJumpModal
redesign. No data to migrate — the app has not shipped and the
project's logbook folders contain no jumps that exercise these
fields.

**Decision.** Three optional fields are removed end-to-end from the
jump model: `landing_direction`, `group_size`, and the per-jump
`environment` override. Each removal touches `SCHEMA.v1.xsd`, the
Pydantic models in `backend/models/jump.py`, the XML
serializer/parser in `backend/xml/serialize.py`, the
roundtrip tests under `backend/tests/`, and the React form in
`frontend/src/modals/LogJumpModal.jsx`. The shared
`EnvironmentKind` simpleType in the XSD stays — it is still used
by `<dropzone>` (D44) and is the single source of truth for
environment values everywhere.

This is an in-place schema edit, not a v2 bump. D18 versions the
XSD inside the logbook folder so that *existing data* can be
read across schema generations; with no shipped data the
in-place edit is equivalent to a v2 bump that nothing would
migrate across. Future contract changes will still version
properly per D18 once we have data to be careful about.

**Why each field goes.**

- *`landing_direction` (D53 enum: `overshoot` | `undershoot`).*
  The redesigned form, agreed with Alex on 2026-05-12, captures
  landing accuracy as a single magnitude (`landing_distance_m`) and
  drops the directional axis. The directional half was added
  speculatively in D53 to allow future "centred vs. overshoot"
  histograms; in practice it doubles the input cost on every jump
  (two fields, one of which is a closed enum that most jumps land
  in neither of) for an analysis surface we have not built. When
  we want it back, freeform notes or a new field are both cheap.

- *`group_size` (D53 positive integer).* The headline jumper
  count is implied by the `group_members` list that already exists
  (`jump_with` in the new UI per Phase 1a). Capturing both
  redundantly invites contradiction — the existing D53 trade-off
  text accepted `len(group_members) ≤ group_size` "is not
  enforced" precisely because users will set the two inconsistently.
  Phase 1 takes the simpler path: the cardinality you can compute
  from member references is the cardinality. If the user records
  no members, the group is implicitly self-only. If we ever want
  a "logged the count but not the names" affordance back, a
  freeform number field is one D-entry away — but it should not
  be a co-equal first-class field with `group_members`.

- *Per-jump `environment` override (D45 step 1 of the resolution
  order).* D45 specified a three-step resolution for the
  environment value the wear math consumes: (1) the jump's
  explicit override, (2) the linked dropzone's environment, (3)
  the main canopy's `default_environment`. Phase 1 collapses
  this to two steps — the dropzone first, then the main canopy
  default. The per-jump override is removed from the jump form and
  from the schema. The wear-math implementation does not exist
  yet (the surface map dated 2026-05-12 confirmed no service
  calls `jump.environment`); the change is therefore documentation
  + contract only, with no code path to retrofit. When wear math
  lands (deferred past v0.1), it reads from the DZ.

  The dropzone's `environment` field is unchanged. Surface
  conditions that *deviate from the DZ's normal profile* are
  captured indirectly via the `packed_in_poor_conditions` checkbox
  (D45 packing-conditions delta), which remains in Advanced.

**Supersession.**

- D45's "Resolution order for the per-jump environment" section
  (lines ~3830–3846 in this file) is reduced from three steps to
  two: dropzone first, main-canopy default second, `clean_grass`
  as the implicit floor if neither resolves. The rest of D45 (the
  Peelman lb-budget structure, the additive RDS and packing
  deltas, the multi-DZ jumpers reasoning) stays intact. Step 1's
  rationale text — that an *explicit* per-jump override beats a
  per-DZ default for one-off dirty jumps — is replaced by the
  observation that the `packed_in_poor_conditions` delta already
  captures the rare per-jump deviation, and that requiring an
  explicit environment radio for the same intent adds a knob with
  poor input ergonomics for marginal additional signal.
- D53's enumeration of new fields (lines ~5416–5441) loses
  `landing_direction` and `group_size`. The rationale paragraphs
  for *why* those fields were added are preserved as historical
  context — the entry's "Rejected alternatives" still document
  why each was chosen at the time — but the entry is marked
  superseded for those two fields. `landing_distance_m`,
  `packed_by`, `jump_types`, and `group_members` are unchanged
  and remain D53-authoritative.
- D55's DRAFT `hidden_fields` enumeration (line ~5677–5678) drops
  the three field names. If D55 reifies as drafted, the per-user
  hide/show registry is the per-field opt-out for fields that
  *exist*; fields removed by D57 are not user-toggleable because
  they no longer exist.

**Consequences.**

- `backend/xml/schema/SCHEMA.v1.xsd`:
  - Remove `<xs:element name="environment" type="EnvironmentKind" minOccurs="0"/>`
    from the jump complex type (line ~968).
  - Remove `<xs:element name="landing_direction" type="LandingDirection" minOccurs="0"/>`
    (line ~1054).
  - Remove `<xs:element name="group_size" type="xs:positiveInteger" minOccurs="0"/>`
    (line ~1065).
  - Remove the `<xs:simpleType name="LandingDirection">` block (lines ~209–225) —
    no other jump-local or shared element references it.
  - Keep `<xs:simpleType name="EnvironmentKind">` (lines ~115–145). It is shared
    with `<dropzone>` and will be unaffected by this change.
- `backend/models/jump.py`:
  - Drop the three field declarations from each of `Jump`,
    `JumpCreate`, and `JumpUpdate` (9 lines total).
  - Drop the `LandingDirection` import; keep `Environment` (still
    used by `Dropzone`).
- `backend/xml/serialize.py`:
  - Remove the three conditional emit blocks for the jump
    elements and the three parse-back blocks. The DZ-side
    environment emit/parse stays.
- `backend/tests/test_d53_jump_fields_roundtrip.py`:
  - Delete the `TestLanding` (lines ~190–237) and `TestGroup`
    (lines ~251–335) classes. Trim the combined-fields tests at
    lines ~342–410 to no longer reference these field names.
    `TestPacker` and `TestJumpTypes` stay untouched.
  - Anywhere `test_xml_roundtrip.py` asserts `restored.environment`
    on a jump, drop that assertion (or the whole test if its only
    purpose was the per-jump environment round-trip).
- `frontend/src/modals/LogJumpModal.jsx`:
  - Remove `LANDING_DIRECTIONS` and `ENVIRONMENTS` module
    constants.
  - Remove `RadioCard` (only used by the now-deleted environment
    picker).
  - Remove `GroupAndPackerDisclosure` and its `detailsOpen` state
    — orphaned by the Phase 1a layout and not revived by Phase 1b.
  - Remove the three field keys from the form-state initializer,
    the reset handler, the edit-mode prefill, and `buildPayload`.
  - The DZ's `environment` value is still referenced in the
    DropzonePicker tooltip and the picker rows — that stays.
- `frontend/src/modals/DropzoneModal.jsx`: untouched. The DZ form
  continues to set the dropzone's `environment` field.

**Verification.**

- `pytest backend/`, `ruff check backend`, `pyright backend`,
  `npm test --run` (vitest) all green.
- Manual round-trip: create a jump through the form, save, reload
  the same jump in edit mode, confirm no warnings about unknown
  XML elements and that the form state matches what was saved.

**Trade-off — losing the speculative analysis surface.** The
strongest case for keeping `landing_direction` and the per-jump
`environment` override was that they're cheap to leave in until
we know whether the analysis surfaces that need them will
materialise. The case for removing them is that an empty closed
enum on every jump is friction the user pays today for an
analysis we may never build, and an environment radio that
duplicates the DZ default's most likely value is a knob whose
ergonomics will get worse the more DZs the user logs at. The
v0.1 posture (D14) favours removing speculative fields over
keeping them around "just in case" — closer alignment with what
the form actually captures, fewer states to validate, fewer
fields to migrate when the schema does eventually rev.

**References.**

- D14 — v0.1 scope (the trim aligns with "keep the surface area
  small").
- D18 — XSD versioning. In-place edits are acceptable pre-ship;
  this entry does not change the long-run versioning policy.
- D44 — Dropzone entity; the surviving home for the `environment`
  value.
- D45 — Wear-math resolution order is amended (3 steps → 2).
- D53 — Loses `landing_direction` and `group_size` from its
  field list; the other four additions stay.
- D55 (DRAFT) — Drops the three removed field names from its
  hide/show enumeration.

## D58 — Starred rig: a single default rig per logbook, used to prefill the jump form

**Status:** Drafted 2026-05-12 as Phase 1 of the "starred rig"
feature. No data to migrate — no shipped logbook contains the
`<starred>` element yet; the rule "exactly one rig is starred
when ≥1 rig exists" is satisfied by the auto-star-on-create
behaviour for every existing rig.xml retroactively (see the
reindex notes below).

**Decision.** A rig carries a boolean `<starred>` flag in its
XML. At any point in time the logbook satisfies a single
invariant:

> If the logbook contains ≥1 non-trashed rig, exactly one of
> those rigs has `starred=true`. If the logbook contains zero
> non-trashed rigs, no rig is starred.

The starred rig is the default that the jump-log form prefills
into its `rig_id` picker (Phase 3 of the rollout). The
invariant means the form can always preselect when the jumper
has any rig at all — there is no "form starts on no-rig" branch
that the user has to handle, and no "two rigs are both default,
which wins?" tiebreaker that the form has to encode.

**Three transitions maintain the invariant.** Every star
mutation lives behind the writer lock (D50), goes through
`atomic_write` (D10) and re-validates the rig's XML against the
XSD (D2) before commit, just like every other rig write.

1. *Create* (`rig_service.create_rig`). If the logbook contains
   zero non-trashed rigs at the moment of the create, the new
   rig is written with `starred=true`. Otherwise it is written
   with `starred=false`; the existing star is left untouched.
   Rationale: a brand-new logbook should never require a separate
   "go and pick a default" step before the jump form is usable,
   but adding a second rig to an established logbook should not
   silently move the default the user has already chosen.

2. *Star a different rig* (`PUT /api/v1/rigs/{rig_id}/star`).
   Idempotent. The service reads the current starred rig (if
   any), writes that rig back with `starred=false`, then writes
   the target rig with `starred=true`. Both writes happen under
   the same writer-lock acquisition so any other process or
   request observes either the pre-state or the post-state, never
   a transient "two rigs starred" intermediate.

   Because both writes are atomic-renames, a crash *between* the
   clear and the set leaves the logbook in a "zero starred" state
   on disk. The reindex contract (see below) re-asserts the
   invariant by deterministically re-electing a star, so the
   recovery surface is "the next reindex / next service start
   restores the invariant" rather than "manual repair".

3. *Soft-delete the starred rig* (`rig_service.delete_rig`). When
   the deletion target has `starred=true` and ≥1 rig will remain
   after the delete, the service auto-moves the star *before*
   the soft-delete commits. The successor is selected by:

     a. Most recent jump logged against the rig — `MAX(jump_date)`
        over the jumps index, grouped by `rig_id`, restricted to
        the remaining rigs.
     b. Tiebreaker when no remaining rig has any jumps logged
        against it (or when several share the same `MAX(jump_date)`):
        the rig with the lowest `display_order` (amended by D59 —
        previously was latest `created_at`). This matches the rig
        carousel's user-visible left-to-right order, so the star
        moves to the leftmost remaining rig — the user's mental-
        model default. Further tiebreakers (rigs without a
        `display_order`): earliest `created_at`, then id.

   "Most recently used" was chosen over "most recently created"
   because the carousel-first rig is not always the rig the
   jumper actually flies. A jumper rotating between two rigs
   should not have the default snap to whichever they happened
   to register most recently — they should keep flying whatever
   they were already flying. The cost of the rule is one extra
   read against the jumps index on delete; the index already has
   `rig_id` and `date` so the query is `SELECT rig_id, MAX(date)
   FROM jumps WHERE rig_id IN (…) GROUP BY rig_id`.

   The auto-move write goes through the same lock + atomic +
   XSD-validate path as transition 2. If it fails the soft-delete
   does not commit; the service rolls back via the writer lock's
   discard path. The user sees an RFC 9457 problem (D16) and the
   rig stays starred-and-present.

**No explicit unstar.** There is no `DELETE /api/v1/rigs/{id}/star`
endpoint and no model path to write `starred=false` directly. The
star is moved only by starring a different rig or by deleting the
currently starred rig. Rationale: the invariant "exactly one
starred while ≥1 rig exists" is what makes the form's preselect
unconditional. Allowing the user to clear the star without
choosing a replacement would re-introduce the conditional default
case for the cost of an affordance ("I want no default") that no
user has asked for. If that affordance becomes needed we can
re-open this by adding the DELETE — additive change, no schema
churn.

**The flag is on the rig itself, not in a separate settings
document.** Considered: store `default_rig_id` in a per-user
settings.xml (aligned with D55's hide/show registry). Rejected:
the star is a property of the rig from the user's mental model
("this is my starred rig") and querying `GET /rigs` to find it
matches how every other rig-list consumer reads the data. A
settings-side pointer would force every caller to also fetch
settings.xml to resolve the default, and would create a
referential-integrity case (settings.default_rig_id points to a
deleted rig) that the on-rig flag does not have. When/if D55
reifies, the per-user hide/show registry is the right home for
*hide which fields appear in the form*, not for *what value
those fields default to* — different concerns.

**Consequences.**

- `<starred>` (optional, `xs:boolean`) added to `RigType` in the
  v1 XSD; absent-or-false rigs serialise without it.
- `starred: bool = False` on `Rig` and `RigSummary`. Not on
  `RigCreate` / `RigUpdate` — service-controlled only.
- New endpoint `PUT /api/v1/rigs/{rig_id}/star`, RFC 9457 errors.
- `rig_service` implements the three transitions under the writer
  lock (D50) with atomic + XSD-validated writes (D2, D10).
- No rigs index table in v0.1 — `starred` is read by walking
  `rigs/*/rig.xml` (cheap at the 1–3-rig scale). When the rigs
  table lands in R.3 it adds a `starred INTEGER` column rebuilt
  from XML per D3.
- Reindex repair (deferred to R.3): election rule heals drift
  (zero or multiple starred). Implicitly already covered in v0.1
  because every `set_star` clears all currently-starred rigs
  before writing the target.

**Verification.** `pytest backend/`, `ruff check backend`,
`pyright backend` all green.

**Trade-off — coupling the star to the jumps index for delete.**
The successor-election rule reaches across the rig boundary into
the jumps index. The alternative ("most recently created") would
keep `delete_rig` self-contained, at the cost of the user-visible
behaviour I rejected above (snap-to-newest, regardless of which
rig the jumper actually flies). The coupling is bounded: it's a
single read against an already-cheap index, the read happens only
on the delete of the *starred* rig, and the fallback path covers
the case where the jumps index is unavailable or empty.

**Non-decisions / deferred.**

- Multi-jumper logbooks (D14 non-decision). When a future
  jumper concept lands, the star will presumably need to scope
  per-jumper (each jumper's default rig). The on-rig flag will
  not survive that change verbatim; the natural follow-up is to
  attach the star to the jumper-rig relation when it exists.
  v0.1 is single-jumper so this is not a blocker.
- Star history / audit. The `notes_log` on each rig is the
  catch-all for free-text audit; we do not record a structured
  "starred at / unstarred at" timeline. If a future use case
  needs it, additive.

**References.**

- D2 — XSD-validate every write. The starred element is part of
  the validated surface.
- D3 — SQLite index is rebuildable from XML. The `starred`
  column follows that rule (and the rebuild also repairs drift).
- D10 — `atomic_write`. Every star transition uses it.
- D14 — v0.1 scope. The feature stays inside "rigs CRUD".
- D16 — RFC 9457 errors. The new endpoint follows the convention.
- D18 — XSD versioning. The added optional element is a backward-
  compatible in-place edit; pre-ship, no version bump.
- D33 — Rig model and XSD. This entry adds one optional element
  to `RigType`.
- D37 — Component-on-one-rig invariant. Unrelated; mentioned only
  because both invariants are service-enforced under the writer
  lock.
- D50 — Writer lock. The "two writes look atomic to readers"
  guarantee comes from this entry.
- D55 (DRAFT) — Considered as the storage home and rejected; see
  rationale above.

## D59 — User-controlled rig order; `display_order` on Rig

**Status:** Drafted 2026-05-13 alongside the drag-and-drop carousel
slice. Existing logbooks have no `<display_order>` element on disk;
the reindex/list code accepts `None` and the create path stamps
fresh values so any pre-D59 rig.xml continues to load.

**Decision.** A rig carries an optional `<display_order>` integer
that drives the carousel order in the UI: the rig with the lowest
non-null value is leftmost, the next is to its right, and so on
1, 2, 3, 4. The first rig added to a fresh logbook gets
`display_order=0`; every subsequent create stamps `max(existing) +
1`, so new rigs land at the right end of the carousel without
disturbing the user-arranged order of older rigs.

The user reorders via drag-and-drop in the carousel; the action
calls a dedicated `POST /api/v1/rigs/reorder` that rewrites every
rig.xml with its new index. Reorder is the **only** client-
controlled mutator for `display_order` — it isn't on `RigCreate`
or `RigUpdate`. The service owns the value the same way D58 owns
`starred`.

This entry supersedes the `list_rigs` ordering documented in
`backend/services/rig_service.py:493-495` ("Ordering: ``created_at``
descending"). The new default is `display_order` ASC, with
`created_at` ASC as the deterministic tiebreaker for rigs missing
a `display_order` (legacy data or a partially-applied reorder).

**Three transitions maintain the dense-integer invariant.**

1. *Create* (`rig_service.create_rig`). Read every existing rig's
   `display_order`. Stamp `new = max + 1` (or `0` when the logbook
   is empty). Rigs missing a `display_order` (pre-D59 legacy data)
   count as `-1` for the max computation so a brand-new rig still
   lands after them.

2. *Reorder* (`rig_service.reorder_rigs`). Takes a list of UUIDs in
   the user's desired left-to-right order. Validates that the list
   is exactly the set of non-trashed rig ids — no missing id, no
   extra id, no duplicate. Under the writer lock, rewrites every
   rig.xml with `display_order = position_in_list`. Atomic per
   file; a crash mid-pass leaves a *partially* reordered state
   that the next list still renders coherently (just not the
   user's intended order). The next reorder call corrects it.

3. *Delete* (`rig_service.delete_rig`). Existing logic stands. The
   deleted rig's `display_order` is left at whatever it was; the
   gap is invisible because list_rigs sorts ascending and the gap
   doesn't reorder the remaining values relative to each other.
   Re-packing the indices on delete would be more work for zero
   user-visible benefit — gaps don't shift the carousel. The next
   reorder call (or the next create, which stamps `max + 1`)
   smooths gaps as a side-effect.

**Amendment to D58.** The successor-election tiebreaker in D58
transition 3 (soft-delete of the starred rig) was: "rig with the
latest `created_at`, then by id". This entry changes the tiebreaker
to "rig with the **lowest** `display_order`, then by `created_at`
ASC, then by id". Rationale: the carousel is the user's mental
model of "which rig is mine"; when the star auto-moves, the
leftmost remaining rig matches the user's expectation better than
the newest. The primary rule (most recently jumped) is unchanged.

**Storage location.** On the rig XML, same posture as D58's
`starred`. Considered: a separate `settings.xml`-style ordering
manifest. Rejected on the same grounds as D58 — the order is a
property the user assigns to the rig itself, and storing it
alongside the rig keeps the "files outlive the app" promise intact
(a jumper opening someone else's logbook sees the same carousel
order without needing a settings file too).

**Consequences.**

- `<display_order>` (optional, `xs:nonNegativeInteger`) added to
  `<rig>` in v1 XSD; legacy rigs parse with `None`.
- `display_order: int | None` on `Rig` and `RigSummary`. Not on
  `RigCreate` / `RigUpdate` — service-controlled only.
- `create_rig` stamps `max(existing.display_order) + 1` (or `0`
  for an empty logbook). `list_rigs` sorts by `(display_order,
  created_at, id)` ASC.
- New endpoint `POST /api/v1/rigs/reorder` with
  `{"rig_ids": [UUID]}` body; 422 on set mismatch.
- D58's successor-election tiebreaker (no-jumps case) amended to
  pick lowest `display_order` first, then `created_at`, then id.
- Frontend carousel reorders via `@dnd-kit/sortable` with
  optimistic local reorder + rollback on API error.

**Crash window.** `reorder_rigs` is N atomic writes (one per rig).
A crash partway through leaves K of N rigs with their new
`display_order` and (N-K) with their old values. The result is a
valid sorted carousel — just not the user's intended order.
Self-healing on the next reorder.

**Non-decisions / deferred.**

- Persisting the dense-integer property as a strict invariant
  through a reindex repair clause (the way D58's starred
  invariant has). Deferred — the cost of a "non-dense" gap is
  zero (the sort still works); the cost of repair code is real.
  If a future use case needs dense indices specifically, add it
  then.
- A drag-handle vs. drag-the-whole-card affordance. Drag-the-
  card is simpler and matches the carousel's current "tap card
  to select" interaction; a dedicated drag handle can be added
  if mobile / accessibility testing surfaces a need.
- Reordering trashed (`.trash/rigs/`) records. Not in scope —
  the carousel only shows live rigs.

**References.**

- D2 — XSD-validates every write; `<display_order>` rides that
  guarantee.
- D3 — Rebuildable from XML. When the rigs index table lands in
  R.3 it'll add a `display_order` column that mirrors the XML.
- D10 — `atomic_write`. Every rewrite in `reorder_rigs` uses it.
- D14 — v0.1 scope. The carousel is part of "rigs CRUD"; reorder
  is the missing CRUD verb.
- D18 — XSD versioning. The optional element is a backward-
  compatible in-place edit; no version bump.
- D33 — Rig model.
- D50 — Writer lock. `reorder_rigs` runs entirely under it.
- D58 — Starred-rig invariant; tiebreaker amended by this entry
  (see "Amendment to D58" above).

## D60 — Starred dropzone: a single default DZ per logbook, used to prefill the jump form

**Status:** Drafted 2026-05-13 as the dropzone analogue of D58.
No data to migrate — no shipped logbook contains the `<starred>`
element on `<dropzone>` yet; the rule "exactly one dropzone is
starred when ≥1 dropzone exists" is satisfied by the auto-star-
on-create behaviour for every existing dropzone.xml retroactively
(see the reindex notes below).

**Decision.** A dropzone carries a boolean `<starred>` flag in
its XML. At any point in time the logbook satisfies a single
invariant:

> If the logbook contains ≥1 non-trashed dropzone, exactly one
> of those dropzones has `starred=true`. If the logbook contains
> zero non-trashed dropzones, no dropzone is starred.

The starred dropzone is the default that the jump-log form
prefills into its `dropzone_id` picker. The invariant means the
form can always preselect when the jumper has any DZ at all —
no "form starts on no-DZ" branch, no "two DZs are both default,
which wins?" tiebreaker.

This entry is the dropzone-side mirror of D58. The semantics,
the no-explicit-unstar rule, and the on-record (not in a
settings file) storage decision are all identical to D58 and
the rationale is not repeated here — see D58 for the discussion.

**Three transitions maintain the invariant.** Every star mutation
lives behind the writer lock (D50), goes through `atomic_write`
(D10) and re-validates the dropzone's XML against the XSD (D2)
before commit, just like every other dropzone write. The
SQLite index row is updated AFTER the XML (D3 ordering) so a
crash between the two leaves the authoritative file on disk
and the next `reindex_from_xml` reconciles.

1. *Create* (`dropzone_service.create_dropzone`). If the logbook
   contains zero non-trashed dropzones at the moment of the
   create, the new dropzone is written with `starred=true`.
   Otherwise it is written with `starred=false`; the existing
   star is left untouched. Rationale per D58 transition 1.
   Implementation detail: the count is read via the SQLite
   index (`SELECT COUNT(*) FROM dropzones`) rather than a
   directory walk, since the dropzones table exists from R.D.3
   and is the canonical projection for "what DZs are live".

2. *Star a different dropzone* (`PUT /api/v1/dropzones/{id}/star`).
   Idempotent. The service walks the dropzones table to find
   every currently-starred dropzone, writes each back with
   `starred=false` (defensive against multi-starred drift), then
   writes the target dropzone with `starred=true`. All writes
   happen under one writer-lock acquisition. The crash window is
   identical to D58: a kill between the clear and the set leaves
   "zero starred" on disk, healed by the next mutation.

3. *Soft-delete the starred dropzone* (`dropzone_service.delete_dropzone`).
   When the deletion target has `starred=true` and ≥1 dropzone
   will remain after the delete, the service auto-moves the
   star *before* the soft-delete commits. The successor is
   selected by:

     a. Most recent jump logged against the dropzone —
        `MAX(date)` over the jumps index, grouped by
        `dropzone_id`, restricted to the remaining dropzones.
        Requires the new `dropzone_id` column on the jumps
        table (see "Consequences" below — schema v9 → v10).
     b. Tiebreaker when no remaining dropzone has any jumps
        logged against it (or several share the same
        `MAX(date)`): the dropzone with the lowest `name COLLATE
        NOCASE`, then `city COLLATE NOCASE`, then id. This
        matches `list_dropzones`'s on-screen order so the star
        moves to the alphabetical-first remaining dropzone — the
        user's mental-model default for a DZ picker. Dropzones
        do not have a `display_order` (D59 is rig-only), so the
        alphabetical order from the DZ picker is the canonical
        "first" surface to fall back to.

   The auto-move write goes through the same lock + atomic +
   XSD-validate path as transition 2. If it fails the soft-
   delete does not commit; the service rolls back via the
   writer lock's discard path. The user sees an RFC 9457
   problem (D16) and the dropzone stays starred-and-present.

**Index schema bump (v9 → v10).** Two correlated changes ship
together so reindex only re-walks once:

- *Add `dropzone_id TEXT` to the jumps table.* Nullable; pre-D60
  jumps and quick-log jumps without a DZ pick stay NULL.
  Populated by `reindex_from_xml` from each jump.xml's
  `<dropzone_id>` element (already present per D44) and by
  `jump_service` on every create/update. Mirrors the v6 → v7
  bump (D33) that added `rig_id` for the same election shape.
  No new SQL index — for v0.1 the jumps table is small enough
  that the `WHERE dropzone_id IN (...) GROUP BY dropzone_id`
  scan is cheap, and we don't yet have a query that filters
  by `dropzone_id` alone outside this election.

- *Add `starred INTEGER NOT NULL DEFAULT 0` to the dropzones
  table.* Rebuildable from `dropzone.starred` in XML (D3).
  Lets `list_dropzones` return the starred flag on
  `DropzoneSummary` without a per-row XML parse.

The schema-version comment block at the top of
`backend/storage/index.py` grows a v9 → v10 note describing
both columns. The drop-and-reindex contract (D26) handles the
schema bump automatically — on the next launch after the v0.1
update lands, the index is dropped and rebuilt against v10.

**Consequences.**

- `<starred>` (optional, `xs:boolean`) added to `DropzoneType` in
  v1 XSD.
- `starred: bool = False` on `Dropzone` and `DropzoneSummary`.
  Not on `DropzoneCreate` / `DropzoneUpdate` — service-controlled.
- SQLite index v9 → v10: adds `dropzone_id TEXT` to jumps and
  `starred INTEGER` to dropzones; D26 drop-and-reindex covers
  the bump on next launch.
- `dropzone_service` implements the three transitions
  (create / set_star / delete with star-transfer) under the
  writer lock with atomic + XSD-validated writes.
- New endpoint `PUT /api/v1/dropzones/{dropzone_id}/star`; no
  DELETE counterpart per D58 rationale.
- Successor election: `MAX(date) GROUP BY dropzone_id` over the
  jumps index, then alphabetical `(name NOCASE, city NOCASE, id)`
  when no jumps reference any candidate.

**Verification.** `pytest backend/`, `ruff check backend`,
`pyright backend` all green.

**Trade-off — coupling the dropzone star to the jumps index.**
Same coupling as D58, same boundedness: a single read against
an already-indexed table, only on the delete of the *starred*
DZ. The fallback (alphabetical) covers the index-unavailable /
no-jumps case so the election is total.

**Non-decisions / deferred.**

- Multi-jumper logbooks (D14 non-decision). When multi-user
  lands, a follow-up D-entry decides whether DZs become
  per-user or stay shared (they're shared in v0.1 because a
  DZ is a real-world place, not user data — see D44).
- Frontend wiring. The DZ list/picker UI showing the star and
  the `LogJumpModal` preselect both land in a follow-up slice;
  this entry is backend-only.
- A separate "favorite / pin" affordance for DZs the jumper
  visits regularly but doesn't want as the default. If a need
  surfaces, add as an additional flag in a future D-entry;
  the starred slot stays reserved for "the one default".

**References.**

- D2 — XSD-validates every write; `<starred>` rides that
  guarantee.
- D3 — Rebuildable from XML. The new `starred` column on
  dropzones and `dropzone_id` column on jumps both restore
  from their respective `<starred>` and `<dropzone_id>`
  elements via `reindex_from_xml`.
- D10 — `atomic_write`. Every star transition uses it.
- D16 — RFC 9457 errors for the new endpoint.
- D18 — XSD versioning. The optional element is a backward-
  compatible in-place edit; no namespace bump.
- D26 — Drop-and-reindex covers the v9 → v10 jumps + dropzones
  schema bump automatically on next launch.
- D33 — Precedent for adding a foreign-key projection column
  (`rig_id`) to the jumps table for an election query.
- D44 — Dropzone entity; `<dropzone_id>` on jump.xml is
  already present.
- D50 — Writer lock. Every star transition runs entirely
  under it.
- D58 — Starred-rig invariant; this entry is the dropzone
  analogue, sharing semantics, transitions, and the
  no-explicit-unstar rule.

---

## D61 — Rename `fun_jump` → `regular_jump`; default-select in log modal

**Decision.** The `jump_types` enum value `fun_jump` (D53) is
renamed to `regular_jump` in-place across the v1 schema, Pydantic
model, tests, and frontend, with no namespace bump and no
deprecation alias. The log-jump modal default-selects
`regular_jump` on the create path (initial open and post-submit
reset). The edit path is untouched: existing jumps hydrate from
their persisted `jump_types` and the preselect does not override.

**Why.**

- "Fun jump" is industry slang that conflates "no formal role on
  this jump" with a vibe. "Regular jump" is the neutral term and
  matches the way the value is actually used — the catch-all when
  none of coaching / instructing / camera / organizing / coached /
  instructed applies. Renaming the label without renaming the wire
  value would create a split between what the user sees and what
  the XML stores; the project's data-survives-the-app posture
  (D2, §README) makes that split worth avoiding.
- Default-selecting `regular_jump` on a new jump matches the
  majority case. The chip is one click to deselect or replace,
  which is cheaper than one click to add every time. The edit
  path stays faithful to persisted data so the preselect is a
  create-time UX nudge, not a model-layer default.
- The model-layer default stays "absent ≡ unset" per D53. Only
  the modal's initial form state preselects the value. The
  distinction matters because (a) the XSD does not require
  jump_types to be present (zero-or-more), and (b) a hand-edited
  jump.xml without a `<jump_types>` block must keep round-tripping
  unchanged.

**D18 trade-off (owner-overridden).** D18 says "old jump files
must keep validating forever" and prescribes additive add /
deprecate-old / v2-bump for any enum change. This rename strips
`fun_jump` from the v1 enum — any existing on-disk jump.xml using
the old value would fail validation. Alex explicitly overrode the
invariant on the grounds that the logbook has not shipped and a
grep of the repo + the active logbook folder found zero files
containing `fun_jump`. The cost of D18 compliance (additive enum
+ deprecation window or v2 bump + migration script + dual-read)
was disproportionate to the cost of the rename itself in this
pre-production window. The override is documented here so future
schema changes don't inherit it: post-release, D18 is back to
binding.

**Consequences.**

- `JumpType.REGULAR_JUMP = "regular_jump"` replaces `FUN_JUMP` in
  `backend/models/jump.py`.
- `<xs:enumeration value="regular_jump"/>` replaces `fun_jump` in
  `backend/xml/schema/SCHEMA.v1.xsd`; the doc comment is updated
  and notes the value is the modal-side default.
- `backend/tests/test_d53_jump_fields_roundtrip.py` is updated for
  the rename; the enum-cardinality assertion (`len(all_types) == 7`)
  is unchanged.
- `frontend/src/modals/LogJumpModal.jsx` updates the chip label to
  "Regular jump" and seeds `form.jump_types = ['regular_jump']` in
  three places: the initial `useState`, the create-path reset
  effect, and the post-submit reset.
- No migration script ships. If a v1 jump.xml with `fun_jump`
  surfaces post-rename (e.g. restored from a backup taken
  pre-rename), validation will reject it; the fix is a one-shot
  find-and-replace by hand or a future migration utility.
- This entry partially supersedes the `fun_jump` line in D53;
  D53's other field additions (`landing_distance_m`, `packed_by`,
  `group_members`) are untouched.

**Alternatives considered.**

- *Additive v1: add `regular_jump`, deprecate `fun_jump`.* Rejected
  for owner-stated reasons above — disproportionate to the rename
  in pre-production. Worth revisiting as the pattern post-release.
- *Bump to SCHEMA.v2.xsd.* Rejected on the same grounds; v2 ships
  for a substantive change, not a single enum rename.
- *Rename only the display label, keep the wire value `fun_jump`.*
  Rejected — splits UI from XML and undermines the
  data-readable-without-the-app principle.

**References.**

- D2 — XML validity on every write; the renamed enum value still
  validates because the XSD changed with it.
- D18 — Schema versioning; explicitly overridden by Alex for this
  one pre-production rename.
- D53 — Source decision for `jump_types`; this entry supersedes
  the `fun_jump` value only.
- D57 — Precedent for removing/renaming D53 fields in-place
  pre-release (per-jump `<environment>`, `landing_direction`,
  `group_size`).

---

## D62 — `verify` is parse-only in `.trash/`; non-jump trash subdirs are skipped

**Decision.** `verify_logbook` runs **two different strictness
levels** over the logbook:

1. **Active jumps** (`jumps/`) — full XSD validation per D2. A
   jump that doesn't validate is `invalid_xml`. Unchanged.
2. **Trashed jumps** (`.trash/<ts>_<name>/`) — parse-only. The
   hardened parser still rejects malformed bytes (`invalid_xml`),
   but XSD validation is skipped. `manifest.from_jump_xml` gains
   a `validate_xsd: bool = True` kwarg; verify passes `False` for
   trash. Every other per-folder check (attachment hashes,
   manifest contents, orphan files) runs unchanged.

Direct children of `.trash/` are classified into three buckets:

- **Trashed-jump folder** — name matches the basic-ISO regex
  `^\d{8}T\d{6}(\.\d{3})?Z?_` produced by
  `storage.trash._now_utc_basic_iso`. Run trash-flavored verify.
- **Namespace subdir** — `rigs`, `dropzones`, `inventory`,
  `people`, `jumpers`. Skipped in v0.1 with a TODO. Trashed
  non-jump entities (D33 rigs, D44 dropzones, D54 people) are
  not validated here; entity-aware trash verify is a follow-up.
- **Unknown** — anything else. Reported as `unknown_trash_entry`
  so a surprise (manual `mv`, half-finished migration, bug) is
  loud rather than silent.

**Why.**

- In-place schema renames (D57 removed `landing_direction` /
  `group_size`; D61 renamed `fun_jump`) leave older trashed
  files structurally fine but XSD-noncompliant. D18 was
  explicitly overridden for these renames pre-production. Pre-
  D62, verify's strictness made every old trashed jump surface
  as `invalid_xml`, training users to ignore verify output.
  That's a bigger integrity-loss risk than tolerating schema
  drift in historical records.
- Trash is by D19's design *historical* — the soft-delete is a
  recovery mechanism, not a write surface. There is no future
  write to a trashed file (only restore, which transitions it
  back to `jumps/` and re-subjects it to D2 validation). Parse-
  only validation is therefore consistent with the trash's role
  as immutable history.
- Verify pre-D62 treated every direct child of `.trash/` as a
  jump folder. After D33 (rig manager) and D44 (dropzones) added
  their own trash subdirs via `soft_delete(subdir=...)` and
  `soft_delete_file(subdir=...)`, those namespaces surfaced as
  `invalid_folder · missing jump.xml`. The classification step
  closes that false-positive gap.
- The `unknown_trash_entry` branch keeps the alert channel open:
  if `.trash/` ever contains a direct child that's neither a
  jump folder nor one of the known namespaces, that's a real
  surprise (likely manual tampering or a bug in soft_delete) and
  should not pass silently.

**Consequences.**

- `_verify_folder` gains an `is_trash: bool = False` kwarg.
  Default behavior is unchanged.
- `manifest.from_jump_xml` gains a `validate_xsd: bool = True`
  kwarg. Every existing caller is unchanged; only verify (in
  trash mode) passes `False`. Docstring updated to flag this as
  the D62 path.
- `storage/verify.py` adds `_TRASH_JUMP_NAME_RE` and
  `_TRASH_NAMESPACE_SUBDIRS`. The regex permits both the older
  no-millisecond shape (`YYYYMMDDTHHMMSSZ_`) and the current
  millisecond shape (`YYYYMMDDTHHMMSS.fffZ_`) produced by
  `storage.trash._now_utc_basic_iso`. The `Z` is also tolerated
  as optional because some pre-existing test fixtures predate
  the `Z`-always rule; verify is permissive on read.
- New verify issue kind: `unknown_trash_entry`. CLI callers that
  switch on `issue.kind` should handle it (treat as an error or
  log-and-continue per their threshold).
- Test surface: 7 new tests in `backend/tests/test_verify.py`
  pin (a) trashed jump with D57-removed field passes verify,
  (b) live jump with same field still fails verify (regression
  guard), (c) trashed jump with truly malformed bytes still
  fails, (d) rigs/dropzones/inventory/people/jumpers namespace
  subdirs are all silently skipped, (e) unknown trash entries
  surface, (f) both `Z` / `.fffZ` trash-name shapes recognised.
- Active-namespace duplicate detection (D23) is unchanged —
  trash was already disjoint from active.

**Deferred — entity-aware trash verify.** A follow-up D-entry
will define per-entity validation for `.trash/rigs/`,
`.trash/dropzones/`, `.trash/inventory/`, `.trash/people/`, and
`.trash/jumpers/`. Each carries its own XSD shape (D33 rig,
D44 dropzone, D54 person, plus jumpers from D47 and inventory
shelves from D34). Scoping is non-trivial and out of D62 per
CLAUDE.md §3 (small phased slices). Until then, those subtrees
are not validated by verify — orphan / hash / structural drift
in trashed non-jump entities goes silently uncaught. The cost
of silence in trash is bounded: D19's role for trash is
recovery, not active correctness.

**Alternatives considered.**

- *Migrate trashed jumps when schemas evolve.* Rejected — the
  trash's intuition is "frozen historical record"; rewriting it
  defeats that. Also it would force every D18 override to ship
  a one-shot migration utility, raising the cost of small
  pre-production renames like D61.
- *Skip `.trash/` from verify entirely.* Rejected — undermines
  D19's "included in verify" explicit consequence, and would
  hide truly corrupt trashed files (parse failure, hash
  mismatch) along with the drift this entry is about.
- *Make verify XSD-strict everywhere; force the user to empty
  the trash to silence noise.* Rejected — see Why above; this
  was the pre-D62 behavior and it actively trained users to
  ignore verify output, which is worse than the drift it was
  catching.
- *Skip-mode for the namespace subdirs but still XSD-validate
  trashed jumps.* Rejected — the namespace fix and the parse-
  only fix address two faces of the same root cause (D33/D44
  + D57/D61 = trash that doesn't pass current verify rules) and
  shipping them together is what closes the user-visible noise
  on a single pass.

**References.**

- D2 — XML validity on every write. Unchanged. Trash is read-
  only relative to the writer; D2 still binds the live path.
- D18 — Schema versioning. Override fallout (D57, D61) is the
  proximate cause for the parse-only stance.
- D19 — Soft-delete to `.trash/`. The "included in verify"
  clause is refined here: included means parse-only for
  jumps, skip-for-now for non-jump namespaces.
- D23 — Duplicate jump_number. Unchanged; trash remains
  disjoint from the active uniqueness namespace.
- D25 — Verify's role and scope. D62 narrows verify's
  strictness in trash; the other checks (hash, manifest,
  orphan) are unchanged.
- D33 — Rig manager. Introduces `.trash/rigs/` via
  `soft_delete(subdir="rigs")`.
- D44 — Dropzone entity. Introduces `.trash/dropzones/` via
  `soft_delete_file(subdir="dropzones")`.
- D54 — People entity. Same trash-subdir posture.
- D57 — Removed D53 fields in-place pre-release. Direct cause
  of historical trash failing post-rename.
- D61 — Renamed `fun_jump → regular_jump` in-place pre-release.
  Same cause class as D57; same fix class as D62.


## D63 — License: GPL-3.0; supersedes D13

**Decision.** The project is licensed under GPL-3.0 (see ``LICENSE``
at the repository root). Supersedes D13 (MIT). The change happened
with the v0.1.0-beta.1 release prep, before any external contributor
PR — relicensing was a one-line edit; once external commits land
the same change would require permission from every contributor.

**Why.** The MIT framing in D13 was "low friction for users, forks,
and downstream commercial use." That framing assumed the project's
risk surface was *adoption* — that any restriction would limit
uptake. After the v0.1.0-beta.1 review the maintainer surfaced a
different concern: a permissively-licensed fork can be taken
closed-source, modified, and distributed without contributing
improvements back. MIT does nothing to prevent that. GPL-3.0
specifically does — derivative works must remain open and GPL-3.0
licensed, so any improvement that ships to users is required to
flow back to the community.

The realistic threat model for this project (a niche self-hosted
skydiving logbook) is small — there is no obvious commercial
exploitation surface, and AGPL-style network-clauses are overkill
for a desktop app. GPL-3.0 strikes the balance: end users notice
no difference (they can still install, use, and modify freely),
but commercial forks have a binding obligation to share back.

The trade-off accepted: corporate contributors are more cautious
about GPL than MIT. We accept that — this isn't a project that
needs corporate adoption to succeed.

**Consequences.**

- ``LICENSE`` replaced with the canonical GNU GPL-3.0 text from
  https://www.gnu.org/licenses/gpl-3.0.txt.
- ``pyproject.toml`` classifier flipped from ``MIT License`` to
  ``GNU General Public License v3 (GPLv3)``.
- README badge, License section, and Settings → About card all
  display GPL-3.0.
- ``CONTRIBUTING.md`` notes that contributions are offered under
  GPL-3.0; no CLA, but contributors confirm they have the right to
  release the code under GPL-3.0.
- D13 is marked superseded; the file remains in place per the
  project's "supersede, don't edit" rule (CLAUDE.md §4).
- The previous initial commit (``d0e30b8``, MIT-licensed) is on
  the public ``main`` branch. Any redistribution of *that exact
  commit* by a third party can rely on MIT terms — git history is
  immutable. From the GPL-3.0 commit forward, GPL-3.0 governs.
  This is the standard "relicensing of future versions" pattern
  every OSS project uses; it is not a defect.

**Re-evaluation triggers.** This decision flips when any of:

- The project gains traction with a corporate user who would only
  contribute under a permissive license, AND the maintainer wants
  that contribution badly enough to relax. Practically requires
  contributor consent.
- A future commercial dual-license model lands (GPL community use
  + paid commercial license). That augments rather than replaces
  D63; AGPL-3.0 is usually the right base for the GPL half of a
  dual-license setup.

**Alternatives considered.**

- *(MIT, status quo)* Rejected for the closed-source-fork concern
  above.
- *(Apache 2.0)* More permissive than GPL but with explicit patent
  grant. Doesn't address the closed-fork concern; rejected on the
  same grounds as MIT.
- *(AGPL-3.0)* Stronger copyleft (covers SaaS network use). For a
  desktop app distributed as a binary, the network clause has no
  effect on the realistic threat model — a SaaS-of-skydive-logbook
  isn't a real risk surface. Reserved as the right answer if
  dual-licensing arrives.
- *(BUSL / PolyForm Noncommercial)* Source-available rather than
  open source. Not OSI-approved; would kill adoption to defend
  against a commercial-exploitation scenario that isn't realistic
  here. Rejected.

**References.**

- D13 — original MIT decision (superseded).
- 2026-05-14 conversation that triggered the relicense (v0.1.0-beta.1
  release prep).
- GPL-3.0 canonical text — https://www.gnu.org/licenses/gpl-3.0.txt
- Choose a License — https://choosealicense.com/licenses/gpl-3.0/


## D64 — In-app auto-update for v0.2; EdDSA-signed, free path

**Decision.** v0.2 ships a user-initiated in-app auto-updater that
downloads, verifies, and applies a new binary without requiring the
user to leave the app. The flow is **app-level signed** (Ed25519
signatures over the release manifest), **not OS-signed** — no paid
Apple Developer ID or Authenticode certificate is required. D52's
re-evaluation trigger ("real auto-update must be signed at the OS
level *or* at the app level — the successor decision picks one")
fires here in favor of app-level. Auto-update is no longer deferred
per CLAUDE.md §10.

The flow is **user-triggered, not silent**. The existing *Check for
updates* button in Settings (D52) gains a second action — *Download
and install* — surfaced only when an update is actually available.
There is no background download, no auto-apply on launch, no "the
app updated itself overnight" anti-pattern.

**Why.**

- D52's deferral of auto-update was tied to the cost of OS-level
  signing certificates. App-level signing using Ed25519 (the
  Sparkle-style approach) sidesteps that cost entirely while still
  meeting D52's re-evaluation requirement that "real auto-update
  must be signed."
- Once external users install the app, every bug fix is friction
  unless the update path is one-click. Manual download-and-replace
  is acceptable for v0.1's pre-user phase; it's a tax on every
  fix once users exist.
- App-level signing is a stronger threat-model fit than relying on
  GitHub Releases' HTTPS alone. A signature pinned to a key embedded
  at build time defends against GitHub compromise, mirror tampering,
  and malicious release-asset replacement — none of which OS-level
  signing alone defends against either.
- User-initiated (not silent) preserves the transparency property
  D52's manual download path provided: the user sees the new
  version, consents to the install, and watches it apply.

**Architecture.**

- **Signing key.** A single Ed25519 keypair governs releases for
  the lifetime of this decision. The **public key** is embedded
  into the app at build time (constant in Python source, no
  network fetch). The **private key** lives offline in the
  maintainer's password manager and is loaded into CI as a
  GitHub Actions secret (`UPDATE_SIGNING_KEY`) only during the
  release-tag workflow. Key rotation requires a new D-entry and
  a transitional dual-key build.
- **Release manifest.** Every signed release publishes three
  assets in addition to the platform binaries:
  - `SHA256SUMS` — line-per-asset SHA-256, plain text.
  - `SHA256SUMS.sig` — detached Ed25519 signature over the bytes
    of `SHA256SUMS` (64-byte raw signature).
  - `release.json` — small JSON manifest with `version`,
    `release_url`, per-platform `{filename, size, sha256}` entries.
    Used by the updater to pick the right asset without parsing
    asset filenames.
- **Update flow.**
  1. User clicks *Check for updates* → existing
     `GET /api/v1/updates/check` returns `update_available`.
  2. UI shows *Download and install* button.
  3. User clicks → frontend calls
     `POST /api/v1/updates/download`. Backend:
     a. Pre-flight: free space in the OS temp dir ≥ asset size +
        100 MiB headroom. If not, return 507
        `update_no_space` (problem+json).
     b. Stream the platform asset to a temp file (resumable
        on second attempt is *not* a v0.2 requirement —
        re-download from zero is acceptable).
     c. Stream `SHA256SUMS` and `SHA256SUMS.sig`.
     d. Verify Ed25519 signature on `SHA256SUMS` against the
        embedded public key. Mismatch → delete temp files,
        return 502 `update_signature_invalid`.
     e. Verify the downloaded asset's SHA-256 against the
        corresponding entry in `SHA256SUMS`. Mismatch → delete
        temp, return 502 `update_hash_mismatch`.
     f. Persist `{verified_asset_path, target_version}` into a
        process-scoped state slot. Return 200 with that state.
  4. UI shows *Quit and install*.
  5. User clicks → frontend calls
     `POST /api/v1/updates/apply`. Backend:
     a. Writes the per-platform helper script to a known temp
        path. Spawns it detached with `start_new_session=True`
        (POSIX) / `DETACHED_PROCESS` (Windows). Helper script
        immediately sleeps ~1.5s to give the parent process time
        to exit cleanly.
     b. Writes a marker file at
        `user_config_dir/update_pending.json` with the target
        version. The new app reads this on boot and shows a
        toast.
     c. Returns 202 (Accepted) and immediately schedules a
        clean shutdown (FastAPI lifespan + pywebview window
        close).
  6. Helper waits for the parent PID to die (polling with a 30s
     timeout), performs the platform swap, and relaunches the
     app.
  7. New app boots, reads the marker, shows
     *Updated to vX.Y.Z*, deletes the marker, and resumes
     normal operation.
- **Telemetry.** Every state transition (download started, hash
  verified, signature verified, swap initiated, swap succeeded,
  any failure with kind+detail) logs to the D27 rotating log at
  INFO (success path) / ERROR (failures). Silent failures are
  the worst class of update bug — instrumentation is mandatory
  for this feature.

**Verification & threat model.**

- TLS to GitHub establishes that we're talking to GitHub. Ed25519
  signature verification establishes that the *content* came from
  the maintainer, regardless of what GitHub or any mirror says.
  Both layers must pass.
- The embedded public key is the trust anchor. Compromising it
  requires compromising the build pipeline at release time.
- Compromising the private key (laptop loss, password-manager
  breach) requires a new keypair, a new D-entry, and a
  transitional dual-key build (next version accepts either old
  or new key; subsequent versions accept only new). Out of scope
  for v0.2; documented as a known operational risk.
- This scheme does **not** defend against the OS itself being
  compromised, against malicious builds shipped to the public
  release pipeline by an attacker with maintainer credentials,
  or against the user manually downloading and running an
  unsigned binary. None of those are in scope for the free path.
- macOS quarantine (`com.apple.quarantine` xattr) is stripped by
  the running, already-trusted parent process before the swap.
  This is the same technique Sparkle uses without Developer ID;
  Apple has not removed it as of macOS 15. Documented as a
  re-evaluation trigger if Apple clamps down.

**Per-platform swap mechanics.**

- **Linux (AppImage).** Atomic. The helper script `chmod +x` the
  new file, `os.replace`s it over the old AppImage path, and
  `execv`s the new file. No external helper needed; the running
  AppImage's content lives in a mount that survives until the
  process exits.
- **macOS (`.app` bundle).** The helper is a small shell script
  written to `$TMPDIR/skydive-update-helper-<pid>.sh`. It:
  1. Waits for the parent PID to exit (`while kill -0 $PPID`).
  2. `xattr -d com.apple.quarantine` on the new `.app` (the
     download itself was quarantined; we strip it inside the
     trusted process tree so the swap is treated as a
     continuation rather than a fresh install).
  3. `rm -rf` the old `.app`, `mv` the new one in place.
  4. `open` the new app.
  5. Deletes itself.
- **Windows (`.exe`).** A `.bat` helper at
  `%TEMP%\skydive-update-helper-<pid>.bat`:
  1. Polls for the parent PID's exit
     (`tasklist /FI "PID eq %PARENT_PID%"`).
  2. `move /Y` the new `.exe` over the old one.
  3. `start ""` the new `.exe`.
  4. `del` itself via a queued delayed delete.

**REST API surface.**

- `GET /api/v1/updates/check` — unchanged (D52).
- `POST /api/v1/updates/download` — new. Returns 200 with
  `{verified, version, asset_path}` on success; problem+json
  on failure (`update_no_space`, `update_signature_invalid`,
  `update_hash_mismatch`, `update_download_failed`).
- `POST /api/v1/updates/apply` — new. Returns 202; the
  app proceeds to shut down after the response is flushed.

**Consequences.**

- New module `backend/services/update_install_service.py` for the
  download + verify + apply orchestrator. Per-platform swap logic
  in `backend/services/update_install_<platform>.py` modules
  dispatched at runtime.
- Helper script templates ship under
  `scripts/packaging/{macos,windows,linux}/update_helper.*`.
- The CI release workflow gains a *signing* step: after building
  all platform artifacts, it generates `SHA256SUMS` and
  `release.json`, signs `SHA256SUMS` with the Ed25519 private
  key (`UPDATE_SIGNING_KEY` secret), and uploads all three
  alongside the binaries to the GitHub Release.
- The PyInstaller / py2app specs gain an embedded
  `update_signing_pubkey.txt` in the bundle's resources.
- `backend/observability/` gains structured log events for the
  six update state transitions, all tagged
  `event=update.<phase>`.
- `pyproject.toml` gains `cryptography>=42` (Ed25519 verification).
  The dependency is already present transitively but pinned
  explicitly to make the signature path's dependency surface
  visible.
- This decision augments D52 rather than superseding it: D52's
  baseline (unsigned binaries, no OS-level signing for v0.1) still
  applies to *binary trust on first install*. D64 adds app-level
  signing only to the *update channel* — the user still
  bypasses Gatekeeper/SmartScreen on first install per D52.

**Phasing.** Each phase ships as a small, independently-verifiable
slice per CLAUDE.md §3.

- **U.0** — This D-entry; update CLAUDE.md §10 to remove
  auto-update from the deferred list.
- **U.1** — CI publishes `SHA256SUMS`, `SHA256SUMS.sig`, and
  `release.json` on every signed release. Documentation for the
  maintainer's signing-key generation and storage workflow.
- **U.2** — Backend download + verify endpoint with the embedded
  public key. Frontend wires a *Download update* button that
  shows the verified state. **No swap yet** — user replaces
  manually. This is end-to-end useful on its own (verified
  downloads > unverified ones).
- **U.3** — Linux AppImage swap helper + *Quit and install* UX.
  First platform with one-click update; smallest swap mechanism.
- **U.4** — macOS `.app` swap helper with quarantine-strip.
- **U.5** — Windows `.exe` `.bat` helper.
- **U.6** — Post-update marker, *Updated to vX.Y.Z* toast,
  error/retry states, mid-download abort paths, full failure-mode
  test matrix.

**Re-evaluation triggers.** This decision flips when any of:

- Apple removes quarantine-strip from the OS (macOS swap stops
  working). Triggers a successor decision: OS-level signing
  (Developer ID + notarization) becomes mandatory on macOS.
- Windows tightens SmartScreen such that unsigned `.exe` updates
  are blocked outright (rather than warned). Triggers
  Authenticode signing on Windows.
- The user base exceeds the level where the Windows SmartScreen
  warning becomes a meaningful support burden. Triggers
  Authenticode (D52 re-evaluation trigger #2).
- App Store / Microsoft Store distribution becomes a goal (D52
  re-evaluation trigger #4). Stores enforce OS-level signing
  and disallow side-loaded auto-update channels.
- The signing private key is suspected compromised. Triggers an
  emergency successor decision with a key rotation transition.

**Alternatives considered.**

- *(Wait for OS-level signing certs, defer auto-update to v0.3)*
  Rejected. The user friction of "every update is a manual
  download" was specifically called out as the motivation for
  doing this now. Waiting on certs delays the user-visible win
  by months while solving the wrong problem (certs reduce
  install friction; auto-update reduces update friction —
  different).
- *(Silent background auto-update on launch)* Rejected on
  transparency grounds. The user should always consent to the
  app being replaced. Maintainer trust is preserved by making
  the consent moment obvious. Re-evaluate if user feedback says
  the confirmation step is too much friction.
- *(GitHub Releases trust alone — HTTPS, no signature)* Rejected.
  GitHub is a single point of compromise; a malicious push to
  the release tag could ship a backdoored binary to every user.
  Ed25519 signatures pin trust to a key the maintainer controls.
- *(GPG signatures instead of Ed25519)* Rejected on UX and
  surface-area grounds. GPG drags in a heavy crypto stack, a key
  ring, and a model designed for human-to-human trust. Ed25519
  via `cryptography.hazmat` is a one-call verify, one-call sign,
  no key ring, no expiry, no revocation — the right primitive
  for machine-to-machine release-asset signing. Sparkle uses
  exactly this design.
- *(Differential updates / delta patches)* Rejected for v0.2.
  Full-binary updates are simpler and the binary is small
  (~80 MiB packed). Revisit if bandwidth or download time becomes
  a complaint.
- *(Pre-release / beta channel toggle in Settings)* Deferred to
  v0.3. v0.2 ships **stable channel only** — `update_check_repo`
  points at the canonical repo and the updater consumes the
  latest non-prerelease tag. A Settings toggle for opting into
  pre-releases is a small addition once the stable path is solid.

**References.**

- D11 — Packaging via PyInstaller/py2app; the bundle layout this
  feature operates on.
- D14 — v0.1 scope. Auto-update was originally deferred here;
  CLAUDE.md §10 mirrored that. Both are updated by this entry.
- D16 — RFC 9457 problem+json error shape; all the update
  failure modes return this.
- D20 — `user_config_dir`; that's where the update-pending
  marker file lives.
- D27 — Rotating log sink; update telemetry writes here.
- D52 — Unsigned binaries for v0.1; whose re-evaluation trigger
  ("real auto-update must be signed at OS or app level") fires
  here in favor of app-level Ed25519.
- D63 — GPL-3.0; the helper scripts inherit the same license.
- Sparkle's EdDSA signing flow —
  https://sparkle-project.org/documentation/ed-signatures/
- `cryptography` Ed25519 docs —
  https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/


## D65 — Guided first-run onboarding wizard with a sentinel completion file

**Decision.** A new jumper who opens a fresh logbook is walked through
a multi-step in-app wizard that creates their first dropzone, their
inventory components (container, main, reserve, AAD), and their first
rig — leaving them ready to log a jump. The wizard renders in the
React SPA (not the launcher's pywebview welcome HTML); it reuses the
existing service-layer + REST surface (POST ``/dropzones``,
``/containers``, ``/mains``, ``/reserves``, ``/aads``, ``/rigs``) and
the existing form modals where shape allows. The launcher's job
narrows back to "pick a folder, run bootstrap, hand off to the SPA".

Completion is recorded as a sentinel file ``.onboarding_completed``
at the logbook root. Its presence dismisses the wizard on every
subsequent launch; its content is a small JSON document that records
``completed_at`` (D17 timestamp) and ``status`` (``"finished"`` when
the user walked every step, ``"skipped"`` when they dismissed). The
content is informational — only the presence of the file is
load-bearing.

**Why.** The bootstrap path before this entry produced a useable but
opaque experience: the launcher dropped the user on the React app's
Profile tab with empty rigs/dropzones/inventory lists and zero
guidance on what to do first. A new jumper who didn't read the
README had no obvious path to "log a jump" because the LogJumpModal
needs at least one rig, and a rig needs all four components, and the
wear math wants a dropzone. The wizard fills that gap by chaining
the existing create flows so the user reaches "ready to log" in one
linear pass.

A sentinel file is the smallest viable persistence layer for the
"dismiss forever" state. The alternatives:

- A new ``settings.xml`` inside the logbook folder. The "right" home
  for per-logbook app state, but D20 deferred it ("config.toml lives
  in user config dir; per-logbook settings deferred"). Introducing
  it for a single flag would be over-spec; we'd need an XSD and
  versioning. Revisit if a second per-logbook flag arrives.
- A row in the SQLite index. The index is rebuildable from XML
  (D3) — onboarding state would have to also live in XML to satisfy
  D3, which collapses back to the settings.xml path above.
- A bit on the existing manifest. The manifest is the SHA256SUMS
  integrity surface; reusing it for unrelated app state conflates
  concerns and would force a manifest-version bump.

The sentinel approach is the minimum spec: a single hidden file,
atomic-written via the existing ``atomic_write`` primitive (D10),
read by a single service function. When more per-logbook flags
arrive, the migration is a one-time read of the sentinel and a
write into a new settings file.

**Wizard shape.** Seven linear steps inside the React SPA (one
welcome frame + six form steps), in order:

  1. **Welcome.** Sets context: "We'll set up your home DZ, your
     gear, and your rig." Two affordances: "Get started" advances
     to step 2, "Skip for now" writes ``status="skipped"`` and
     dismisses the wizard.
  2. **Home dropzone.** Reuses the existing ``DropzoneModal`` form.
     The first DZ in a fresh logbook is auto-starred by
     ``dropzone_service.create_dropzone`` (D60 transition 1) — the
     wizard does nothing special on top.
  3. **Container.** All ``ContainerCreate`` fields exposed; only
     ``status`` is required (the rest are optional per D34).
  4. **Main canopy.** Required: ``size_sqft`` + ``default_environment``.
     Optional: identification + lineset. Lineset is shown but
     skippable so a brand-new jumper isn't blocked on knowing their
     line type (D38 "onboarding" path: deferred lineset is legal).
  5. **Reserve canopy.** All ``ReserveCreate`` fields exposed.
  6. **AAD.** All ``AADCreate`` fields exposed.
  7. **Build your rig.** Pre-fills the four ``current_*_id`` from
     the components created in steps 3–6, asks for ``nickname`` +
     ``jurisdiction``. Disabled if any component step was skipped
     (D37 invariant: a rig needs all four).

Every step has Back / Continue / Skip. Skip on any step records
``status="skipped"`` and dismisses. Finish on step 7 records
``status="finished"`` and dismisses. Both end states write the
sentinel; the wizard reads it on next launch and stays hidden.

**Resumption.** When the sentinel exists but the logbook is missing
pieces (user skipped mid-wizard, or created data outside the wizard
and then re-opened the app), Profile renders a small "Finish setup"
banner pointing at the next missing step. The banner is dismissable
per session (in-memory) so a deliberate skipper isn't nagged on
every render, but re-appears next launch — matching the "sentinel
is final; banner is a nudge" split. This is the resumption
affordance promised in D14's "v0.1 UX" envelope and unblocks the
"I skipped but want to come back later" workflow without forcing
an in-XML state machine.

**Trigger.** On every React mount, the SPA calls
``GET /api/v1/onboarding``. The endpoint returns:

```
{
  "completed": <bool>,                       # sentinel exists
  "completed_at": "<D17 timestamp>" | null,  # parsed from sentinel
  "status": "finished" | "skipped" | null,   # parsed from sentinel
  "has_jumper": <bool>,                      # listJumpers > 0
  "has_dropzones": <bool>,                   # COUNT(*) FROM dropzones > 0
  "has_rigs": <bool>                         # rigs/ has any folder
}
```

The SPA renders the wizard when ``!completed`` and at least one of
the three ``has_*`` flags is false. The wizard never appears when
``completed`` is true.

**Consequences.**

- New service module ``backend/services/onboarding_service.py``
  owns sentinel read/write and the three "has_*" counts. The
  sentinel is written through ``atomic_write`` (D10) and validated
  as JSON on read — a malformed sentinel logs at WARNING and is
  treated as "presence only, status unknown".
- New REST endpoints under ``/api/v1/onboarding``:
  ``GET`` (state) and ``POST /complete`` (body ``{"status": …}``,
  writes sentinel and returns the updated state).
- New React component ``OnboardingWizard.jsx`` mounted at App.jsx's
  root. Renders nothing until the status fetch resolves; renders
  the wizard or null after. A "Finish setup" banner lives in
  ``Profile.jsx`` for the dismissed-but-incomplete state.
- The launcher's hardcoded welcome HTML loses the personal greeting
  ("Good morning, Alex" — leaked from a dev session). The welcome
  step now lives in the SPA where it can be themed alongside the
  rest of the app and reuses the existing component library.
- No XSD, schema, or manifest bumps. The sentinel is plain JSON
  and lives outside the on-disk record set that ``verify`` walks.
  ``verify`` ignores the sentinel (dot-prefix, like ``.trash/``).
- The wizard does NOT inflate scope: every step is a thin shell
  around an existing service function. No new fields, no new
  invariants. v0.1 scope (D14, as superseded by D33) is unchanged.

**Re-evaluation triggers.** This decision flips when any of:

- A second per-logbook flag arrives (e.g. "tour mode dismissed",
  "imported from another logbook"). At two flags, migrate the
  sentinel into a proper ``settings.xml`` with an XSD and bump
  D20 to formalise the per-logbook config home.
- Multi-jumper (D33 deferred): the wizard becomes per-jumper rather
  than per-logbook, which moves the sentinel into the jumper's XML.
- The wizard grows branches (recreational vs tandem vs AFF-student
  paths). Linear shape can stay; the branching belongs in the SPA,
  not in the sentinel.

**Alternatives considered.**

- *(No wizard; rely on Profile's existing ``OnboardingForm`` and let
  the user discover dropzone/inventory/rig via the sidebar.)* The
  status quo. Rejected for the "log a jump" cliff above — a new
  jumper hits the LogJumpModal, sees it demands a rig, and has no
  obvious path forward.
- *(All-in-launcher wizard, HTML/JS in launch_desktop.py.)* Rejected
  because it duplicates the form layer that already lives in React
  (DropzoneModal, AddComponentModal, AddRigModal) and forces the
  launcher to grow a UI framework. The launcher's job is "boot
  fast, hand off"; the SPA owns user-facing UI.
- *(Inline empty-state cards on each tab — no modal wizard.)* Less
  intrusive but fragments the linear "set up everything you need"
  story across five tabs; the user has to know which tabs to visit
  in which order. The wizard solves the sequencing problem in one
  place. The Profile banner is the inline-card fallback for the
  resumption case.
- *(``settings.xml`` from day one.)* Over-spec for a single flag —
  see the sentinel rationale above. Revisit when a second flag
  arrives.

**References.**

- D10 — atomic_write is the only persistence primitive.
- D14 — v0.1 scope (foundation for "ready to log a jump").
- D20 — config.toml lives in user config dir; per-logbook
  ``settings.xml`` deferred.
- D33 — rig manager + the "all four components" invariant the
  wizard chains.
- D37 — every component in zero or one rigs at any time
  (constrains step 7).
- D38 — used-gear onboarding path; lineset can be deferred.
- D44 — dropzone first-class entity.
- D60 — first DZ auto-stars; the wizard does nothing special on top.
- 2026-05-14 conversation that scoped this slice (OB.1 — wizard
  framework + Welcome step + sentinel mechanism; OB.2 through OB.5
  add the per-step forms in subsequent slices).


## D66 — `RigUpdate` accepts `repack_history`; narrows D38's R.5 deferral

**Decision.** ``RigUpdate`` accepts an optional ``repack_history``
field. When present, the rig service writes the supplied list to
``rigs/<nickname>/rig.xml`` (replacing whatever was on disk). When
omitted (Pydantic default ``[]``), the merge uses the on-disk
value untouched — matching the pre-D66 behaviour for legacy
clients. This narrows D38's "R.5 territory" deferral: jumpers can
now set or correct their repack history through the regular rig
edit surface without hand-editing ``rig.xml``.

The R.5 full repack-event flow (atomic append + reserve/AAD
counter updates + clock recomputation) remains deferred. D66 only
unlocks the *metadata-shaped* path for editing the history list;
the cross-component side-effects D38 §Consequences described
(``repack_count_derived`` bump, AAD fire count) still need R.5 to
land and are intentionally NOT triggered by this change.

**Why.** D38 chose to defer the WRITE FLOW (event-shaped: append +
side effects) because the cross-component coordination is a real
service slice. But the practical consequence — D38 itself flags
this — is that "a jumper with a freshly-repacked rig who uses
the service layer alone sees 'next repack due: never' on a rig
with empty repack_history. To see the clocks, they set the
initial repack history via RigCreate at onboarding, or hand-edit
rig.xml afterwards."

For v0.1 that's an actively bad user experience: the alternative
to a 30-second form field is teaching the user how to open
rig.xml in a text editor, find the right element, write a date
in YYYY-MM-DD form, validate it against the XSD, and hope nothing
else breaks. Most users will give up and the repack clock stays
broken on their rig.

The narrower fix — accept a replacement list on PUT, no side
effects — gets the visible-clock case working today without
prejudicing R.5's design. R.5 can still ship its full append +
counter-update flow on a dedicated POST endpoint; this PUT path
remains the "I'm editing my logbook" surface (the same posture
D31 already takes for jump metadata edits). The two are
complementary, not in conflict.

**Consequences.**

- ``RigUpdate.repack_history: list[RepackEntry]`` added with
  default ``[]``. Existing callers that don't send the field get
  the pre-D66 preserve-from-disk behaviour because the service
  treats "empty list AND on-disk has entries" as "client didn't
  intend to clear" — see below.
- ``update_rig`` distinguishes "client supplied an empty list"
  from "client supplied the existing list":
  - Empty payload list + non-empty on-disk → preserve on-disk.
    (The most common case for clients that pre-date D66 — they
    just round-trip the rig without sending repack_history.)
  - Non-empty payload list → replace on-disk verbatim.
  - Empty payload list + empty on-disk → no-op.
  This sidesteps the legacy-client-wipes-history footgun while
  still letting D66-aware clients edit history.
- ``EditRigModal`` (frontend) renders a "LAST REPACK DATE" date
  picker. On save, if the date differs from the latest entry's
  date, the modal builds a new ``repack_history`` array
  (preserving older entries, replacing/appending the latest)
  and sends it. The "JURISDICTION" label is renamed to
  "SEALED UNDER" to match the rig-header subtitle copy.
- Cross-component side effects (reserve ``ride_count`` bump,
  AAD fire-count update, ``repack_count_derived`` projection)
  are unchanged — still R.5 territory. A user who edits the
  repack date here gets a correct repack clock immediately; the
  counter side-effects land when R.5 ships.
- D38's "Consequences" section is updated by superseding (not
  editing in place): the line "RigUpdate in R.1 does NOT allow
  modifying repack_history" is now narrowed by D66. The "you
  have to hand-edit rig.xml" workaround in D38 is no longer
  the only path.

**Re-evaluation triggers.** This decision flips when any of:

- R.5 lands its dedicated append endpoint. At that point, the
  rig edit modal can route the date through R.5's POST and stop
  using the PUT-replacement shortcut. The ``repack_history``
  field stays on ``RigUpdate`` as the "bulk edit / corrections"
  surface; R.5 owns the "I just got my rig repacked" event flow.
- Multi-user lands. The "I'm editing my own logbook" framing
  weakens when the editor isn't the jumper — the repack event
  is then an attestation by a rigger, and the structured event
  flow (R.5) is the right surface, not a PUT replace.

**Alternatives considered.**

- *(Wait for R.5 before letting users edit dates.)* The status
  quo per D38. Rejected because the wait has no concrete
  schedule and the alternative is teaching every user to edit
  XML.
- *(Add a dedicated PATCH /rigs/{id}/repack-history endpoint.)*
  More REST-pure but doubles the API surface for one field. The
  PUT-replace pattern matches the rest of the metadata edits in
  this codebase (jumps, dropzones, jumpers); consistency wins.
- *(Always use the payload value, even on an empty list.)*
  Simpler service code but introduces a footgun: a pre-D66
  client that PUTs a rig metadata change without sending
  ``repack_history`` would silently wipe the history. The
  empty-list-vs-on-disk disambiguation above costs three lines
  of service code and avoids the footgun entirely.

**References.**

- D31 — jump metadata edit via PUT is the "full replace"
  pattern this extends to rigs.
- D38 — original "R.5 territory" deferral; narrowed by D66.
- D58 — starred rig (similarly service-controlled, doesn't ride
  on RigUpdate).
- 2026-05-15 conversation: user observed that the rig edit
  modal had no way to set the repack date, only the wizard
  did at create time.
