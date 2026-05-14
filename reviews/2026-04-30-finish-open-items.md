# Finishing the open items from the 2026-04-29 tech-debt audit

**Date:** 2026-04-30
**Scope:** Every OPEN non-decision item from `reviews/2026-04-29-progress.html`. 13 eligible items in the brief; 12 closed in this session, 1 blocked by sandbox-permission limits and documented for the user to complete locally.

## Final state

- `uv run ruff check backend` — All checks passed.
- `uv run pyright backend` — 0 errors, 0 warnings, 0 informations.
- `uv run pytest backend/tests` — **1459 passed**, 1 skipped (Darwin-only F_FULLFSYNC). Baseline was 1418; +41 new tests across this session.
- `npm test` (frontend) — 8 passed, 5 skipped (the skipped 5 are diagnostic stubs that the sandbox couldn't delete; they're explicitly inert).
- `.github/workflows/ci.yml` — added a `frontend` job (Node 20, ubuntu-latest) running `npm ci` + `npm test` against the new vitest suite. The existing `test` matrix (ruff + pyright + pytest, 3 OS × 3 Python) is unchanged.

## What shipped

| ID       | Item                                                                 | Files                                                                                         | Tests added |
|----------|----------------------------------------------------------------------|-----------------------------------------------------------------------------------------------|-------------|
| TEST-4   | XSD-rejection write-path pinning                                     | `backend/tests/test_create_jump.py`                                                           | 2           |
| TEST-5   | NFC round-trip pinning (D4)                                          | `backend/tests/test_create_jump.py`                                                           | 1           |
| TEST-6   | DOCTYPE-CDATA + case-insensitive byte-scan pinning                   | `backend/tests/test_validator.py`                                                             | 2           |
| TEST-2   | D44 dropzone crash-path tests (create / update / delete)             | `backend/tests/_crash_child.py`, `backend/tests/test_dropzone_crash_recovery.py` (new)         | 8           |
| TEST-7   | SIGKILL-leaves-recoverable-lockfile-state                            | `backend/tests/test_main_sigkill_lock_release.py` (new)                                        | 1           |
| TEST-8   | Frontend vitest smoke + CI job                                       | `frontend/{vite,vitest}.config.js`, `frontend/test/{setup,views.smoke}.{js,jsx}` (new), `.github/workflows/ci.yml` | 7           |
| CODE-4   | `sanitize_folder_name` UTF-8 byte-length cap                         | `backend/storage/filesystem.py`, `backend/tests/test_filesystem.py`                           | 4           |
| TEST-3   | Smoke tests for `launch_desktop.py` (605-line cross-platform surface) | `backend/tests/test_launch_desktop.py` (new)                                                  | 19 (incl. INFRA-6) |
| INFRA-6  | First-run folder picker pinning                                      | (rolled into TEST-3 file)                                                                     | (4 of the 19) |
| INFRA-5  | PyInstaller spec at project root                                     | `skydive-logbook.spec` (new), `BUILD.md` (new)                                                | spec validates Python-syntax and module-resolves |
| INFRA-7  | Placeholder icon + version metadata + signing hooks + draft D52      | `build/icons/skydive-logbook.svg` (new), `build/icons/README.md` (new), `BUILD.md`, `DECISIONS.md` (D52 draft) | (no tests; deployment metadata) |
| INFRA-8  | Three `.venv` folders cleanup                                        | **HALTED** — sandbox cannot delete the venvs; documented in HANDOFF.md.                       | n/a         |

Total **+41** backend tests, **+7** frontend tests, **1** new D-entry draft (D52), **2** new top-level files (`skydive-logbook.spec`, `BUILD.md`), **1** new placeholder icon SVG, **1** new CI job.

CODE-1 (open_index per-call refactor) — **profiled, then re-deferred**. The audit's gating condition was "Defer until profiling shows it." The profile (1000 iterations, single-process, warm cache):

- `open_index(root)` open + `SELECT 1` + close: **~76μs / call**
- Same `SELECT 1` on a held connection: **~0.5μs / call**
- 100 service requests at 3 opens/request (worst-case `create_jump` pattern): **22ms total** (~220μs/req)

For a single-user desktop app at any plausible interactive rate, the cost is invisible. The current per-call pattern is also simpler than a FastAPI-dependency-injected connection lifecycle would be. CODE-1 stays open in the dashboard but the profile result is pinned here so it doesn't get re-litigated; it should not be picked up unless a real workload (a several-thousand-row reindex, a future async background job) demonstrates a cost.

## Surprises caught

These are the things that weren't in the audit but that the work surfaced.

### Pre-existing pyright regression in `launch_desktop.py`

The 2026-04-29 baseline claimed 0 pyright errors, but pyright 1.1.409 + pywebview 6.2.1's stubs flag `webview.FOLDER_DIALOG` as `module_property` (a Proxy class) where the `create_file_dialog`'s `dialog_type:int` expects `int`. The error fires on `webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG, ...)` because the typed receiver checks the arg; the second call site at line 391 uses `window` from a `start()` callback (Unknown receiver) and so escapes the check.

This is upstream stub drift, not a real bug. Fix: a targeted `# pyright: ignore[reportArgumentType]` with the reason inline. **Deferring this would have left the green baseline broken.**

### Audit's TEST-4 framing was imprecise

Audit said: "assert ValidationFailedError and that the jump folder was NOT created." The actual code raises `XSDValidationError` (not `ValidationFailedError`) on XSD failure, and per D25 step 1 the folder IS mkdir'd before serialise+validate. The test is now correct: asserts `XSDValidationError`, asserts no `jump.xml` and no `SHA256SUMS` and no SQLite row landed. The folder existing post-failure is in fact the D25 "incomplete folder" crash state that `folder_reconcile` and `verify` already handle. Documented inline in the test class docstring.

### `vi.mock` Proxy hangs vitest's `await import`

A naive `vi.mock('../src/api', () => new Proxy({...}, get: () => noopPromise))` hangs the await chain because `await import(...)` queries `.then` on the resolved module to detect thenables — the Proxy's catch-all returned a never-resolving promise, so the await never returned. Switched approach: don't mock the api module at all, stub `globalThis.fetch` with a never-resolving promise, let the views' useEffect-driven fetches stay in their loading branch. Simpler, avoids the 50+ named-export inventory the Proxy was designed to dodge, and produces the same smoke surface.

### `.venv` directories are sandbox-readonly

The sandboxed shell cannot remove `.venv-sandbox/` and `.venv-test/` ("Operation not permitted"). The user has to delete them locally — they are already in `.gitignore` so leaving them is harmless to source control. **HANDOFF.md notes the canonical venv name is `.venv` and asks the user to `rm -rf .venv-sandbox .venv-test` once.**

### One quiet documentation surface

INFRA-6's intent ("first-run folder picker") was already implemented in `launch_desktop.py:_setup_first_run` before this session — it just had no tests. The 4 tests added in `TestFirstRunFolderPicker` pin the existing behaviour (existing-config-skips-picker, picker-accept, picker-cancel-falls-back-to-default, FOLDER_DIALOG marker). The audit's dashboard had it open because no tests existed; that's now closed.

## Halt-and-document items

Per the brief's halt conditions §1 and §4:

### Per-platform binary verification (INFRA-5 / INFRA-7)

The PyInstaller spec validates Python-syntax and tomllib-loads the project version, but I cannot produce the binary artefacts — that requires real macOS, Windows, and Linux build machines. Specifically:

- **macOS `.app`** — untested. First real build on Apple Silicon and Intel will surface any missing `hiddenimports` the static scan didn't catch. The Info.plist privacy-usage strings (NSDesktopFolderUsageDescription etc.) are required for Mojave+ NSOpenPanel calls, written to spec, untested.
- **Windows `.exe`** — untested. WebView2 (Edge Chromium) is bundled into Windows 11 by default, NOT into Windows 10 LTSC; the installer needs to check and prompt for the Evergreen runtime if missing. Documented in BUILD.md; not bake-tested.
- **Linux ELF / AppImage** — untested. AppImage staging steps are documented in BUILD.md; first real build will surface any missing system libraries that PyInstaller's bundling missed.

GitHub Actions Linux runners CAN build a Linux PyInstaller bundle as a smoke step. We did not add this to CI in this session — it would consume meaningful CI minutes and the spec is a placeholder for v0.1 anyway. **Decision: defer the CI-side build smoke until INFRA-5 has been verified on a real Linux machine first.** That's a follow-up.

### Code-signing certificates (INFRA-7)

D52 is drafted as DRAFT in DECISIONS.md and pinned to "ad-hoc / unsigned for v0.1, signed before GA." The certificates are per-developer per-platform per-paid-cert; none of them are in this repo. The PyInstaller spec exposes the hooks as env-var-driven; BUILD.md documents the per-platform sign + notarize commands.

### `.venv` cleanup (INFRA-8)

Sandbox permission. User must run `rm -rf .venv-sandbox .venv-test` locally. HANDOFF.md says so.

## Deviations from the plan

- The audit's TEST-4 wording was imprecise (XSDValidationError vs. ValidationFailedError, folder-created vs. folder-NOT-created) — followed the code's actual contract per CLAUDE.md §2. Documented inline.
- INFRA-6 was already implemented; tests added rather than re-implementing the flow. Bundled the 4 first-run tests into the same TEST-3 file (`test_launch_desktop.py`) so the launcher's tests live in one place.
- Did NOT touch the `# type: ignore` comments at lines 229 and 535 of `launch_desktop.py` — they're outside the scope of any audit item and the brief restricted edits to listed items.
- TEST-8's mock approach pivoted from "module Proxy" to "global fetch stub" once the Proxy hang was diagnosed. Simpler and more robust.
- 6 diagnostic test files in `frontend/test/` (sanity, profile, lucide, api-import, careerstats-import, import-only) could not be deleted from the sandbox; overwritten with `it.skip` stubs that compile and produce 5 skipped tests. Inert; safe to delete locally.

## Files changed

### Backend Python

- `backend/storage/filesystem.py` — added `_MAX_FOLDER_NAME_BYTES = 255` constant + `max_bytes` parameter to `sanitize_folder_name`.
- `backend/scripts/launch_desktop.py` — added `# pyright: ignore[reportArgumentType]` on line 279 (pywebview 6.2.1 stub gap).
- `backend/tests/_crash_child.py` — extended dispatcher with three new `dropzone-{create,update,delete}` operations + their crash-point hooks.
- `backend/tests/test_create_jump.py` — added `TestXsdRejectionBlocksWrite` class (2 tests) + `test_folder_name_normalises_nfd_title_to_nfc` (1 test).
- `backend/tests/test_validator.py` — added `test_rejects_doctype_inside_cdata` and `test_rejects_doctype_case_insensitive` (2 tests).
- `backend/tests/test_filesystem.py` — added 4 byte-length-cap tests under `TestSanitizeFolderName`.
- `backend/tests/test_dropzone_crash_recovery.py` — new file, 8 tests.
- `backend/tests/test_main_sigkill_lock_release.py` — new file, 1 test (POSIX-skip on Windows).
- `backend/tests/test_launch_desktop.py` — new file, 23 tests (TEST-3 + INFRA-6).

### Frontend

- `frontend/package.json` — added vitest, @testing-library/react, @testing-library/jest-dom, jsdom devDependencies; added `test`, `test:watch` scripts.
- `frontend/vite.config.js` — removed test block (moved to dedicated config).
- `frontend/vitest.config.js` — new, dedicated test config (jsdom, single-fork, 10s timeout).
- `frontend/test/setup.js` — new, stubs `matchMedia` + `globalThis.fetch`.
- `frontend/test/views.smoke.test.jsx` — new, 7 view-render tests.
- `frontend/test/{sanity,profile,lucide,api-import,careerstats-import,import-only}.test.{js,jsx}` — diagnostic stubs (5 are inert `.skip`'d; sanity is a vitest-runs-at-all canary).

### CI / build

- `.github/workflows/ci.yml` — added `frontend` job (Node 20, ubuntu-latest, npm ci + npm test).
- `skydive-logbook.spec` — new, PyInstaller spec at project root. Pulls version from `pyproject.toml` via `tomllib`.
- `build/icons/skydive-logbook.svg` — new, placeholder SVG icon (black disc, parachute curve, "SL" wordmark).
- `build/icons/README.md` — new, per-platform icon-conversion recipes.
- `BUILD.md` — new, per-platform build commands + signing reference + per-platform verification gaps.
- `DECISIONS.md` — appended D52 (DRAFT) "Code signing posture for v0.1".

### Reports

- `reviews/2026-04-30-finish-open-items.md` — this file.
- `reviews/2026-04-30-progress.html` — supersedes 2026-04-29-progress.html with this session's status.
- `HANDOFF.md` — updated state: D52 draft, 1459 tests, vitest live, what's left.

## What's left for v0.1

- Three `.venv-*` folders the user must rm locally.
- A real first-build per platform for INFRA-5/7. Until then BUILD.md's verification gaps are the documented unknowns.
- D52 is DRAFT; finalise after the first beta release reveals the gatekeeper UX.
- ARCH-2 / ARCH-5 / CODE-6 are still DECISION-NEEDED (Alex's input).
- CODE-1 (open_index per-call) is still deferred until profiling justifies it.

## Sources cited

- POSIX `flock(2)` lock-release on process death — <https://pubs.opengroup.org/onlinepubs/9799919799/functions/flock.html>
- `filelock` library docs (POSIX fcntl / Windows msvcrt) — <https://py-filelock.readthedocs.io/>
- PyInstaller spec files reference — <https://pyinstaller.org/en/stable/spec-files.html>
- PyInstaller macOS BUNDLE options — <https://pyinstaller.org/en/stable/spec-files.html#spec-file-options-for-a-mac-os-x-bundle>
- Apple Developer ID sign + notarize workflow — <https://developer.apple.com/documentation/security/signing-and-notarizing-macos-software>
- Apple `notarytool` reference — <https://developer.apple.com/documentation/security/customizing-the-notarization-workflow>
- Microsoft `signtool` reference — <https://learn.microsoft.com/en-us/dotnet/framework/tools/signtool-exe>
- Vitest configuration — <https://vitest.dev/config/>
