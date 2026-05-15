# Changelog

All notable changes to this project. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions
follow [PEP 440](https://peps.python.org/pep-0440/) (Python) and
[SemVer](https://semver.org/) (frontend). The two stay in lockstep
on the major / minor / patch number.

## [Unreleased]

Post-`0.1.0-beta.1` audit / hardening cohort. Driven from
``reviews/2026-05-15-code-debt-deep-audit.md`` and its three
addenda; tracked as "Wave A" in
``reviews/2026-05-15-slice-plan.md``.

### Added — backend
- **D67**: ``Settings.expose_internal_errors: bool | None``
  controls whether ``application/problem+json`` 500 bodies
  surface ``f"{type(exc).__name__}: {exc}"`` as ``detail``.
  ``None`` (default) auto-resolves to ``True`` on loopback
  bind, ``False`` otherwise. Env override:
  ``SKYDIVE_EXPOSE_INTERNAL_ERRORS``.
- **D68**: ``IndexSchemaTooNewError``. ``open_index`` now
  refuses to start when ``PRAGMA user_version`` is greater
  than the build's ``INDEX_SCHEMA_VERSION``; ``main.py``
  exits 1 with a message naming both versions and the
  index path. Supersedes the pre-D68 "drop in either
  direction" branch.
- **API contract**: every ``/api/v1/*`` route now declares
  an explicit ``operation_id`` (stable name for SDK
  codegen) and a ``responses=`` set referencing the
  reusable RFC 9457 error envelope components in the
  OpenAPI spec. 68 routes touched across 10 routers. Seven
  snapshot tests pin coverage.
- ``Settings.bind_host`` produces a loud WARNING at boot
  when set to anything other than loopback, with a pointer
  to ``SECURITY.md`` and D48.

### Changed — backend
- Three function-local imports in
  ``backend/services/jump_service.py``
  (``ValidationError``, ``hashlib``, ``mimetypes``) moved
  to module top.
- ``backend/api/jumps.py:list_jump_files_route`` returns
  ``FolderFileResponse(**dataclasses.asdict(f))`` instead
  of ``**f.__dict__`` — idiomatic for the frozen
  dataclass.

### Changed — frontend
- ``App.jsx`` ``handleResume`` now lands the user on
  ``dashboard`` after dismissing the wizard; the prior
  ``'profile'`` matched no key in ``VIEWS`` and only
  worked through the ``|| Dashboard`` fallback at the
  cost of leaving the sidebar with no highlighted tab.
- ``Settings.jsx`` ``TrashSection`` no longer claims
  ``"2 deleted jumps · 1 retired component"`` via
  hardcoded literals — the trash listing endpoint isn't
  implemented end-to-end, so the section now renders an
  honest "slated for v0.2" placeholder.
- ``frontend/README.md`` updated to reflect the actual
  wired-status of every view + modal (the pre-Wave-A line
  "Only Jumps Log is wired" was the root cause of a
  third-party review's incorrect headline).

### Removed
- ``frontend/src/modals/ComponentModal.jsx`` — orphan dead
  code (322 LOC), never imported. Only consumer of
  ``frontend/src/mock.js``.
- ``frontend/src/mock.js`` — prototype mock data (351
  LOC); no surviving importer.
- ``backend/services/file_service.py`` — 6-line scaffold
  stub. Attachment logic lives in
  ``backend/services/jump_service.py``.

### Documentation
- ``DECISIONS.md``: D67 (exception redaction policy) +
  D68 (newer-on-disk refusal).
- New audit / verification documents under ``reviews/``:
  ``2026-05-15-code-debt-deep-audit.md``,
  ``2026-05-15-chatgpt-findings-deep-dive.md``,
  ``2026-05-15-chatgpt-technical-sweep-verification.md``,
  ``2026-05-15-slice-plan.md``.

## [0.1.0-beta.1] — initial public beta

First public release. This is a beta — the on-disk format is stable
(D2 + D18) but the desktop UX still has rough edges.

### Licensing
- **Project license is now GPL-3.0** (was MIT in pre-public commits;
  see D63 in DECISIONS.md). Forks must remain open-source under
  GPL-3.0; end-user installation, modification, and use are
  unchanged.

Self-hosted,
single-user, loopback-only (D48); your data stays on your machine.

### Added — backend
- Jumps: create / list / get / update / delete with attachment uploads
  (D14 §1, §2; D30, D31).
- Rig manager (D33): main canopy, reserve, AAD, container as
  first-class entities; rig composition; per-jump rig snapshot in the
  jump folder (D36); component assignment and swap rules (D37);
  repack-event schema seam (D38, write-flow deferred to v0.2).
- Dropzones as first-class entities (D44) with starred-DZ default
  for jump-form prefill (D60).
- People entity for group members and packers (D54).
- Jumper credentials: federation memberships, CoPs, ratings,
  manufacturer tandem ratings, government medicals; tandem-currency
  calculator with per-manufacturer rules (D47).
- Stats endpoint with D14 §4 aggregations (`/api/v1/stats`).
- Verify + reindex operations endpoints (`/api/v1/verify`,
  `/api/v1/reindex`).
- `/api/v1/updates/check` — user-initiated GitHub Releases lookup
  for "is there a newer version?" Settings → *Check for updates*
  button surfaces it; no automatic download (D14 still defers
  silent auto-update).
- RFC 9457 problem+json error envelope on every failure (D16) with
  request-id correlation to log records (D27).
- Atomic-write discipline (D10) plus single-instance lockfile (D9)
  and intra-process writer lock (D50).
- Soft-delete to `.trash/` with timestamped folders (D19).

### Added — data integrity surface
- Schema drift detector: CI test fails if a Pydantic model field has
  no matching reference in the XML serializer or no declaration in
  the XSD (closes the D2 three-place invariant gap).
- Dangling-reference detection in `verify`: surfaces jumps whose
  `rig_id` / `dropzone_id` / `packed_by` / `group_members` reference
  deleted (trashed) or never-existing entities.

### Added — frontend
- React SPA with Settings, Jumps, Profile, Inventory, My Rig,
  Dropzones, Career Stats views.
- pywebview-packaged native shell on macOS / Windows / Linux
  (D11) launched by `backend.scripts.launch_desktop`.

### Known caveats
- **Unsigned binaries.** macOS Gatekeeper will warn that the app is
  unsigned; right-click → Open the first time to bypass. Windows
  SmartScreen will show "Windows protected your PC"; click *More
  info* → *Run anyway*. Linux AppImages have no gatekeeper. See
  the README for full install instructions.
- **Automatic in-app update is not shipped.** The Settings
  *Check for updates* button surfaces a new release if available,
  but installation is manual (download from the release page,
  replace the existing app).
- **Loopback-only.** The REST API binds to `127.0.0.1` by default
  (D48). Exposing the server to a LAN requires editing
  `config.toml`; there is no authentication surface in v0.1, so
  treat LAN exposure as opt-in development convenience only.
- **Cloud-sync folders supported best-effort** (D49). The XML data
  is safe under cloud-sync replication (each write is atomic and
  manifest-verified), but the SQLite index may need rebuilding via
  *Reindex from XML* after a sync conflict. The button is in
  Settings → *Verify & Reindex*.
- **No FlySight CSV parsing yet** (D14 deferred). FlySight files
  can be attached to a jump but are stored as opaque blobs.
- **`update_check_repo` is empty by default.** Set
  `update_check_repo = "owner/repo"` in `config.toml` to enable
  the Settings *Check for updates* button against your fork.
  Unset, the button is hidden.

### For developers
- 1690 backend tests, 63 frontend tests; full triple gate
  (`pytest`, `ruff check`, `pyright backend`) green on Python
  3.11 / 3.12 / 3.13 across Linux / macOS / Windows runners.
- See `DECISIONS.md` for the full rationale behind every load-
  bearing choice; `docs/decisions-draft.md` for in-flight decisions.
