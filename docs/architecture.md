# Architecture

This doc explains **how** the code is organized. It is short on purpose —
the *why* lives in `DECISIONS.md`. If something here disagrees with the code,
the code is right and this doc is stale; please fix.

## Shape of the system

```
                             ┌──────────────────────────┐
                             │  Desktop app (pywebview) │
                             │  React SPA (Vite build)  │
                             └────────────┬─────────────┘
                                          │ HTTP (localhost)
                                          ▼
                             ┌──────────────────────────┐
            OpenAPI 3.1 ◄────│  REST adapter (FastAPI)  │
            (public spec)    │  backend/api/rest.py     │
                             └────────────┬─────────────┘
                                          │ function calls
                                          ▼
                             ┌──────────────────────────┐
                             │    service layer         │   ← all logic
                             │ backend/services/*       │
                             └────────────┬─────────────┘
                                          │
                         ┌────────────────┼────────────────┐
                         ▼                ▼                ▼
                  ┌───────────┐    ┌───────────┐    ┌───────────┐
                  │ XML + XSD │    │  SQLite   │    │ file I/O  │
                  │ (trust)   │    │  (index)  │    │ (uploads) │
                  └───────────┘    └───────────┘    └───────────┘
```

## Directory map

```
skydive-logbook/
├── .claude/agents/              # role-scoped dev agents
├── README.md
├── DECISIONS.md                 # canonical decision record
├── LICENSE                      # GPL-3.0
├── config.toml.example          # logbook_root, port, etc.
├── docs/
│   ├── architecture.md          # this file
│   ├── build.md                 # per-platform build commands
│   ├── decisions-draft.md       # in-flight D-entries
│   └── historical-reviews.md    # archive of pre-launch audits
├── backend/
│   ├── main.py                  # acquires lock, boots REST, (later) launches webview
│   ├── config.py                # loads config.toml into a Pydantic Settings object
│   ├── models/                  # Pydantic models — domain shapes (single source of truth)
│   │   ├── jump.py
│   │   ├── dropzone.py          # D44 dropzone record
│   │   ├── _component_base.py   # ComponentStatus + NotesLogEntry + ComponentBase (D34)
│   │   ├── main.py              # main canopy + nested Lineset (D33+D34)
│   │   ├── reserve.py           # reserve canopy + recert extensions (D33+D34)
│   │   ├── aad.py               # automatic activation device (D33+D34)
│   │   ├── container.py         # container (D33+D34)
│   │   └── common.py            # shared constants (namespace, generator, regexes)
│   ├── xml/
│   │   ├── schema/              # XSD source copies; written to logbook root on first-run
│   │   │   ├── SCHEMA.v1.xsd    # jump + dropzone + four rig-manager component elements; additive growth per D18
│   │   │   └── SCHEMA.v2.xsd    # future — coexists with v1, never replaces it
│   │   ├── serialize.py         # Pydantic ↔ XML
│   │   └── validator.py         # XSD validation (defensive XML parsing, XXE off)
│   ├── storage/
│   │   ├── filesystem.py        # folder layout, path safety, atomic writes, NFC normalize
│   │   ├── manifest.py          # SHA-256 SHA256SUMS generation + verification
│   │   ├── lockfile.py          # single-instance lock (.logbook.lock) via filelock
│   │   ├── trash.py             # soft delete to <logbook_root>/.trash/
│   │   └── index.py             # SQLite index + reindex command
│   ├── services/                # all business logic (takes user_id from day one)
│   │   ├── jump_service.py
│   │   ├── dropzone_service.py
│   │   ├── reindex_service.py
│   │   ├── file_service.py
│   │   ├── stats_service.py
│   │   ├── main_service.py      # create + get for main; R.1 adds list/update/delete (D33)
│   │   ├── reserve_service.py   # create + get for reserve
│   │   ├── aad_service.py       # create + get for aad
│   │   └── container_service.py # create + get for container
│   ├── api/
│   │   ├── rest.py              # FastAPI adapter, thin
│   │   ├── openapi.py           # OpenAPI spec augmentations
│   │   └── errors.py            # error → HTTP status mapping
│   ├── scripts/
│   │   ├── reindex.py           # rebuild SQLite from XML
│   │   └── verify.py            # check all manifests against disk
│   └── tests/
└── frontend/
    ├── package.json             # React 18, Vite 5, Tailwind 3, lucide-react
    ├── vite.config.js           # proxies /api/* to the backend in dev
    ├── tailwind.config.js
    ├── postcss.config.js
    ├── index.html
    └── src/
        ├── main.jsx             # React entry
        ├── App.jsx              # sidebar shell + active-view dispatch
        ├── index.css            # Tailwind base + scrollbar polish + dark page bg
        ├── api.js               # fetch wrappers + RFC 9457 ApiError class (D16, D27)
        ├── units.js             # feet ↔ meters conversion at UI boundary (D12)
        ├── primitives.jsx       # StatusDot, ClockPill, StatCard, Card, etc.
        ├── Sidebar.jsx          # Jumps / My rig / Inventory / Dropzones + Settings cog
        ├── mock.js              # mock data for views not yet wired to backend
        ├── lineTypes.js         # canopy lineset reference data (D34, D45)
        ├── rigShape.js          # SVG rig diagram geometry
        ├── views/               # Profile, Jumps, MyRig, Inventory, Dropzones, Settings, CareerStats
        └── modals/              # LogJump, JumpDetail, Component, AddRig, AddComponent, etc.
```

The frontend is plain JavaScript today — no TypeScript layer, no codegen
from OpenAPI yet. Forms are hand-rolled inside the modal components rather
than using a library. Server state is local component state plus
`api.js`-level fetch wrappers; see `frontend/README.md` for what's wired
to the backend versus what still reads from `mock.js`.

## How a jump is written (the hot path)

1. SPA posts `multipart/form-data` to `POST /api/v1/jumps` (D30) — a JSON
   `jump` field plus zero or more file `files` parts.
2. REST adapter validates the JSON against the Pydantic `JumpCreate` model
   and constructs an `Upload` per file part.
3. `jump_service.create_jump(logbook_root, user_id, payload, uploads)`
   does, in this strict order (D25):
   a. Pre-flight: validate every upload filename via D4 sanitization,
      reject duplicates within the request — before any disk write.
   b. Compute folder name `[<jump#>] <title>` (D4; title optional,
      empty → bare `[<jump#>]`), normalize to Unicode NFC, reject
      forbidden characters and Windows reserved names.
   c. D23 scan: refuse if `jumps/[<jump#>]` or `jumps/[<jump#>] *`
      already exists (409 jump_number_conflict).
   d. `mkdir(exist_ok=False)` — kernel-level backstop on the same
      collision class.
   e. For each upload, stream-hash-write to `<folder>/<filename>` via
      `atomic_write_stream`; the SHA-256 returned is what lands in
      `<attachment>/<sha256>` so claim and bytes agree by construction.
   f. Build the canonical `Jump` model, serialize → XML via
      `xml/serialize.py`, validate against `SCHEMA.v1.xsd`.
   g. `atomic_write` `jump.xml`: `<path>.tmp` → `fsync` → `os.replace`.
   h. `atomic_write` `SHA256SUMS` from the just-written `jump.xml` (one
      manifest line per attachment plus `jump.xml`).
   i. Insert a row into the SQLite index. A `UNIQUE(user_id, jump_number)`
      collision raises 409 (the index-layer backstop on D23).
4. Adapter returns the persisted model as JSON, with `Location` set to
   the new resource URL.

D25 crash semantics: the service does **not** auto-cleanup on failure
mid-write. A half-built folder (mkdir done, some attachments on disk,
no `jump.xml`) is the documented "incomplete folder" crash state —
`verify` flags it; `reindex` skips it; the user can resolve manually.
Auto-rmtree would mask the underlying error.

`summary.md` is documented in D5 but write is deferred in v0.1 — the
canonical record is `jump.xml`. The future write step lands on a
post-v0.1 slice or supersedes D5.

## How a jump is read

1. SPA hits `GET /api/v1/jumps/{uuid}`.
2. Service reads the SQLite row to find the folder path.
3. `xml/serialize.py` parses `jump.xml` (XXE disabled, DTDs disabled,
   size capped), reads the declared XML namespace, and picks the matching
   `SCHEMA.vN.xsd` for validation.
4. Returned as Pydantic model → JSON with the structured-error envelope
   from D16 if anything fails.

If the XML fails XSD validation, the request returns HTTP 500 with a
structured error; the row is flagged for manual inspection but the file is
not modified.

## Concurrency and sync

- **Single-instance lock** (`.logbook.lock`) is acquired via the `filelock`
  library (fcntl on POSIX, msvcrt on Windows) and prevents two app
  instances from writing to the same folder at once, per-machine.
- **Atomic writes** (write-tmp, fsync, `os.replace`) mean partial writes
  never leave the folder in a broken state.
- **Manifest** (`SHA256SUMS`) detects corruption or partial cloud syncs
  and is verified by `python -m backend.scripts.verify`.
- **Cross-machine sync** (Dropbox, iCloud, Syncthing) is supported best-
  effort per D49: the XML data is safe regardless, the SQLite index may
  need rebuilding via the D26 reindex flow on first open from a synced
  copy. Users must not run the app on two machines simultaneously
  (D9's lock is per-machine). The first-run UX that surfaces this
  warning is itself deferred (audit INFRA-6); the position is
  documented in D49 and in this paragraph until that UX lands.
- **Soft delete.** Jump folders move to `<logbook_root>/.trash/` instead of
  being removed; see D19. The trash is ignored by the index but included
  in `verify`.

## API contract and versioning

- **REST public API**: versioned in the URL (`/api/v1/...`). Breaking changes
  require `/api/v2/...` and a parallel OpenAPI document.
- **OpenAPI 3.1** spec at `/openapi.json` (unversioned URL so third-party
  tooling can discover the current spec without hard-coding a version;
  when v2 ships, `/openapi.json` serves v2's document while `/api/v1/...`
  routes remain mounted for old clients). Interactive docs at `/docs`.
- **XML schema**: `SCHEMA.v1.xsd`. Every XML file declares its schema version
  via the XML namespace; writing v2 creates `SCHEMA.v2.xsd` in the logbook
  root next to v1, and v1 files keep reading forever (D18).
- **Error shape**: structured `error.code` / `error.message` / `request_id`
  envelope (D16) is part of the contract; new codes are additive.
- Additive changes (new optional fields, new endpoints) are allowed within a
  major version.

The `api-contract-steward` agent enforces this; see its agent file.

## Integrity model

| Layer       | Mechanism                              | Catches                          |
|-------------|----------------------------------------|----------------------------------|
| Schema      | XSD validation on read/write           | Shape violations                 |
| File        | SHA-256 per file in `SHA256SUMS`       | Corruption, truncation, bad sync |
| Write       | Atomic rename (`os.replace`)           | Partial writes from crashes      |
| Future      | `<signature>` element reserved         | Tampering (not enforced yet)     |

`summary.md` is excluded from `SHA256SUMS` — it is a derived artifact
(see D5) and regenerable from `jump.xml`, so including it would produce
false-positive integrity failures whenever we change the summary template.

## Deferred (documented in DECISIONS.md)

FlySight parsing, digital signing enforcement, multi-user accounts, import
from other logbook apps, video thumbnails, and a mobile app are explicitly
out of scope for v0.1.
