# Historical reviews — archive

Condensed summaries of pre-launch audits and design reviews. The original
files were removed during the 2026-05-15 doc cleanup; their findings either
shipped as code, landed as `D`-entries in `DECISIONS.md`, or were captured
into the May 14 pre-launch evidence pack which still lives under
`reviews/`.

This document is a pointer of last resort. For project rationale, read
`DECISIONS.md`. For the current state of the codebase, read the code.
For pre-launch action items, read the May 14 reviews under `reviews/`.

---

## Active (kept in tree)

- **`reviews/2026-05-14-tech-debt-audit.md`** — full repo inventory of
  remaining debt; complement to the second-opinion roadmap.
- **`reviews/2026-05-14-second-opinion.md`** — design-and-coherence
  pressure-test against 2025–2026 consensus; contains the nine-phase
  GitHub-ready roadmap and the `stats_service.compute_stats` perf bug.
- **`reviews/2026-05-14-pre-launch-execution.md`** — what shipped in the
  pre-launch session and the user-action checklist (badges, branch
  protection, hero GIF, Discussions toggle, ADR split).

---

## Archived 2026-04-23 — first deep audit pass

Eight reviews from a parallel four-axis pass that established the
project's baseline pre-frontend.

- **API contract & OpenAPI audit** — REST routes thin, RFC 9457 wired,
  one gap: jump routes did not declare error responses in the OpenAPI
  signature. Fixed in subsequent slices.
- **DECISIONS ↔ code drift audit** — verified D1–D31 against code on
  disk. Found D26 reindex code-lag (WARNING instead of refusing
  startup); landed.
- **Invariants & security audit** — confirmed atomic_write, XSD
  validation, hardened parser, SQLite-not-authoritative, path safety,
  RFC 9457, lockfile. One CRITICAL: missing catch-all exception handler
  on `rest.py`. Landed (`backend/api/rest.py:100–171`).
- **Test quality & crash-path audit** — 25 test files, 8 findings; the
  CRITICAL was D3 reindex untested (script was `NotImplementedError`
  at the time). All landed.
- **Multipart / attachment edge cases research** — flagged three
  `python-multipart` CVEs (CVE-2024-24762 ReDoS, CVE-2024-53981
  logging DoS, CVE-2026-40347 preamble DoS). Floor was raised; spool
  threshold documented.
- **Platform portability research** — `os.replace` is atomic but
  durability requires parent-dir fsync; macOS `fsync(2)` doesn't flush
  disk caches without `F_FULLFSYNC`; flock semantics on NFS/CIFS.
  Landed: `F_FULLFSYNC` on Darwin, parent-dir fsync after rename.
- **SQLite + FastAPI concurrency research** — current per-connection
  pattern is safe; documented WAL multi-reader/single-writer guarantees,
  `busy_timeout=0` gotcha, and cloud-sync corruption risk for
  `.wal`/`.shm`.
- **Forward-looking review** — second pass building on the four-axis
  reports, surfacing D24 drift, dependency-floor CVEs, and platform
  gaps. Findings folded into subsequent D-entries.

---

## Archived 2026-04-24 — Rig Manager integration analysis

Mapped the externally-authored rig-manager spec against the Phase 3.6
code. Recommended deferring to v0.2 with a thin `Equipment` model for
v0.1; **rejected** in the same-day design session because the thin
shape couldn't answer "can I jump this rig today?". The decision and
the R.0–R.5 phasing landed as D33–D39 in `DECISIONS.md`.

---

## Archived 2026-04-27 — Design critique of `ui-mockup.html`

Walked the 1271-line, 10-view HTML mockup through the design-critique
framework (WCAG 2.1, Nielsen heuristics, HIG/MD3). The key finding
was that Equipment / Equipment Edit / Rigs List / Rig Edit views
reflected the pre-D33 shape and were desynced from the freshly-landed
rig manager. The mockup was deleted in the 2026-05-15 cleanup —
the React frontend supersedes it.

---

## Archived 2026-04-28 — Three deep reviews

- **Deep review (cross-cutting)** — nine parallel review tracks
  (invariants, decisions drift, security, test coverage, tech debt,
  API surface, frontend UX, accessibility, UX copy) against D1–D45.
  Confirmed the previously-CRITICAL D16 catch-all is now wired. D44
  dropzones and D45 environment fields shipped end-to-end; D41–D43
  attachment-edit endpoints live.
- **Frontend WCAG 2.1 AA audit** — multiple failures noted: stripped
  `:focus-visible` ring, contrast on inactive nav items + AAD-mode
  toggle, missing `htmlFor`/`id` pairs, missing `aria-hidden` on
  decorative Lucide icons. Foundation present (semantic HTML, ARIA
  landmarks); detail work landed in subsequent slices.
- **Security posture audit** — re-verified XXE, path traversal,
  lockfile, SQLite, multipart, error envelope, Bearer auth. Strong
  posture; the CRITICAL was the `python-multipart` CVE floor (already
  flagged on 2026-04-23). Two advisories: Bearer scheme advertised but
  not enforced; macOS `fsync` durability.

---

## Archived 2026-04-29 — Pyright rollout (DEP-2)

Took `pyright backend` from 3760 errors (strict-everywhere) to **0
errors** under Option 2 mode: top-level `basic`, allow-list of strict
folders covering production code, per-folder overrides for
`backend/xml/` (lxml stub gaps) and `backend/tests/` (fixture-typing).
CI gates the same triple (ruff + pyright + pytest) on every push.
Decision pinned as **D51**.

---

## Archived 2026-04-30 — Finish open items from 2026-04-29 audit

Closed 12 of 13 OPEN items in one session. Final triple: ruff green,
pyright 0/0/0, pytest **1459 passing** (+41) with 1 Mac-only skip;
frontend 8 vitest passing + new CI job. Items: TEST-2/4/5/6/7/8 (crash
+ XSD + NFC + DOCTYPE-CDATA + SIGKILL + frontend smoke), CODE-4
(`sanitize_folder_name` UTF-8 cap), TEST-3 (`launch_desktop.py`
smoke), INFRA-5/6/7 (PyInstaller spec + first-run picker + signing
hooks + draft D52). INFRA-8 (`.venv*` cleanup) blocked by sandbox
permissions. CODE-1 (`open_index` per-call) profiled (~76μs/call) and
re-deferred — invisible at single-user interactive rates.

---

## Archived 2026-04-29 / 2026-04-30 — Tech-debt dashboards

Three HTML progress dashboards (`docs/reviews/2026-04-29-progress.html`,
`2026-04-29-tech-debt-audit.html`, `2026-04-30-progress.html`) tracked
in-flight tech-debt items. The audit findings are summarised in the
2026-04-30 entry above. Removed in the 2026-05-15 cleanup.

---

## Archived 2026-04-30 — Session handoff

EOD orientation note for the next agent picking up the backend after
the tech-debt closeout. Captured a snapshot of the pre-launch state:
1459 passing tests, 7 frontend tests, 0 lint/type errors, 52 D-entries
including draft D52. Superseded by the 2026-05-14 reviews (D52 has
since shipped non-draft; the test counts moved on).
