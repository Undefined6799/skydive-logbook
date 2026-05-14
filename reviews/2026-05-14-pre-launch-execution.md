# Pre-launch execution — 2026-05-14

**Scope:** phases 1–3 of the GitHub-ready roadmap from
[`2026-05-14-second-opinion.md`](2026-05-14-second-opinion.md).

**Mode:** sandbox-permitted file edits and new files landed in this
session. File deletes and moves are deferred to a single shell script
(`scripts/repo-cleanup.sh`) the user runs locally — same constraint
pattern that closed INFRA-8 in the 2026-04-30 audit.

## What landed in this session

### Phase 1 — repo hygiene

- `.gitignore` extended with four new patterns:
  - `frontend/vitest.config.*.timestamp-*.mjs` (the missing Vitest
    twin of the existing Vite line)
  - `*.bak`, `*.bak[0-9]`, `*.bak[0-9][0-9]` (covers the 55
    zero-byte `.bak*` files under `frontend/src/`)
  - `pytest-cache-files-*/` (the stray cache directory at root)
- New file: `scripts/repo-cleanup.sh` (231 lines, executable, syntax-
  validated via `bash -n`). The script is idempotent, refuses to run
  outside the repo root, asserts each `.bak*` is zero bytes before
  deleting, and prints per-step counts. It closes:
  - 55 `.bak*` files in `frontend/src/`
  - 79 + 43 = 122 Vite/Vitest config-timestamp files in `frontend/`
  - 2 `.DS_Store` (root, `backend/`)
  - 1 `pytest-cache-files-*/` directory
  - 6 inert frontend test stubs (`sanity`, `api-import`,
    `careerstats-import`, `import-only`, `lucide`, `profile`)
  - Move: `HANDOFF.md` → `docs/internal/session-handoffs/2026-04-30.md`
  - Move: 3 review HTML reports → `docs/reviews/`
  - Move: `ui-mockup.html` → `docs/mockups/`
  - Delete: 4 stale venvs (`.venv-fresh`, `.venv-linux`,
    `.venv-sandbox`, `.venv-test`) — closes INFRA-8

### Phase 2 — doc-truth alignment

Four files edited (all syntax-valid):

| File | Change |
|---|---|
| `backend/services/_write_lock.py` | Docstring rewritten. Old text said "Reads do not acquire the lock"; new text acknowledges that reads-that-may-write-via-reconcile DO acquire the lock, and explains why D50's earlier framing predated D25 reconcile-on-open. |
| `backend/services/stats_service.py` | Docstring flags the known performance debt: v4 schema (D26, 2026-04-28) added the columns `compute_stats` needs but the function still walks XML. Points at the second-opinion's Phase 5 fix. |
| `backend/api/rest.py` | Replaced two `__import__("sys").stderr` calls with a module-top `import sys`. |
| `backend/xml/validator.py` | Documented `_load_schema`'s LRU-cache lifetime: keyed on Path only, not mtime. Notes the invalidation gap is unreachable today (bootstrap writes XSDs once, never mutates), with a one-line fix if a future slice needs hot-reloading XSDs. |

### Phase 3 — public-facing files

| File | Purpose |
|---|---|
| `README.md` (rewritten) | 13-section consensus structure: tagline, badges, hero placeholder, features list, status banner, principles, stack, on-disk layout, verifying-your-data, install/run (with port `8765` corrected from the previous `8000`), architecture diagram + link, roadmap, contributing/security/license pointers. |
| `CONTRIBUTING.md` (new, 166 lines) | Pre-alpha-honest, explains the small-increments rule, advertises the `DECISIONS.md` discipline, lists v0.1 deferred items, dev setup with `uv sync --extra dev`, the green-light triple, commit conventions. |
| `SECURITY.md` (new, 120 lines) | Plain-language threat model (single-user, loopback-only per D48), GitHub private vulnerability reporting flow, list of defences (hardened XML parser, path safety, atomic writes, manifest integrity, CVE-pinned floors), in-scope / out-of-scope. |
| `CODE_OF_CONDUCT.md` (new, 150 lines) | Contributor Covenant **3.0** (released 28 July 2025) — not 2.1. Includes the "addressing and repairing harm" section that replaces the old enforcement ladder. `[REPORTING ADDRESS]` placeholder flagged for user. |
| `.github/ISSUE_TEMPLATE/bug_report.yml` | YAML issue form. Required fields: what, repro, expected, actual, version, OS, Python. Optional: logs. Pre-submission checks (search dupes, not a security issue). |
| `.github/ISSUE_TEMPLATE/feature_request.yml` | YAML issue form. Required: problem, proposal. Optional: alternatives, audience. Includes D14-deferred-list checkbox. |
| `.github/ISSUE_TEMPLATE/config.yml` | `blank_issues_enabled: false`. Two contact links: GitHub Security advisories (replaces public issues for vulns) and Discussions (replaces issues for questions/ideas). |
| `.github/pull_request_template.md` | Summary, linked issue, changes, D-entry impact (4-option checkbox), verification (ruff/pyright/pytest/vitest checkbox), backwards-compatibility (additive/breaking/none). |

### What was NOT touched

- `.github/workflows/ci.yml` — verified shasum unchanged. The 3 OS × 3
  Python matrix and the Node 20 Vitest job stay exactly as they were.
- Every file under `backend/` and `frontend/src/` other than the four
  Phase-2 docstring/import edits.
- The XSD, the SQLite schema, any service-layer behaviour, any model.

## What requires your action

These items can't be done from the agent sandbox.

### Mandatory before publishing

1. **Run the cleanup script locally**:

    ```bash
    cd path/to/skydive-logbook
    less scripts/repo-cleanup.sh   # review what it will delete/move
    bash scripts/repo-cleanup.sh
    ```

2. **Verify the green-light triple** after the cleanup runs and after
   the Phase 2 docstring/import changes:

    ```bash
    uv sync --extra dev
    uv run ruff check backend
    uv run pyright backend
    uv run pytest backend/tests
    (cd frontend && npm test)
    ```

   I expect every check to pass — the Phase 2 edits were all
   docstring + module-top `import sys`. If something fails, the most
   likely cause is an import-order rule in ruff (`I`) catching the
   new `import sys` line; ruff's autofix `--fix` resolves it
   trivially.

3. **Replace placeholder tokens**:
   - In `README.md`: three `OWNER` tokens in badge URLs and the
     `git clone` line. Replace with your GitHub username/org.
   - In `.github/ISSUE_TEMPLATE/config.yml`: two `REPLACE_WITH_OWNER`
     tokens in the security advisories and discussions URLs.
   - In `CODE_OF_CONDUCT.md`: the `[REPORTING ADDRESS]` placeholder.
     A real email is required — shipping with the template
     placeholder is the most common "copied without reading" tell.

4. **Enable GitHub features** in repo settings:
   - **Security** → **Private vulnerability reporting** → Enable.
     This is what makes the "Report a vulnerability" button work and
     what `SECURITY.md` directs reporters to.
   - **Discussions** → Enable. The issue-template config points at
     Discussions for questions; if Discussions are off, that link
     404s.

### Strongly recommended before publishing

5. **Capture a hero GIF**. The README has a commented-out
   `![hero]` line at the top. Capture a 5–10 second clip of the Log
   Jump flow (Kap on macOS, LICEcap on Windows, Peek on Linux),
   target ≤2 MB and ≤1200 px wide, save to `docs/assets/hero.gif`,
   uncomment the `<!-- -->` block in the README.

6. **Phase 4 — split `DECISIONS.md` to `docs/adr/`**. Yesterday's
   second-opinion calls this the single highest-leverage structural
   change to fit in before launch. Mechanical work, ~half a day. The
   payoff: a stranger landing on the repo can read one ADR in 60
   seconds; today they ctrl-F through 342 KB.

### Optional before publishing

7. **Rename `CLAUDE.md` → `AGENTS.md`** if you want to adopt the
   emerging tool-agnostic convention at [agents.md](https://agents.md).
   The content stands on its own either way.

8. **Verify the badges actually render** after replacing OWNER —
   click each one on the rendered README, confirm it leads somewhere
   real.

## Audit-trail summary

This document plus the two from earlier this week are now your
pre-launch evidence pack. Read in order:

1. [`reviews/2026-05-14-tech-debt-audit.md`](2026-05-14-tech-debt-audit.md) — what's broken, with file:line citations.
2. [`reviews/2026-05-14-second-opinion.md`](2026-05-14-second-opinion.md) — design review against 2026 consensus + 9-phase roadmap.
3. [`reviews/2026-05-14-pre-launch-execution.md`](2026-05-14-pre-launch-execution.md) — this file. What shipped, what's user-action.

After Phase 4 ships, this trio is enough to defend the project's
shape to any contributor who shows up post-launch.
