# Pyright rollout — 2026-04-29

DEP-2 from the 2026-04-29 tech-debt audit. Same session as the audit
itself; the originally-scheduled morning agent at 2026-04-30 08:00 EDT
was cancelled in favor of doing the work in-session.

## Result

| Metric | Before | After |
|---|---|---|
| `pyright backend` (strict-everywhere) | 3760 errors | — |
| `pyright backend` (Option 2) | — | **0 errors** |
| `ruff check backend` | green | green |
| `pytest backend/tests` | 1385 passing | 1385 passing |

**0 / 0 / 0** — type, lint, test all clean. CI gates the same triple
on every push and PR per `.github/workflows/ci.yml`.

## What landed

1. **`pyproject.toml` `[tool.pyright]` reshape.** Top-level
   `typeCheckingMode = "basic"`. `strict = [...]` allow-list covers
   every production folder + `main.py` + `config.py`.
   `executionEnvironments` per-folder override for `backend/xml/`
   silencing `reportAttributeAccessIssue` and
   `reportOptionalMemberAccess` (lxml stub gap), and another for
   `backend/tests/` silencing the fixture-typing rule cluster. Both
   envs carry `extraPaths = ["."]` so project-root import resolution
   still works inside the env (lost otherwise — see "Surprise" §3).
2. **CI step.** `uv run pyright backend` added to
   `.github/workflows/ci.yml` between the ruff and pytest steps. Runs
   on every matrix cell (3 OSes × 3 Python versions = 9 cells).
3. **D51 in DECISIONS.md** — pins the Option 2 policy, suppression
   form, lxml-boundary architecture, bump policy, and re-evaluation
   triggers.
4. **CLAUDE.md §7** — green-light triple updated to add pyright;
   Option 2 policy summarized for new contributors.

## Architectural moves

The cleanup wasn't just annotation work. Three small architectural
fixes removed cascade clusters at their source:

### `XMLElement = Any` type aliases in `backend/xml/validator.py`

lxml's `etree._Element` is unresolved by pyright — every caller
inheriting that type saw `Unknown`, cascading 75+ errors across
`storage/`, `services/`, and `xml/serialize.py`. Defining

```python
from typing import Any, TypeAlias
XMLElement: TypeAlias = Any
XMLSchema: TypeAlias = Any
```

at the top of `validator.py` and updating its public function
signatures (`parse() -> XMLElement`, `validate(element: XMLElement, ...)`,
`schema_for_namespace() -> XMLSchema`) gave callers a usable type
instead of a propagating `Unknown`. The runtime types are unchanged
— at runtime these are still `lxml.etree._Element` / `XMLSchema`
objects.

### `namespace_of(element)` helper

Three modules (`storage/manifest.py`, `storage/verify.py`, plus the
big `from lxml import etree` line in `bootstrap.py`) imported lxml
directly to access `etree.QName(root).namespace`. Each was a
mini-cascade of unknowns. Centralizing to

```python
def namespace_of(element: XMLElement) -> str:
    ns = etree.QName(element).namespace
    return ns or ""
```

removed those direct imports and kept the lxml use behind the
`backend/xml/` boundary where the pyright override silences it.

### `validate_schema_file(xsd_path)` helper

`storage/bootstrap.py` did its XSD-syntax safety check via a direct
`from lxml import etree; doc = etree.parse(...); etree.XMLSchema(doc)`
sequence. Now extracted into `validator.validate_schema_file(...)`
which lives inside the typed boundary; bootstrap calls the typed
helper and wraps `XSDValidationError` into `OSError` for `main.py`'s
existing exit-1 branch.

## The dataclass / Pydantic list-default pattern

Across `backend/models/` (30 fields) and a few dataclasses in
services + storage, the same pattern flagged as `list[Unknown]`:

```python
items: list[X] = Field(default_factory=list)            # Pydantic
items: list[X] = field(default_factory=list)            # dataclass
```

Pyright sees the bare `list` callable and infers
`default_factory: () -> list[Unknown]` regardless of the field
annotation. Two clean fixes by container type:

- **Pydantic v2:** `items: list[X] = []` works — Pydantic v2 deep-
  copies the literal default per instance, so the shared-mutable
  footgun doesn't apply. Verified against the runtime via a quick
  `M(); M()` parallel-instance check.
- **Dataclass:** `field(default_factory=list[X])` works — `list[X]`
  is a callable `types.GenericAlias` that returns an empty list with
  the element type preserved. Same runtime behavior; pyright stops
  seeing `Unknown`.

Applied across:
- `backend/models/` (30 instances) — Pydantic v2 literal defaults.
- `backend/storage/verify.py` (1 instance) — dataclass form.
- `backend/services/reindex_service.py` (4 instances) — dataclass form.
- `backend/services/stats_service.py` (2 instances) — dataclass form.

## The `Jumper(**dict)` cascade

`backend/services/jumper_service.py:create_jumper` and `update_jumper`
both used the pattern

```python
j = Jumper(
    id=uuid4(),
    **{
        **payload.model_dump(),
        "exit_weight_updated_at": weight_stamp,
    },
    created_at=now,
    updated_at=now,
)
```

Pyright cannot narrow the dict's value type per kwarg — it sees
every kwarg as `Any | date` and reports it can't be assigned to
`name: str | None`, `exit_weight_lb: float`, etc. (17 total errors
between the two functions.) Runtime works fine — pydantic validates
each field — but the type checker has nothing to verify against.

Refactored to

```python
j = Jumper.model_validate({
    "id": uuid4(),
    **payload.model_dump(),
    "exit_weight_updated_at": weight_stamp,
    "created_at": now,
    "updated_at": now,
})
```

`model_validate` accepts `Any` and returns `Jumper`. Same runtime
guarantees; pyright is happy.

## The `# pyright: basic` pragma on `launch_desktop.py`

The desktop launcher imports `webview` (pywebview), which ships no
type stubs. 50+ errors fell out as `Unknown` cascades through every
`webview.X` access. Two options:

- **Per-line ignores everywhere** — 50+ comments, brittle, hard to
  see real bugs in.
- **Per-file `# pyright: basic` pragma** at the top — downgrades just
  this file to basic mode. Real bugs (argument-type, optional access,
  operator) still fire under basic. The pragma is a one-liner and
  documents the policy in-place.

Picked the pragma. Three remaining `reportMissingImports` for
`import webview` itself (basic still reports those) carry per-line
`# pyright: ignore[reportMissingImports]` annotations.

## Real bugs surfaced

The audit baseline counted 292 `reportArgumentType` errors —
historically the highest-yield bug class. After Option 2 silenced the
test fixture-spread noise (159 of those), the real ones in production
code came down to:

- **Zero new bugs.** Every production-code `reportArgumentType` was
  resolved by adding type annotations or via the `model_validate`
  refactor — none surfaced a runtime bug. The reasonable
  interpretation: the codebase's existing test discipline (1385
  tests, including the D25 crash harness) was already catching the
  classes of bug pyright targets at this surface.

The architectural moves did remove **two genuinely dead helpers**:
`backend/services/jumper_service.py:_jumper_xml_path` (defined
once, never called or tested) and
`backend/services/rig_service.py:_rig_folder` (replaced inline
ages ago, definition orphaned). Both surfaced as
`reportUnusedFunction`. Removed.

## Suppressions added (the audit trail)

Total: **8 `# pyright: ignore[<rule>]`** comments in production
code, plus **1 `# pyright: basic`** file-level pragma. By rule:

| Rule | Count | Where |
|---|---|---|
| `reportUnusedFunction` | 3 | `backend/api/rest.py` — FastAPI handlers registered via `@app.exception_handler` and `@app.get` decorators. The decorators register the function on the app; pyright doesn't track the registration and sees the local-scope function as unused. |
| `reportPrivateUsage` | 5 | `backend/services/jumper_credential_service.py` — deliberate cross-service helper accesses to `jumper_service._now_utc_iso`, `_write_jumper`, `_jumper_folder`, `_read_jumper`, `_today_utc`. The underscore signals "service-private"; the cross-module use is intentional package-shape. |
| `reportMissingImports` | 3 | `backend/scripts/launch_desktop.py` — `import webview` lines. Pywebview is the optional `[desktop]` extra; CI does not install it. |
| `# pyright: basic` (file) | 1 | `backend/scripts/launch_desktop.py` — pywebview ships no type stubs; per-file basic mode silences the 50+ Unknown cascades while keeping basic's bug-catching rules. |

Every suppression carries a one-line reason next to the rule name
per D51's policy.

## Surprise during the rollout

Initial config attempt set `typeCheckingMode = "strict"` at top
level + `executionEnvironments` overrides for `tests/` and `xml/`
expecting the override to *relax* the rules. **Pyright doesn't allow
that.** Per the docs §"Diagnostic Settings Defaults": *"In strict
type checking mode, overrides may only INCREASE the strictness."*
The right shape is the inverse: top-level `basic` + `strict = [...]`
allow-list. Reshaped accordingly.

Adding `executionEnvironments` *also* changed the import-resolution
scope — without explicit `extraPaths = ["."]`, files in the env
couldn't resolve `from backend.xml.validator import ...`. Took one
iteration to discover (3760 → 571 → 698 → 390 → ...). Documented
in the inline comment in `pyproject.toml`.

## Files touched

Production code:
- `backend/api/openapi.py` — type alias for `PROBLEM_DETAILS_SCHEMA`,
  return type for `custom_openapi`, dropped stale comment about
  bearerAuth (D48).
- `backend/api/rest.py` — three `# pyright: ignore[reportUnusedFunction]`
  on FastAPI handler decorations.
- `backend/models/_component_base.py`, `aad.py`, `container.py`,
  `dropzone.py`, `jump.py`, `jumper.py`, `main.py`, `reserve.py`,
  `rig.py` — 30 `Field(default_factory=list)` → `[]` conversions.
- `backend/observability/logging.py` — typed
  `CorrelationIdMiddleware.__init__` and `__call__` against
  `starlette.types.{ASGIApp,Scope,Receive,Send,Message}`.
- `backend/storage/bootstrap.py` — replaced direct lxml use with
  `xml_validate_schema_file(xsd_path)` from `validator.py`.
- `backend/storage/manifest.py` — replaced direct `etree.QName`
  use with `namespace_of(...)` helper. Two sites.
- `backend/storage/verify.py` — same `namespace_of(...)`
  substitution; `issues: list[VerifyIssue] = field(default_factory=list[VerifyIssue])`.
- `backend/services/dropzone_service.py` — `params: tuple[int, ...]`
  type-arg.
- `backend/services/jumper_service.py` — deleted dead
  `_jumper_xml_path`; refactored `create_jumper` and `update_jumper`
  to use `Jumper.model_validate({...})`; `Sequence[object]` for the
  credential-collections iteration; `Sequence` added to
  `collections.abc` import.
- `backend/services/jumper_credential_service.py` — five
  `# pyright: ignore[reportPrivateUsage]` annotations on
  cross-service helper accesses; `**updates: object` annotation.
- `backend/services/reindex_service.py` — added `import sqlite3`;
  typed `conn: sqlite3.Connection` on `_reindex_dropzones` and
  `_reindex_jumper_credentials`; four dataclass list-default
  conversions.
- `backend/services/rig_service.py` — added `Callable` import;
  introduced `_GetterFn` and `_AssignerFn` type aliases; typed
  `_COMPONENT_REGISTRY` and `_validate_component_for_assignment`'s
  `getter` parameter; deleted dead `_rig_folder`.
- `backend/services/stats_service.py` — two dataclass list-default
  conversions.
- `backend/scripts/launch_desktop.py` — `# pyright: basic` pragma at
  the top; deleted dead `_reveal_path`; three
  `# pyright: ignore[reportMissingImports]` on `import webview`.
- `backend/xml/validator.py` — added `XMLElement` and `XMLSchema`
  type aliases; updated public signatures to use them; added
  `namespace_of(element) -> str` helper; added
  `validate_schema_file(xsd_path) -> None` helper.
- `backend/xml/serialize.py` — imported `XMLElement` from
  `validator`; sed-replaced 42 `etree._Element` → `XMLElement`
  occurrences in public signatures and helper signatures.

Configuration:
- `pyproject.toml` — `[tool.pyright]` block reshaped to Option 2;
  `pyright>=1.1.400` added to `[project.optional-dependencies] dev`.
- `.github/workflows/ci.yml` — pyright step added between ruff and
  pytest; on every matrix cell.

Documentation:
- `CLAUDE.md` §7 — green-light triple updated; Option 2 policy
  summarized.
- `DECISIONS.md` — D51 appended.
- `reviews/2026-04-29-pyright-rollout.md` — this document.

## What's next

DEP-2 is closed. Audit dashboard at
`reviews/2026-04-29-tech-debt-audit.html` is the source of truth
for what's left in v0.1. The forward path per HANDOFF.md §"Ordering
recommendation":

1. **TEST-1** — crash-path tests for `update_jump` + `delete_jump`.
2. **ARCH-4** — implement D50's writer lock.
3. **Phase 3 hygiene cluster** — CODE-3 / CODE-5 / CODE-7 / CODE-8.
4. **INFRA-6** — first-run folder picker.
5. **INFRA-5** — PyInstaller spec.

The morning scheduled task (2026-04-30 08:00 EDT) is now cancelled;
HANDOFF.md is up to date as of the close of this session.
