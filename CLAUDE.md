# CLAUDE.md — instructions for working on skydive-logbook

This file is for *you* (Claude) whenever you're collaborating with Alex on
this project. It encodes the ground rules so every session is aligned
without having to re-derive them from context. Keep it short; put durable
rationale in `DECISIONS.md`, not here.

---

## 1. What this project is (in one paragraph)

A self-hosted, single-user skydiving logbook. Jumps and gear live
as XML files validated against a versioned XSD in a user-chosen folder
(the "logbook folder"). SQLite is a rebuildable index, not a store. A
FastAPI REST API (OpenAPI 3.1, RFC 9457 errors) wraps a thin service
layer so a future pywebview-packaged SPA and any third-party tool use
the same contract. The data is meant to outlive this app — anyone with
a text editor and an XSD validator should be able to read and verify it.

---

## 2. Source-of-truth hierarchy

When sources disagree, trust in this order:

1. **The code**, right now, on disk. If `DECISIONS.md` contradicts
   the code, the code is authoritative and the decision record is
   stale — fix the record.
2. **`DECISIONS.md`** — the D-numbered record of *why* the project
   is shaped the way it is. Always read it before making a
   cross-cutting change. Rationale and consequences live there, not
   in tribal knowledge.
3. **`ARCHITECTURE.md`** — narrative overview. Derived from D-entries
   and code; useful for orienting, not for deciding.
4. **Git history** — for recent context (who/when/why-this-commit).
5. **`README.md`** — user-facing; never a decision source.

**Never** treat a past conversation, a prior plan, or a memory entry
as more authoritative than the above. Memory is a *hint*; verify
before acting on it.

---

## 3. Working cadence (the rule)

Ship in small, logical, verified pieces. Each slice:

- touches as few files as possible for a coherent outcome
- ships with tests that actually exercise the new behaviour
- runs `pytest` and `ruff check` green before it's considered done
- updates the relevant D-entry (or adds one) if it shifts a contract

When choosing between "finish this slice" and "pause to verify",
**pause**. The feedback memory `feedback_small_increments.md` records
Alex's direct quote on this: *"we master the basics. lets carry on
the work in small piece so it is done correctly."* This is not
negotiable — it overrides any local optimization for speed.

Before starting non-trivial work, propose a phased plan and get
alignment on the *first phase*, not the whole roadmap.

---

## 4. Decision-record discipline

Before changing anything cross-cutting (storage format, error shape,
API surface, concurrency model, etc.):

1. Search `DECISIONS.md` for a relevant D-entry. If one exists,
   honour it. If the decision is wrong, supersede it with a new
   D-entry — don't edit the old one in place.
2. If no entry exists and the choice is non-obvious, **draft one
   before coding**. Even a terse decision note beats silent drift.
3. Quote the D-entry number in code comments when the code's shape
   is non-obvious and the rationale lives in the record (e.g.
   `# Per D10: atomic_write fsyncs before os.replace`).

Deferred items in `DECISIONS.md` (FlySight parsing, digital
signatures, multi-user, imports, video, mobile, headless-server,
auto-update) are **binding non-decisions**. Do not pull them into
scope without an explicit scope change from Alex.

---

## 5. Invariants you must not violate

These are load-bearing; breaking any one of them would invalidate
guarantees the rest of the system depends on.

- **Every write to disk goes through `backend/storage/filesystem.py:atomic_write`.**
  Never call `open(..., "w")` directly for persisted data. (D10.)
- **Every XML we produce is validated against the XSD before the
  atomic write.** Never write XML that hasn't passed `validator.validate`.
  (D2.)
- **Every XML we read is parsed through the hardened parser in
  `backend/xml/validator.py`.** DTDs are rejected pre-parse,
  entity expansion is off, external resources are blocked.
  Security posture is non-negotiable (see D2 consequences).
- **Every path derived from user input goes through `safe_join` +
  `sanitize_folder_name` / `sanitize_filename`.** No exceptions.
  (D4.)
- **SQLite is never authoritative.** Every field in the index must
  also exist in XML; `reindex` must be able to rebuild the DB from
  XML alone. (D3.)
- **Errors cross the API boundary as RFC 9457 `application/problem+json`
  only.** Use `backend/api/errors.py:error_response` or raise a
  `ServiceError` subclass. Never return a bare dict or a plain 500.
  (D16.)
- **Unicode is normalized to NFC on every write.** Folder names and
  filenames both. (D4.)

If a task seems to require breaking one of the above, stop and
escalate to Alex with the trade-off explicit. Don't negotiate with
yourself.

---

## 6. Module layout (and who owns what)

```
backend/
  models/       Pydantic models — the runtime + API shape, single source of truth for fields (D2)
  xml/          Serialize/parse/validate XML; hardened parser lives here
  xml/schema/   XSDs, versioned (D18)
  storage/      Filesystem primitives, lockfile, manifest, SQLite index (D3, D4, D9, D10)
  services/     Business logic. The REST adapter must be thin; logic belongs here (D7)
  api/          FastAPI app, routes, errors, OpenAPI augmentation
  scripts/      CLI entry points (reindex, verify)
  tests/        pytest; mirrors the module tree
```

When in doubt: logic goes in `services/`; the REST layer only
translates HTTP ↔ service call. (D7.)

---

## 7. Test + lint + type-check discipline

Run all three before declaring a slice done:

```
pytest
ruff check backend
pyright backend
```

CI gates the same triple on every push and PR (D51).

Tests should exercise the behaviour, not the implementation —
prefer calling through a service function to mocking its
collaborators. Integration tests for storage primitives (atomic
writes, manifest round-trips, index rebuild) must touch a real
temp directory, not mocks. (Mocking filesystem behaviour hides
cross-platform bugs.)

Crash-path tests matter here more than in most backends because
disk XML is authoritative — write a test for the half-written
case whenever you add a multi-file write.

Pyright runs in Option 2 mode per D51: strict for production code
under ``backend/services/``, ``storage/``, ``models/``, ``api/``,
``scripts/``, ``observability/``, plus ``main.py`` and ``config.py``;
basic for ``backend/tests/`` (test fixtures inherently propagate
``Unknown`` types pyright cannot pin); per-folder overrides for
``backend/xml/`` accepting lxml's incomplete stubs as the typed
boundary. Suppressions live as ``# pyright: ignore[<rule>]  # <reason>``
at the offending line — never blanket file-level disables and never
``# type: ignore`` (the mypy form). New strict-allow-list code is held
to zero pyright errors before merge.

---

## 8. Subagents

The project ships four agent personas under `.claude/agents/`:

- **`api-contract-steward`** — owns OpenAPI spec and the XSDs;
  consult for any change that alters the REST or on-disk contract.
- **`backend-engineer`** — service-layer implementation work.
- **`frontend-engineer`** — pywebview SPA (deferred per D14; not
  yet scaffolded).
- **`code-reviewer`** — second opinion on non-trivial slices.

When you delegate to a subagent, follow the rule in
`feedback_agent_thoroughness.md`: authorize unrushed work and
require citations to docs/RFCs/forums. Terse prompts produce
shallow work.

---

## 9. Memory conventions

- Memory lives in the per-space memory directory and is indexed
  by `MEMORY.md`. The types are `user`, `feedback`, `project`,
  `reference`.
- Save to memory when a fact persists across sessions. Don't save
  code patterns, file paths, or anything derivable from the repo.
- Before acting on a memory, verify the claim against current
  code (a memory naming a function is a claim it existed when
  written, not that it exists now).
- When a memory becomes stale, fix or delete it rather than
  working around it.

---

## 10. Scope (v0.1)

D14 pins v0.1 scope, with D33 superseding §3 ("equipment tracking"
→ rig manager). Keep the surface area small: jumps CRUD,
attachments, dropzones (D44), rig manager (D33; phased R.0–R.5),
basic stats, reindex, verify, lockfile, atomic writes, XSD
validation, RFC 9457 errors. Any request that drifts outside this
list — even a sensible one — needs an explicit scope change from
Alex before you spend effort on it.

---

*Last revised 2026-04-23. Keep this file short; move rationale to
`DECISIONS.md` as new D-entries.*
