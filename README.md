# Skydive Logbook

> A self-hosted skydiving logbook that respects your ownership of your data.

[![CI](https://img.shields.io/github/actions/workflow/status/Undefined6799/skydive-logbook/ci.yml?branch=main&label=CI)](https://github.com/Undefined6799/skydive-logbook/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%20|%203.12%20|%203.13-blue.svg)](pyproject.toml)
[![Status](https://img.shields.io/badge/status-beta-blue.svg)](DECISIONS.md)

<!-- Hero visual goes here once captured. ~5-10s of the Log Jump flow,
     <2 MB, ≤1200px wide, stored at docs/assets/hero.gif and referenced
     with a relative path. See `docs/internal/session-handoffs/` for
     capture notes. -->

<!--
![Skydive Logbook in action](docs/assets/hero.gif)
-->

Log jumps, upload FlySight files and video, track gear, query your
history. Ships as a native desktop app on macOS, Windows, and Linux.
Exposes a public REST API so other tools can build on top of it.

Your data stays on your machine — plain XML files you can read, back
up, or verify by hand. If this project ever disappears, your logbook
does not.

## Features

- **Per-jump XML you can read in any text editor**, validated against a
  versioned XSD that ships next to the data. Verifiable with the OS
  `xmllint` / `shasum` tools.
- **Rig manager** — main canopies, reserves, AADs, containers, full
  rig assemblies. Tracks repacks, reline events, wear counters, and
  jurisdictions per component.
- **People + dropzones as first-class entities**, so group jumps,
  packer assignments, and per-dropzone stats are queryable.
- **Public REST API** with OpenAPI 3.1 at `/openapi.json` and RFC 9457
  problem+json error envelopes. Third-party tools can build on it from
  day one.
- **Crash-safe writes** — every persisted byte goes through an atomic
  `tmp` + `fsync` + `replace` + parent-dir-fsync sequence (with
  `F_FULLFSYNC` on Darwin) so a crash never leaves a half-written
  jump.
- **SHA-256 manifest per jump folder**, `shasum -c` compatible.
  Catches silent corruption from cloud sync, bit rot, or hostile
  edits.
- **Rebuildable SQLite index** — if the DB is deleted, the
  reindex command walks the XML and rebuilds it. The data is never in
  one place.

## Status

**Public beta** (`v0.1.0-beta.1`), single maintainer. The on-disk
format is stable per D2 + D18; the desktop UX still has rough edges
that real-world use is the best way to find. See `CHANGELOG.md` for
what's in this release.

**Known caveats for this beta — please read before installing:**

- **Unsigned binaries.** macOS Gatekeeper will say *"this app cannot
  be opened because Apple cannot check it for malicious software"* —
  right-click → Open the first time to bypass. Windows SmartScreen
  will show *"Windows protected your PC"* — click *More info* →
  *Run anyway*. This is expected for a pre-revenue indie project;
  signing certificates are an ongoing recurring cost that's deferred
  until the project has a real user base. See D52 in `DECISIONS.md`
  for the full reasoning and the conditions under which the posture
  flips.
- **Loopback-only by default** (D48). The REST API binds to
  `127.0.0.1`; the app is single-user and has no authentication
  surface in v0.1.
- **Manual updates only.** A Settings → *Check for updates* button
  surfaces newer releases; download and install are still manual.
  Silent auto-update is deferred per D14.
- **Cloud-sync folders supported best-effort** (D49). The XML
  survives any sync conflict cleanly; the SQLite index may need a
  one-click *Reindex from XML* after switching machines.
- **FlySight parsing not implemented yet.** FlySight CSVs can be
  attached to a jump but are stored as opaque files until the parser
  lands in a later release.

Other deferred items per D14: digital signatures, multi-user,
import from other logbook apps, video thumbnails, mobile, headless
server mode.

## Principles

- **You own your data.** Human-readable folder names, XML validated
  against an XSD that ships next to it, SHA-256 manifest in every
  folder. Self-describing without the app.
- **The API is a contract.** REST + OpenAPI 3.1. Documented,
  versioned, and built for third-party consumers from day one.
- **Simple on purpose.** No Docker, no cloud dependency, no account.
  One app, one folder, one file per jump.

## Stack

| Layer             | Choice                                                    |
|-------------------|-----------------------------------------------------------|
| Language          | Python 3.11+ (D15)                                        |
| REST API          | FastAPI + OpenAPI 3.1 (D1)                                |
| Errors            | RFC 9457 problem+json (D16)                               |
| Disk format       | One XML file per jump, validated against XSD (D2)         |
| Integrity         | SHA-256 manifest per jump folder (D5, D10)                |
| Index             | SQLite, rebuildable from XML (D3, D26)                    |
| Frontend          | Vite 5 + React 18 + Tailwind CSS + Lucide icons           |
| Desktop shell     | pywebview, packaged per-platform (D11)                    |

See [`DECISIONS.md`](DECISIONS.md) for the reasoning behind each of
these choices.

## On-disk layout

```
<logbook_root>/
  README.md                           # human-readable overview of the folder
  SCHEMA.v1.xsd                       # schema for jump.xml (v1)
  settings.xml                        # per-logbook settings (units, jumper name)
  .logbook.lock                       # single-instance lock (D9)
  index.sqlite                        # rebuildable index (D3)
  .trash/                             # soft-deleted jumps (D19)
  dropzones/
    <uid>.xml                         # one file per dropzone record (D44)
  people/
    <uid>.xml                         # group members and packers (D54)
  jumpers/                            # jumper records + credentials (D47)
    <uid>/
      jumper.xml
      attachments/<uid>/<filename>    # licence scans, etc.
  inventory/                          # rig-manager components (D33, D34)
    mains/<uid>.xml                   # main canopies
    reserves/<uid>.xml                # reserve canopies
    aads/<uid>.xml                    # automatic activation devices
    containers/<uid>.xml              # containers
  rigs/                               # rig assemblies (D33, D37)
    <nickname>/
      rig.xml
  jumps/
    [851] First 4-way of the season/  # [<jump#>] <title> — human readable (D4)
      jump.xml                        # source of truth, contains UUID <id>
      SHA256SUMS                      # SHA-256 checksums (shasum -c compatible)
      flysight.csv                    # uploaded attachments
      video_01.mp4
```

Notes:

- Folder names follow `[<jump#>] <title>` per D4. When `<title>` is
  empty the folder is just `[<jump#>]`. Editing the title via the
  API renames the folder atomically; renaming via Finder/Explorer
  never rewrites `jump.xml`.
- `summary.md` is deferred per D5 — the canonical record is
  `jump.xml`.
- `<logbook_root>` defaults to `~/SkydiveLogbook/` and is configurable.
  App-level config (which logbook to open, window state) lives in the
  OS user config dir, not the logbook folder — see D20.

## Verifying your data without the app

```bash
# Validate a jump.xml against its declared schema version
xmllint --schema <logbook_root>/SCHEMA.v1.xsd \
        "<logbook_root>/jumps/[851] First 4-way of the season/jump.xml" --noout

# Verify file integrity for one jump folder
cd "<logbook_root>/jumps/[851] First 4-way of the season/"
shasum -c SHA256SUMS

# Verify every folder in the logbook (bundled CLI)
python -m backend.scripts.verify --logbook-root <logbook_root>
```

## Install and run

### Installing the v0.1.0-beta.1 binary

When prebuilt binaries are published, install per platform:

- **macOS** (`.dmg` or `.app`): Move the app to `/Applications`,
  then **right-click → Open** the first time. Gatekeeper will warn
  that the app is unsigned; choose *Open*. Subsequent launches work
  with a double-click.
- **Windows** (`.exe` or `.msi`): Run the installer. SmartScreen will
  show *"Windows protected your PC"* — click **More info** →
  **Run anyway**.
- **Linux** (`.AppImage`): `chmod +x` the AppImage, then run it.
  No gatekeeper.

If you'd rather check for updates yourself, the app has a button for
that under **Settings → Updates**.

### Building from source

You need:

- Python 3.11 or newer (3.11 / 3.12 / 3.13 all supported and CI-tested)
- [uv](https://github.com/astral-sh/uv) (`pip install uv`)
- Node.js 20+ (only for the desktop or dev modes — the REST-only mode
  doesn't need it)

The backend and the React frontend can run together as one packaged
desktop window, or separately for development.

### Desktop (one window — simplest)

Builds the frontend once, then opens a native window served by the
embedded backend.

```bash
git clone https://github.com/Undefined6799/skydive-logbook
cd skydive-logbook
uv sync --extra desktop
uv run skydive-logbook-desktop
```

What happens: the launcher checks `frontend/dist/`, runs `npm install`
+ `npm run build` if missing, starts uvicorn on a daemon thread, then
opens a pywebview window pointing at the embedded server. Closing the
window stops both processes.

The PyInstaller spec that wraps this into a per-platform `.app` /
`.exe` / AppImage lives at `skydive-logbook.spec`; per-platform
binaries are still being bake-tested (see `BUILD.md`).

### Dev mode (two terminals, hot reload)

When you're editing React source and want HMR:

```bash
# terminal 1 — backend with auto-reload, port 8765
uv sync --extra dev
uv run uvicorn backend.api.rest:app --reload --port 8765

# terminal 2 — Vite dev server, port 5173
cd frontend && npm install && npm run dev
```

Vite's dev server proxies `/api/*` to the backend on 8765, so the
frontend uses relative URLs in both modes. CORS for `localhost:5173`
is configured on the backend.

### REST-only (third-party developers)

```bash
uv run python -m backend.main      # REST on http://127.0.0.1:8765
                                   # OpenAPI: /openapi.json
                                   # Interactive docs: /docs
```

The bind port is configurable via `SKYDIVE_BIND_PORT=N` or
`bind_port` in your `config.toml` — see `config.toml.example`.

### Tests and lint

```bash
uv run ruff check backend
uv run pyright backend
uv run pytest backend/tests
(cd frontend && npm test)
```

All four must be green before claiming a change is done — see
[CONTRIBUTING.md](CONTRIBUTING.md) and `CLAUDE.md` §7. A GitHub
Actions workflow runs the same matrix on every push and PR across
Python 3.11 / 3.12 / 3.13 × Ubuntu / macOS / Windows.

## Architecture

The shape of the system at a glance:

```
   ┌──────────────────────────┐
   │  Desktop app (pywebview) │  React SPA, built by Vite
   │  React SPA (Vite build)  │
   └────────────┬─────────────┘
                │ HTTP (localhost)
                ▼
   ┌──────────────────────────┐
   │  REST adapter (FastAPI)  │  Thin; translates HTTP ↔ service calls
   │  backend/api/rest.py     │  RFC 9457 error envelope
   └────────────┬─────────────┘
                │ function calls
                ▼
   ┌──────────────────────────┐
   │    service layer         │  All business logic; sync Python functions
   │ backend/services/*       │
   └────────────┬─────────────┘
                │
   ┌────────────┼────────────┐
   ▼            ▼            ▼
┌────────┐ ┌────────┐  ┌────────┐
│XML+XSD │ │ SQLite │  │ files  │
│ (truth)│ │ (index)│  │uploads │
└────────┘ └────────┘  └────────┘
```

The full breakdown — module map, write hot path with crash semantics,
read path, integrity model, deferred items — lives in
[`ARCHITECTURE.md`](ARCHITECTURE.md). The reasoning behind every
load-bearing choice is in [`DECISIONS.md`](DECISIONS.md).

## Roadmap

- ✅ Backend: jumps + dropzones + people + rig manager + jumper
  credentials end-to-end with crash-safe writes, RFC 9457 errors,
  versioned XSD, hardened XML parser.
- ✅ Frontend: Jumps log + Log Jump modal wired to the backend; rig +
  inventory + dropzones views render against mock data pending
  remaining backend wiring.
- ⏳ Per-platform packaged binaries (`.app` / `.exe` / AppImage) — spec
  exists, bake-testing in progress (see `BUILD.md`).
- ⏳ Code signing posture (DRAFT D52 — ad-hoc/unsigned for v0.1, signed
  before GA).
- ⏳ Frontend modernization wave (TypeScript via `allowJs`, generated
  API client from OpenAPI, `react-hook-form`, `shadcn/ui`). See
  `reviews/2026-05-14-second-opinion.md`.

Issues with the `enhancement` label are the public roadmap.

## Contributing

PRs welcome. **Read [CONTRIBUTING.md](CONTRIBUTING.md) first** — it
explains the small-increments cadence, the `DECISIONS.md` discipline
that holds the project together, and what's out of scope for v0.1.

The single most important rule is: **before changing anything
cross-cutting, search `DECISIONS.md` for the relevant `D<N>` entry.**
If one exists, honour it; if not, draft one before coding. That
discipline is the reason a one-person project has been able to evolve
without losing track of why things are the way they are.

## Security

If you find a security vulnerability, **do not open a public issue**.
Use GitHub's private vulnerability reporting — see
[SECURITY.md](SECURITY.md) for the threat model, defences, and
reporting flow.

## License

[MIT](LICENSE). You owe nothing back; you give up nothing on the way
in.

## Code of conduct

Participation is governed by
[Contributor Covenant 3.0](CODE_OF_CONDUCT.md). Be kind.
