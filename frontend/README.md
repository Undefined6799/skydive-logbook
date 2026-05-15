# Skydive Logbook — frontend

Vite + React + Tailwind + Lucide. v0.1 prototype.

Every view and modal is wired to the FastAPI backend — Dashboard,
Jumps Log, My Rig, Inventory, Dropzones, Identity, Onboarding, and
Settings (Identity / Verify / Reindex / Updates). The Settings →
Trash section is the one remaining stub: listing and restoring
trashed items is not yet implemented end-to-end (no backend
``GET /api/v1/trash`` route, no UI fetch). Slated for v0.2.

## Run as one app — single command

This is the way most users will run it. Builds the frontend once,
then opens a native window.

```bash
cd "/path/to/skydive-logbook"
uv pip install -e ".[desktop]"
skydive-logbook-desktop
```

What happens:
1. The launcher checks `frontend/dist/`. If missing, it runs
   `npm install` (~80 MB, one-time, ~60 s) and `npm run build`
   (~20 s).
2. Uvicorn starts on a daemon thread serving the API at
   `http://localhost:8000/api/v1/*` AND the built React app at
   `http://localhost:8000/`.
3. A pywebview window opens at 1280×820 pointing at
   `http://localhost:8000/`.

Closing the window stops both the backend thread and the WebView.
One process, one URL, one window.

You'll need Node.js installed (https://nodejs.org, LTS is fine) for
the first run's build step. Subsequent runs reuse `dist/` — no Node
required at runtime.

## Run for development (HMR)

When you're editing React source and want hot-reload, the two-terminal
flow is faster — Vite rebuilds and pushes to the browser on save.

**Terminal 1 — backend:**
```bash
cd "/path/to/skydive-logbook"
python -m uvicorn backend.api.rest:app --reload
```

**Terminal 2 — frontend dev server:**
```bash
cd "/path/to/skydive-logbook/frontend"
npm install
npm run dev
```

Open http://localhost:5173. CORS is configured on the backend to allow
that origin. Edits to `src/*.jsx` hot-reload in the browser.

You can also run the desktop launcher pointing at Vite if you want a
pywebview window with HMR — this requires both terminals plus the
launcher, and isn't the common path. Skipped here.

## Backend env knobs

`VITE_API_BASE` in `frontend/.env.local` overrides the API base URL
(defaults to `http://localhost:8000`). Useful when you're running the
backend on a different port or testing against a deployed API.

```bash
echo "VITE_API_BASE=http://localhost:8001" > frontend/.env.local
```

## What's here

```
frontend/
├── package.json              dependencies (React 18, Vite 5, Tailwind 3, Lucide)
├── vite.config.js
├── tailwind.config.js
├── postcss.config.js
├── index.html                loads Archivo + JetBrains Mono from Google Fonts
└── src/
    ├── main.jsx              React entry
    ├── index.css             Tailwind base + scrollbar polish + dark page bg
    ├── App.jsx               sidebar + active-view shell
    ├── api.js                fetch wrappers + RFC 9457 ApiError class
    ├── mock.js               mock data for the views not yet wired
    ├── primitives.jsx        StatusDot, ClockPill, StatCard, ProgressRow, Card, etc.
    ├── Sidebar.jsx           Jumps / My rig / Inventory / Dropzones + Settings cog
    ├── views/
    │   ├── Jumps.jsx         Log (wired to backend) + Stats (mocked)
    │   ├── MyRig.jsx         carousel + dashboard (mocked)
    │   ├── Inventory.jsx     filter pills + flat table (mocked)
    │   ├── Dropzones.jsx     featured home card + 2-up grid (mocked)
    │   └── Settings.jsx      Profile / Logbook / Units / Verify / Trash / Diagnostics / About (mocked)
    └── modals/
        ├── ComponentModal.jsx   click any component on My rig (mocked)
        └── AddRigModal.jsx      click + Add rig in the carousel (mocked)
```

## What's wired and what isn't

**Wired to the real backend (Jumps Log view):**
- `GET /api/v1/jumps` — populates the row list. Backend returns
  `JumpSummary` (id, jump_number, title, date, dropzone), so rows
  show only those fields. Aircraft / discipline / FF time / attachments
  are NOT in the summary today; they'd require either fetching each
  full jump (N+1 round-trips) or extending `JumpSummary` on the backend
  with a few denormalized columns in the SQLite index.
- Loading skeleton renders while the request is in flight.
- Empty state renders when the backend returns `[]`.
- RFC 9457 problem+json errors render in a banner with `type:`,
  `status:`, and `X-Request-Id` for support tickets. A network failure
  (uvicorn not running) shows a friendlier message.
- A Retry button re-runs the request.

**Mocked (everything else):**
- Jumps Stats sub-tab — career counters and breakdowns from `mock.js`.
- My rig — three rigs in `mock.js`. Carousel, status dots, clock pills,
  component cards, modals all work but don't persist.
- Inventory — flat list combining all rigs' components plus
  `unassignedComponents` and `retiredComponents` from `mock.js`.
- Dropzones — featured home card + grid from `mock.js`.
- Settings — Profile / Units / Verify / Trash / Diagnostics / About
  all decorative.
- Component-detail modal, Add-rig modal — mock-only.

**Not yet drawn at all:**
- Jump-detail modal (clicking a row in Jumps Log does nothing).
- Trash view (Settings has the button; click is a no-op).
- New-jump form (Log jump button is decorative).

## Decisions referenced

- **D11** — pywebview frontend, React + Vite, per-platform bundlers.
  This launcher is the development shape of D11; the per-platform
  bundlers (py2app on macOS, PyInstaller on Windows + Linux) wrap
  the same script + the pre-built `dist/` into a single binary.
- **D14** — v0.1 scope.
- **D16** — RFC 9457 problem+json error envelope (consumed by `ApiError` in `api.js`).
- **D27** — `X-Request-Id` correlation header (surfaced by `api.js`).
- **D31** — `update_jump` is metadata-only in v0.1.
- **D33–D39** — Rig Manager module shape (informs the mocked views).
- **D40** — Rig component fields are jumper-editable in v0.1; rigger-only enforcement deferred.

## Known follow-ups

- **Extend `JumpSummary`** with `aircraft`, `discipline`,
  `freefall_time_s`, `attachments_count` so the Log view rows can show
  the same density as the mockup widget. Requires bumping the SQLite
  index schema (D26 drop-and-reindex pattern) and updating reindex.
- **New jump form** — POST multipart wired through `api.createJump`.
- **Jump detail modal** — `getJump(id)` then render full Jump.
- **SPA fallback for hard-refresh on nested routes** — when React Router
  is added, the FastAPI static mount (`html=True`) serves index.html
  for paths like `/` but returns 404 for `/jumps`. A small custom
  catch-all route returning index.html for non-API non-asset paths
  closes that gap. Not needed today since the app uses in-memory state
  for navigation.
- **Backend services for rig manager** — D33's R.0+ phases.
- **Tests** — Vitest with one render-and-import smoke test per view
  to catch import drift.
