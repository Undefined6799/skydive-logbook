# Deep dive — ChatGPT review findings, code-level

Companion to `reviews/2026-05-15-code-debt-deep-audit.md` Appendix A.
This document walks each ChatGPT finding through the actual code: call
graph, exact failure mode, reproduction, concrete fix (with code), and
tests to add. All file:line citations verified against the working tree.

---

## Finding 1 — `setActiveTab('profile')` references a non-existent view

### What the code actually does

`frontend/src/App.jsx`:

```jsx
// line 16-23
const VIEWS = {
  dashboard: Dashboard,
  jumps: Jumps,
  myrig: MyRig,
  inventory: Inventory,
  dropzones: Dropzones,
  settings: Settings,
};

// line 29
const [activeTab, setActiveTab] = useState('dashboard');

// line 30
const View = VIEWS[activeTab] || Dashboard;  // ← silent fallback

// line 72-81 — handler for ONBOARDING_RESUME_EVENT
function handleResume() {
  getOnboardingState()
    .then((state) => { if (!cancelled) setOnboardingState(state); })
    .catch(() => { /* keep previous state */ });
  setResumeOverride(true);
  // Make sure the user lands on Profile after dismissing — the
  // sidebar may have been on a different tab when they clicked
  // "Resume setup", and the wizard overlay covers it anyway.
  setActiveTab('profile');  // ← BUG
}
```

`Sidebar` (`frontend/src/Sidebar.jsx:5-11`) defines NAV: `dashboard`,
`jumps`, `myrig`, `inventory`, `dropzones` + a separate `settings`
button. **No `profile` entry.** The `ResumeBanner` is rendered from
`Dashboard.jsx:262`. Comment at `App.jsx:26-28` reads: *"Identity moved
into Settings."*

### Call paths that trigger the bug

Two paths dispatch `ONBOARDING_RESUME_EVENT`:

1. `frontend/src/views/onboarding/ResumeBanner.jsx:85` —
   `handleResume()` button. The banner only renders on Dashboard
   (`Dashboard.jsx:262`). So when the user clicks "Resume setup"
   from the banner, they're *already* on Dashboard. The
   `setActiveTab('profile')` is a no-op visually because:
   - `VIEWS['profile']` is `undefined`
   - The fallback `|| Dashboard` keeps `View === Dashboard`
   - The user sees Dashboard, which is where they were.
   - **But:** the `activeTab` state is now the string `'profile'`,
     which matches no NAV item, so the Sidebar's `active` highlight
     vanishes (no sidebar button shows as selected).

2. `frontend/src/views/Settings.jsx:58` — the "Re-run setup wizard"
   button in `OnboardingSection`. The user is on Settings when they
   click it. Flow:
   - Event dispatches.
   - `App.jsx:80` sets `activeTab = 'profile'`.
   - Wizard overlay mounts on top (full-screen).
   - User walks through wizard or dismisses.
   - On dismiss, `handleWizardDone` (line 100-111) clears
     `resumeOverride` but **does not touch `activeTab`**.
   - User is now seeing Dashboard (via the `|| Dashboard` fallback)
     with `activeTab === 'profile'`. **No sidebar button is
     highlighted.**

### Observable user-visible artifact

After dismissing the wizard launched from Settings, the user lands on
Dashboard with **no active sidebar tab**. Clicking any sidebar tab
restores normal highlighting. A naive user won't notice; an attentive
user sees an odd-looking sidebar for one click.

### Why both prior audits missed it

The bug is hidden by the `|| Dashboard` fallback at `App.jsx:30`. The
fallback is a defensive pattern (sane default for unknown tab) — it
just happens to mask a real navigation bug. Grep-driven audits don't
catch a string-not-in-map referencing issue; you have to actually walk
the navigation graph.

### Fix

Two correct fixes, equivalent in user effect:

**Option A — explicit Dashboard navigation:**

```diff
   function handleResume() {
     ...
     setResumeOverride(true);
-    // Make sure the user lands on Profile after dismissing — the
-    // sidebar may have been on a different tab when they clicked
-    // "Resume setup", and the wizard overlay covers it anyway.
-    setActiveTab('profile');
+    // Land the user on Dashboard after the wizard dismisses — the
+    // ResumeBanner lives there and any further nudges will render
+    // alongside the user's stats. Profile (now under Settings) is
+    // not the right destination because the wizard already covers
+    // the identity step.
+    setActiveTab('dashboard');
   }
```

**Option B — drop the call entirely.** Defensible because the wizard
is a full-screen overlay regardless of tab, and the user can
self-navigate after dismiss. Riskier: if the user resumed from Settings
they stay on Settings after dismiss, which the original comment
suggests was undesirable.

Recommend **Option A** — minimal change, preserves the original
intent.

### Test

`frontend/test/app-resume-navigation.test.jsx` (new):

```jsx
import { render, fireEvent, waitFor } from '@testing-library/react';
import App from '../src/App';
import { ONBOARDING_RESUME_EVENT } from '../src/views/onboarding/ResumeBanner';

it('lands on Dashboard after dispatching the resume event from any tab', async () => {
  // ... mock getOnboardingState to return { completed: true, has_jumper: false }
  const { container } = render(<App />);
  // Simulate the Settings "Re-run wizard" button:
  window.dispatchEvent(new CustomEvent(ONBOARDING_RESUME_EVENT));
  // Wizard mounts (we assert by querying for an onboarding-specific
  // testid); after simulated dismiss, assert the active sidebar tab is
  // 'dashboard'.
});
```

Effort: 5 minutes for the fix, 15 minutes for the test. **P1.**

---

## Finding 2 — Raw exception type + message in 500 response body

### What the code actually does

`backend/api/rest.py:123-194`:

```python
@app.exception_handler(Exception)
async def on_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    ...
    # line 178-183
    # v0.1 is a single-user desktop app bound to loopback (D20).
    # Surface the exception type and message in the response body
    # so the user can read it in the modal/error banner without
    # tailing logs. When v0.1 grows beyond loopback (multi-user,
    # remote API), this branch tightens to honor D16's safety
    # concern about leaking internal state.
    detail = f"{type(exc).__name__}: {exc}"
    response = error_response(
        InternalServerError(detail),
        request_id=request_id,
        instance=request.url.path,
    )
```

The leak is **intentional and documented** — the comment is honest
about it. The "tightening" the comment promises has not happened.

### What actually leaks

Concrete examples of `str(exc)` for common Python exceptions that
could escape the typed `ServiceError` hierarchy:

| Exception class | Sample message | What it reveals |
|---|---|---|
| `KeyError` | `'aircraft'` | Internal field name; row schema |
| `FileNotFoundError` | `[Errno 2] No such file or directory: '/Users/alex/My Logbook/jumps/[42]/jump.xml'` | **Absolute path including username** |
| `sqlite3.OperationalError` | `no such column: foo` | DB column name |
| `lxml.etree.XMLSyntaxError` | `Premature end of data in tag attachment line 12, column 8` | XML byte offsets, internal layout |
| `pydantic.ValidationError` | (multi-line) field names + types | Internal model surface |
| `PermissionError` | `[Errno 13] Permission denied: '/path/to/file'` | Path + permission state |

For a single-user app bound to `127.0.0.1`, this is fine — the user is
the only client, the path is the user's own. For any of the user's
stated future contexts ("serious desktop application, potentially
multi-user later"), this is an information-disclosure vulnerability:

- **Multi-user**: leaks other users' filenames if a query touches
  cross-user state (today there is none; D8 reserves the surface).
- **LAN exposure (D48)**: leaks the host's `home/` layout. An attacker
  on the same WiFi can probe handlers to enumerate filesystem
  structure.
- **Crash report shared in a bug report**: user pastes a 500 body into
  a GitHub issue, leaking their machine layout to the world.

### Why this isn't already a P1 in my main audit

I did read this comment and noted it. My main audit framed it as
P3-equivalent under the v0.1 loopback assumption. ChatGPT correctly
elevated it given the user's stated longer-term context. The leak is
a one-line decision, not a structural issue.

### Fix — gated by a Settings flag, default-safe outside loopback

`backend/config.py` — add a setting:

```python
class Settings(BaseSettings):
    ...
    # When True, the unhandled-exception handler at /api/v1/* includes
    # `<ExcType>: <msg>` in the response body for operator readability.
    # When False (default for non-loopback deployments), the body is a
    # generic "internal error" string and the full detail goes to the
    # structured log only. Tracked under D-NEW.
    expose_internal_errors: bool = False

    @model_validator(mode="after")
    def _default_expose_to_loopback(self) -> Self:
        # If the user hasn't explicitly set the flag and the server is
        # bound to loopback, default to True for desktop UX. Any
        # non-loopback bind defaults to False — fail-safe.
        if "expose_internal_errors" not in self.model_fields_set:
            self.expose_internal_errors = self.host in {"127.0.0.1", "localhost", "::1"}
        return self
```

`backend/api/rest.py` — read the flag:

```python
@app.exception_handler(Exception)
async def on_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    ...
    settings = get_settings()  # cached via lru_cache
    if settings.expose_internal_errors:
        detail = f"{type(exc).__name__}: {exc}"
    else:
        # Generic message; full detail is in the structured log,
        # correlated by request_id from the response body.
        detail = "an internal error occurred; see server logs"
    response = error_response(
        InternalServerError(detail),
        request_id=request_id,
        instance=request.url.path,
    )
```

### Test

```python
# backend/tests/test_unhandled_exception_redaction.py
def test_unhandled_exception_redacts_detail_when_not_loopback(client_factory):
    app = client_factory(expose_internal_errors=False)
    # Register a deliberately-failing route
    @app.get("/_test/explode")
    async def explode():
        raise FileNotFoundError("/home/alice/secrets/.password")
    client = TestClient(app)
    resp = client.get("/_test/explode")
    assert resp.status_code == 500
    body = resp.json()
    assert "alice" not in body["detail"]
    assert "/home/" not in body["detail"]
    assert "an internal error" in body["detail"].lower()

def test_unhandled_exception_exposes_detail_on_loopback(client_factory):
    app = client_factory(expose_internal_errors=True)
    # ... assert detail contains the FileNotFoundError message
```

Effort: 30 minutes including settings + tests. **P1** under the
"may become serious app" framing. Should ship with a new D-entry
documenting the policy decision (loopback default vs non-loopback
default).

### Additional concern in the same handler

`backend/api/rest.py:131-137` prints the **full traceback** to stderr:

```python
print(
    f"\n=== unhandled exception in {request.url.path} ===",
    file=sys.stderr,
    flush=True,
)
traceback.print_exception(type(exc), exc, exc.__traceback__)
```

This is documented as "human visibility for desktop launcher in a
terminal" but bypasses the structured JSON logger. If a future
deployment redirects stderr to a file that gets sent to telemetry,
the traceback (which contains local variable names, source file
paths) is in that pipe. **For v0.1 fine.** Note: if the
`expose_internal_errors` flag goes false, the stderr print should
also be gated — they leak the same data.

---

## Finding 3 — `jump_service.py` excessively large

### What the code actually contains

`backend/services/jump_service.py` — 1252 LOC, 11 public functions:

| Function | Lines | Concern |
|---|---|---|
| `create_jump` | 232-452 | CRUD |
| `get_jump` | 455-490 | CRUD |
| `list_jump_files` | 520-578 | Files |
| `track_files` | 581-728 | Attachments |
| `add_attachments` | 731-866 | Attachments |
| `delete_attachment` | 869-978 | Attachments |
| `list_jumps` | 981-1023 | CRUD |
| `update_jump` | 1026-1204 | CRUD |
| `delete_jump` | 1207-1253 | CRUD |

Each function is well-commented, well-typed, individually small (most
in the 30-150 LOC range, with the bigger ones being `create_jump`,
`track_files`, `add_attachments`, `update_jump`). The size comes from
**count, not individual complexity**.

### What I'd actually split, and how

The clean cleavage is **per-operation-family**:

```
backend/services/jump/
  __init__.py            re-exports for the public surface
  _common.py             _index_conn, _get_jump_folder,
                         _write_jump_and_manifest,
                         _jump_number_is_taken,
                         _raise_jump_number_conflict,
                         _sanitize_upload_filenames
  crud.py                create_jump, get_jump, list_jumps,
                         update_jump, delete_jump
  attachments.py         track_files, add_attachments,
                         delete_attachment
  files.py               list_jump_files + FolderFile dataclass
```

Public import sites (`backend/api/jumps.py`,
`backend/services/reindex_service.py`, tests) all use
`from backend.services import jump_service`. The `__init__.py`
re-exports keep these working.

```python
# backend/services/jump/__init__.py
from .crud import create_jump, get_jump, list_jumps, update_jump, delete_jump
from .attachments import track_files, add_attachments, delete_attachment
from .files import list_jump_files, FolderFile
from ._common import Upload  # the dataclass moves here too

__all__ = [
    "create_jump", "get_jump", "list_jumps", "update_jump", "delete_jump",
    "track_files", "add_attachments", "delete_attachment",
    "list_jump_files", "FolderFile",
    "Upload",
]
```

### Effort and risk

- Mechanical refactor; no behavior change.
- ~3 hours including running the full test suite between each move.
- All 71 backend test files import from `jump_service` — they keep
  working because of the re-exports.
- Risk: zero if the re-exports are complete. The pyright strict gate
  catches any missed export at PR time.

### Why this isn't urgent

Already tracked in my main audit §4.4 and the 2026-05-14 audit §3.2.
ChatGPT's framing as a "bug" is overstated — it's debt, not a defect.
The pyright + ruff + test gates make the file maintainable even at
1252 LOC. **P3** unless onboarding pain becomes a real complaint.

---

## Finding 4 — File upload / attachment validation incomplete

### Three separate gaps, three different severities

**Gap 4a — No content-type sniffing.**

`backend/api/jumps.py:179, 315`:

```python
Upload(
    filename=f.filename or "",
    content_type=f.content_type,  # ← from multipart Content-Type
    chunks=_upload_chunks(f),
)
```

`f.content_type` comes straight from the client's multipart
declaration. The client controls it. An attacker uploads
`evil.html` with `Content-Type: image/png` → the server stores
`content_type="image/png"` in `jump.xml`.

**Today's safety:** no GET endpoint streams attachment bytes with the
stored `content_type` as the response `Content-Type` header. There is
no `GET /api/v1/jumps/{id}/attachments/{filename}` route. So nothing
will execute the bytes via a browser MIME-sniffer.

**Latent hazard:** the moment the SPA gains an inline-view feature
("show the jump's flight log photo"), the wrong `Content-Type` becomes
an XSS vector. The fix has to land **before** the inline-view
endpoint ships.

**Concrete fix shape:**

```python
# backend/services/jump_service.py — extend Upload sanitization
def _sniff_content_type(first_chunk: bytes, declared: str | None) -> str | None:
    """Return a trusted content-type, or None if the bytes can't be
    classified. Uses the stdlib's mimetypes for now; for stronger
    sniffing, layer in `filetype` (no external deps beyond pure Python)
    or `python-magic` (binds libmagic).
    """
    # Stdlib's `imghdr`/`sndhdr` are minimal; use the magic-bytes
    # mapping in `filetype` for common formats.
    import filetype
    kind = filetype.guess(first_chunk)
    sniffed = kind.mime if kind else None
    if sniffed and declared and sniffed != declared:
        # Either reject hard or downgrade to the sniffed value. v0.1
        # downgrade: log + trust the bytes.
        _logger.warning("content_type_mismatch", extra={
            "declared": declared, "sniffed": sniffed,
        })
        return sniffed
    return sniffed or declared
```

But this is a future feature, **not v0.1 blocking**. Today's hazard is
purely latent. **P2 — fix before the first attachment-view endpoint
ships.**

**Gap 4b — No per-file or per-request total size cap.**

Already covered in my main audit §1.5. Confirmation:

```
$ grep -rE "MAX_(BODY|UPLOAD|REQUEST)|max_size|client_max" backend/
(no hits in production code)
```

A multipart POST with 50 GiB of attachments will stream to disk
without rejection until disk fills. The streaming primitive is correct
(memory-bounded), so the failure mode is "filesystem full" not "OOM".
**P2.**

**Gap 4c — Filename validation does happen, and is solid.**

`backend/storage/filesystem.py:sanitize_filename` (line 144-162) rejects:
- forbidden chars (`/\:*?"<>|`)
- control characters
- empty / `.` / `..`
- Windows reserved names (CON, PRN, …)
- trailing space / dot
- 255-char cap

And `backend/services/jump_service.py:_sanitize_upload_filenames`
(line 177-229) wraps it with duplicate-within-request detection.

Tests (`backend/tests/test_filesystem.py`, 478 LOC) pin every rule.
**No gap here.**

### Effort

Gap 4a + 4b together: ~4 hours including a `filetype` dependency add
and the body-size middleware. Both gate D48.

---

## Finding 5 — Frontend still leans on `mock.js`

### Actual scope (much narrower than implied)

`frontend/src/mock.js` — 351 LOC of prototype data. Exports:

```
exports = jumper, rigs, unassignedComponents, miscMock1, ...
```

Importers across `frontend/src/`:

```
$ grep -rE "from.*['\"]./mock['\"]|from.*['\"]../mock['\"]" frontend/src/
frontend/src/modals/ComponentModal.jsx:4: import { unassignedComponents } from '../mock';
```

**One file. One import.** And that file — `ComponentModal.jsx`,
322 LOC — is **not rendered anywhere**:

```
$ grep -rE "<ComponentModal[> ]|import ComponentModal" frontend/src/
(no hits except its own export)
```

ComponentModal is dead code. The active modals are
`ComponentDetailModal.jsx` (1472 LOC, actively rendered from
`MyRig.jsx`) and `AddComponentModal.jsx` (used from `AddRigModal.jsx`
and `Inventory.jsx`). `ComponentModal.jsx` is an earlier prototype
that was superseded but never deleted.

### Fix

Two commits, both trivial:

```bash
git rm frontend/src/modals/ComponentModal.jsx
git rm frontend/src/mock.js
```

Then `npm test` — should pass cleanly because nothing imports either.

**Effort: 2 minutes.** **P1 (hygiene + reduces grep noise).**

### Slight nuance

If there's any chance `ComponentModal` will be revived (e.g. the
"jumper-driven main swap" flow D-entry is in flight and that flow
needs an in-rig pick-component modal), then delete `mock.js` but keep
`ComponentModal.jsx` — and wire it to the real list endpoints
(`/api/v1/mains?assigned_rig_id=null`, etc.) instead of mock data.
Worth checking with the human before the delete commit.

---

## Finding 6 — Multipart upload limits and validation

Largely covered in §4 above and §1.5 of the main audit. Two
additions:

### 6a — `Upload.chunks: Iterable[bytes]` — consumed-once contract is unenforced

`backend/services/jump_service.py:60-75`:

```python
@dataclass(frozen=True)
class Upload:
    """A single inbound file upload on its way to ``atomic_write_stream``.
    ...
    The ``chunks`` iterable is consumed exactly once — that matches
    the HTTP upload reality (bytes flow past, then they're gone) and
    matches what ``atomic_write_stream`` needs.
    """
    filename: str
    content_type: str | None
    chunks: Iterable[bytes]
```

The "consumed exactly once" contract is documented but unenforced. A
test that builds `Upload(..., chunks=[b"hello"])` (a list, not a
generator) and then calls a code path that walks `chunks` twice (e.g.
a retry inside the service) would silently re-write the bytes —
which is fine for a list but **catastrophic for the
`_upload_chunks(f)` generator** in `api/jumps.py:179` because the
underlying SpooledTemporaryFile's read cursor is past EOF on the
second pass.

There's no code today that re-walks `chunks`, so this is a contract
trap for future contributors, not an active bug. Cheap defense:

```python
@dataclass(frozen=True)
class Upload:
    filename: str
    content_type: str | None
    chunks: Iterable[bytes]
    _consumed: bool = field(default=False, init=False)

    def __iter__(self):
        if self._consumed:
            raise RuntimeError("Upload.chunks already consumed")
        object.__setattr__(self, "_consumed", True)
        yield from self.chunks
```

But that adds frame-level state to a frozen dataclass which feels
heavier than the risk warrants. **NIT.** Document by adding the
contract to a property name (e.g. `chunks_consumed_once`) or by
raising clearly on a second `iter(upload.chunks)` call.

### 6b — `_UPLOAD_CHUNK_SIZE = 64 * 1024`

`backend/api/jumps.py:64` — 64 KiB chunk size. Comment says "small
enough to keep memory bounded on multi-gigabyte uploads, large enough
to avoid per-chunk syscall overhead on small files." This is fine. No
gap.

---

## Finding 7 — `CustomEvent` global bus pattern

### Inventory of window-level events

```
$ grep -rE "(CustomEvent|addEventListener|dispatchEvent)" frontend/src/ \
    | grep -v node_modules
```

Three channels:

1. `ALTITUDE_CHANGE_EVENT` (`frontend/src/units.js:38-39, 58`) —
   listeners: every component that reads altitude via `useAltitudeUnit`.
2. `ONBOARDING_RESUME_EVENT`
   (`frontend/src/views/onboarding/ResumeBanner.jsx:21`,
   dispatched by `Settings.jsx:58`, `ResumeBanner.jsx:85`;
   listened by `App.jsx:82`).
3. `ONBOARDING_STATE_CHANGED_EVENT`
   (`ResumeBanner.jsx:28`, dispatched by `App.jsx:110`;
   listened by `ResumeBanner.jsx:54`, `Identity.jsx:72`).

Plus `Dropzones.jsx:448-449` adds `mousedown`/`keydown` listeners for
click-outside-dropdown handling — these are local UI affordances, not
the cross-component bus pattern.

### Why it's a smell

- **No type safety.** A typo in a channel name (`'logbook:onboarding-state-chnaged'`)
  silently breaks the wiring. No compile-time check.
- **No DevTools visibility.** React DevTools shows component state and
  prop flow; it does not show window-level event traffic. Debugging a
  cross-component refresh requires reading the dispatch+listen pair
  by hand.
- **Strict Mode fragility.** React 18+ Strict Mode double-invokes
  effects in development to catch impure cleanup. Each double-mount
  adds, then removes, a listener — net zero in steady state, but the
  brief window between add and remove can fire the handler twice if
  an event dispatches during the cleanup phase of an unmount.
- **HMR (hot module reload) fragility.** Vite HMR replaces the module
  but the old listeners may not detach if the cleanup callback's
  closure references the new module's symbol. Manifests as duplicate
  banner re-renders after editing `ResumeBanner.jsx`.
- **Cross-window risk.** `window.dispatchEvent` on a CustomEvent is
  same-window only, but if a future contributor switches to
  `BroadcastChannel` for cross-tab sync (a plausible feature for a
  pywebview window plus a settings popup), the channel id becomes a
  cross-window security surface.

### Refactor to a Context

Replace the two onboarding events with a provider. The altitude
event is more isolated; can wait.

```jsx
// frontend/src/onboardingContext.jsx — new file, ~50 LOC
import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { getOnboardingState } from './api';

const Ctx = createContext({
  state: undefined,
  refresh: () => {},
  requestResume: () => {},
  resumeRequested: false,
  acknowledgeResume: () => {},
});

export function OnboardingProvider({ children }) {
  const [state, setState] = useState(undefined);
  const [resumeRequested, setResumeRequested] = useState(false);

  const refresh = useCallback(() => {
    let cancelled = false;
    getOnboardingState()
      .then((s) => { if (!cancelled) setState(s); })
      .catch(() => { if (!cancelled) setState(null); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const cleanup = refresh();
    return cleanup;
  }, [refresh]);

  return (
    <Ctx.Provider value={{
      state,
      refresh,
      requestResume: () => setResumeRequested(true),
      resumeRequested,
      acknowledgeResume: () => setResumeRequested(false),
    }}>
      {children}
    </Ctx.Provider>
  );
}

export function useOnboarding() {
  return useContext(Ctx);
}
```

`App.jsx`, `ResumeBanner.jsx`, `Settings.jsx`, `Identity.jsx` all swap
their `window.add/dispatchEvent` calls for `useOnboarding()`. The
inventory of changes:

| File | Before | After |
|---|---|---|
| `App.jsx:64-87` | `addEventListener(ONBOARDING_RESUME_EVENT)` + `setActiveTab('profile')` | `useEffect` watching `resumeRequested` + `setActiveTab('dashboard')` (fix from §1) |
| `App.jsx:106-110` | `dispatchEvent(ONBOARDING_STATE_CHANGED_EVENT)` | `refresh()` from context |
| `ResumeBanner.jsx:36-58` | local fetch + reload key + listener | consume from context |
| `ResumeBanner.jsx:84-86` | `dispatchEvent(ONBOARDING_RESUME_EVENT)` | `requestResume()` from context |
| `Settings.jsx:56-59` | same | same |
| `Identity.jsx:72` | `addEventListener(ONBOARDING_STATE_CHANGED_EVENT)` | `useOnboarding().state` consumer |

Effort: ~3 hours including the smoke test of the four affected views.
**P2.**

### Altitude event — keep or migrate?

`ALTITUDE_CHANGE_EVENT` is consumed by `useAltitudeUnit()` — a hook in
`units.js`. The pattern is:

```js
// units.js:50-65 — roughly
export function useAltitudeUnit() {
  const [unit, setUnit] = useState(getAltitudeUnit());
  useEffect(() => {
    function handleCustom(e) { setUnit(e.detail); }
    function handleStorage() { setUnit(getAltitudeUnit()); }
    window.addEventListener(ALTITUDE_CHANGE_EVENT, handleCustom);
    window.addEventListener('storage', handleStorage);
    return () => {
      window.removeEventListener(ALTITUDE_CHANGE_EVENT, handleCustom);
      window.removeEventListener('storage', handleStorage);
    };
  }, []);
  return [unit, ...];
}
```

The `'storage'` listener is the cross-tab sync path (it fires when
`localStorage` changes in **another** tab — same-tab writes don't
fire it). The CustomEvent is the same-tab sync path. So both are
required for the actual contract.

This one is harder to migrate cleanly because the cross-tab story
genuinely needs a window-level signal. A Context would handle
in-tab updates but not cross-tab. The pattern is correct for the
problem; the migration would be lateral. **Keep as-is.**

---

## Finding 8 — Crash consistency across XML + SQLite + manifests

### The actual invariants

D2, D3, D10, D25 together: XML on disk is authoritative; SQLite is a
rebuildable projection; manifests are derived; atomic_write is the
only sanctioned write primitive. The reconcile-on-read step
(`folder_reconcile`, `backend/storage/reconcile.py`) heals
SHA256SUMS drift from a crash between the XML write and the
manifest write.

For each multi-step write, the code already documents its crash
order. Spot-checks:

| Operation | Step 1 (durable) | Step 2 | Step 3 | Crash recovery |
|---|---|---|---|---|
| `create_jump` | mkdir folder | stream attachments | write jump.xml + SHA256SUMS | folder_reconcile heals manifest; orphan attachments visible via list_jump_files |
| `update_jump` | write jump.xml at old folder | write SHA256SUMS | rename folder (if needed) | reconcile heals manifest; rename non-completion leaves old folder name with new content (visible via verify) |
| `add_attachments` | stream each attachment | rewrite jump.xml | rewrite SHA256SUMS | per-step crash leaves orphan attachment(s) on disk, jump.xml unchanged — track_files recovers |
| `delete_attachment` | rewrite jump.xml without entry | rewrite SHA256SUMS | unlink file | crash after step 1: file becomes untracked drop-in; recoverable via track_files |
| `create_rig` | validate components | mkdir folder | write rig.xml + SHA256SUMS | (BROKEN — see §A.8.3 below) |
| `update_rig` | write rig.xml at old folder | write SHA256SUMS | rename folder | similar to update_jump |
| `delete_rig` | clear assigned_rig_id on 4 components | soft_delete rig folder | (post-D58 amendment) clear/transfer star | gap: partial-clear is not recoverable on retry |

Most of these have crash tests:

- `test_dropzone_crash_recovery.py` — 396 LOC
- `test_main_sigkill_lock_release.py` — 168 LOC
- `test_concurrent_writes.py` — 216 LOC
- `tests/_crash_child.py` — 479 LOC (shared SIGKILL harness)

### Three real gaps I'd add tests for

#### 8.1 — `create_rig` D37 partial-write recovery

`backend/services/rig_service.py:402-548`. I covered this in my main
audit §2.2. ChatGPT correctly flags this as an example of the
class. Concrete failure:

1. `create_rig` validates all four components (line 488-501) — each
   `assigned_rig_id` is None or equals `r.id` (a fresh UUID4).
2. `mkdir(exist_ok=False)` creates the rig folder (line 506).
3. `_write_rig_folder(folder, r)` writes rig.xml referencing all four
   components (line 519).
4. Loop assigns each component (line 535-537):
   - Iter 1: `main.assigned_rig_id = r.id` ✓
   - **Crash here, e.g. process killed**

State on disk after crash:
- `rigs/<nickname>/rig.xml` exists, references all four components.
- `inventory/mains/<id>.xml` has `assigned_rig_id = r.id`.
- `inventory/reserves/<id>.xml`, `aads/<id>.xml`, `containers/<id>.xml`
  still have `assigned_rig_id = None`.

Retry path is **doubly broken**:

1. **`mkdir(exist_ok=False)` fails** because the folder already exists.
   The service raises `RigNicknameConflict` with the user's nickname.
2. **Even if the user renames and retries with a fresh nickname**,
   `_validate_component_for_assignment` (line 145-219) sees that the
   *main* has `assigned_rig_id = <old r.id>`, which is neither `None`
   nor the new `r.id` (because line 475 mints a new UUID4 on retry).
   It raises `ComponentAlreadyAssigned` with the **stale** rig id —
   pointing at a rig that doesn't exist in the index but does have a
   folder on disk.

Recovery requires manual XML edit. **This is a real footgun for the
user.**

Fix shape: ship `folder_reconcile_rigs(logbook_root)`:

```python
# backend/storage/reconcile.py — extend
def folder_reconcile_rigs(logbook_root: Path) -> ReconcileReport:
    """Heal D37 bidirectional refs between rigs and components.
    
    For every rig folder containing rig.xml:
      - Parse rig.xml; build the set of (component_kind, id) refs.
      - For each ref, ensure the component's assigned_rig_id matches.
        If it doesn't (None or wrong rig id), repair the component.
    
    For every component XML across mains/reserves/aads/containers:
      - If assigned_rig_id is set, ensure the named rig actually
        references the component. If the rig is gone or doesn't
        reference back, clear the component's assigned_rig_id.
    
    Idempotent. Run on boot under WRITER_LOCK alongside the existing
    jump-folder reconcile. Each repair is one atomic_write per
    affected component.
    """
```

Call site: `backend/main.py` boot sequence, after `bootstrap()` and
`open_index()`, before the API starts accepting traffic.

Test:

```python
# backend/tests/test_rig_partial_create_recovery.py
def test_create_rig_crash_after_first_component_assign_recovers(tmp_path, monkeypatch):
    # 1. Set up four unassigned components.
    # 2. Monkey-patch main_service.set_assigned_rig_id to raise after
    #    being called once.
    # 3. Call rig_service.create_rig; assert it raises.
    # 4. Assert: rig folder exists, main has stale assigned_rig_id,
    #    reserve/aad/container have None.
    # 5. Call folder_reconcile_rigs(tmp_path).
    # 6. Assert: main's assigned_rig_id is now None (rig.xml is
    #    "incomplete" — no other components point back, so the rig
    #    is treated as a half-built artifact and components clear),
    #    OR all four are assigned (the reconcile decides which
    #    direction to heal). Define the policy and pin it.
```

The policy decision is non-trivial: when the rig has refs but the
components don't all point back, do you (a) finish the assignment or
(b) revert the rig? Two defensible answers. Recommend (a) — the rig
folder is the user's stated intent — but document the choice in a
new D-entry.

#### 8.2 — `update_jump` folder-rename crash leaves the wrong folder name

`backend/services/jump_service.py:1141-1142`:

```python
if new_folder != current_folder:
    os.rename(current_folder, new_folder)
```

Sequence of events:

1. Write jump.xml at `current_folder` with new title/jump_number (step 6).
2. Write SHA256SUMS at `current_folder` (step 7).
3. `os.rename(current_folder, new_folder)` (step 8) — **crash here**.

State after crash:
- `current_folder` (old name, e.g. `jumps/[42]`) contains jump.xml
  with the new content (e.g. `<jump_number>43</jump_number>`).
- The SQLite index row was not yet updated (step 9 hasn't run) — it
  still points at `current_folder`.

What `verify` sees: a folder named `[42]` whose jump.xml claims
jump_number 43 — a clean mismatch. What `get_jump` sees: the index
row says `folder = jumps/[42]`, reads jump.xml from there, gets
jump_number=43 — the response *is* correct from the user's POV (the
edit landed), but the on-disk layout violates D4 ("folder name
encodes jump_number").

Recovery requires re-running `update_jump` (which will see the same
number and skip the rename) or running reindex which will spot the
mismatch.

**There's no test for this exact step crash.** Add:

```python
# backend/tests/test_update_jump_rename_crash.py
def test_update_jump_crash_after_xml_before_rename_recovers(...):
    # Use the _crash_child.py harness to SIGKILL the process between
    # the SHA256SUMS write and the os.rename call. Assert verify
    # reports the mismatch and a clean reindex repairs.
```

**P2.**

#### 8.3 — `track_files` SHA256 streaming can disagree with on-disk bytes if file is mutated mid-read

`backend/services/jump_service.py:670-678`:

```python
for name in to_track:
    path = folder / name
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    size = path.stat().st_size  # ← race
```

The hash is computed over the bytes as read; `path.stat().st_size`
is queried separately. If the user is concurrently saving the file
from another app (Finder copy still in progress, or cloud-sync
download mid-flight), the size and the hash can disagree with each
other — bytes hashed reflect what was on disk during the read; size
reflects what's on disk *after*. Mismatch leaves an Attachment whose
`size` doesn't match its `sha256`.

**Not a corruption bug** (jump.xml's claim is self-consistent), but
a verify-warning bug — the SHA256SUMS later compares
`actual_size_now` against `attachment.size` and flags drift even
though both claims came from the same write.

Cheap fix: stat once at the start, use that size, hash only the
first `size` bytes:

```python
size = path.stat().st_size
h = hashlib.sha256()
with path.open("rb") as f:
    remaining = size
    while remaining > 0:
        chunk = f.read(min(64 * 1024, remaining))
        if not chunk:
            break  # file truncated mid-read — bigger problem
        h.update(chunk)
        remaining -= len(chunk)
```

Or stronger: refuse to track files whose mtime changed between stat
and the end of the hash read. **NIT** — low-probability race for a
local-only single-user app.

---

## Summary — what to fix, in what order

| # | Finding | Severity | Effort | When |
|---|---|---|---|---|
| 1 | `setActiveTab('profile')` → `'dashboard'` | **P1** | 5 min | This week |
| 5 | Delete `ComponentModal.jsx` + `mock.js` | **P1** | 2 min | This week |
| 2 | Settings-gated exception redaction | **P1** | 30 min | Before D48 |
| 8.1 | `folder_reconcile_rigs` + test | **P2** | 1 day | Before R.3 |
| 8.2 | Update_jump rename crash test | **P2** | 2 hr | Same slice |
| 4a | Content-type sniffing | **P2** | 4 hr | Before attachment-view endpoint |
| 4b | Body size middleware | **P2** | 2 hr | Before D48 |
| 7 | `<OnboardingProvider>` Context | **P2** | 3 hr | Next FE slice |
| 3 | Split `jump_service.py` | **P3** | 3 hr | When onboarding pain shows |
| 6a | Upload.chunks consumed-once | NIT | 15 min | Optional |
| 8.3 | track_files size/hash race | NIT | 15 min | Optional |

The first three (#1, #5, #2) are an hour total and **strictly
improve the codebase with zero design debate**. Ship them in three
focused commits and the headline value of this review is captured.

---

*— end —*
