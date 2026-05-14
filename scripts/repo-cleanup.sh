#!/usr/bin/env bash
#
# Phase 1 repo-hygiene cleanup — the file-system actions an agent
# sandbox can't perform. Review the planned changes first, then run.
#
# What this does:
#
#   * Deletes 55 zero-byte ``.bak*`` files under ``frontend/src/``.
#   * Deletes 122 transient Vite + Vitest config-timestamp files
#     under ``frontend/`` (43 ``vite.config.js.timestamp-*.mjs`` and
#     79 ``vitest.config.js.timestamp-*.mjs``).
#   * Deletes ``.DS_Store`` at repo root and ``backend/``.
#   * Deletes the empty ``pytest-cache-files-*/`` directory at root.
#   * Deletes 6 inert frontend test stubs whose only contents are
#     ``it.skip(...)`` placeholders (``frontend/test/sanity.test.js``
#     plus 4 "superseded" stubs plus ``profile.test.jsx``); the
#     remaining real tests in ``frontend/test/`` are untouched.
#   * Moves ``HANDOFF.md`` → ``docs/internal/session-handoffs/2026-04-30.md``.
#   * Moves ``reviews/2026-04-29-progress.html``, ``2026-04-29-tech-debt-audit.html``,
#     ``2026-04-30-progress.html`` → ``docs/reviews/``.
#   * Moves ``ui-mockup.html`` → ``docs/mockups/ui-mockup.html``.
#   * Removes the four stale virtual environments
#     ``.venv-fresh``, ``.venv-linux``, ``.venv-sandbox``, ``.venv-test``
#     (closes audit item INFRA-8 from 2026-04-30 — see HANDOFF.md). The
#     canonical ``.venv`` is preserved.
#
# What this does NOT do:
#
#   * Touch any tracked source file in ``backend/`` or
#     ``frontend/src/`` (other than the zero-byte ``.bak*`` files).
#   * Run any tests. Verify with the standard triple after:
#
#         uv run ruff check backend
#         uv run pyright backend
#         uv run pytest backend/tests
#         (cd frontend && npm test)
#
# Safety posture:
#
#   * Refuses to run outside the repo root (checks for ``DECISIONS.md``).
#   * Uses ``set -euo pipefail`` so any failure stops the script.
#   * Each block prints what it deleted; no silent ``rm -rf`` of
#     unspecified paths.
#   * Idempotent — re-running after a clean run is a no-op.
#
# Rationale for every file class deleted lives in:
#   reviews/2026-05-14-tech-debt-audit.md §5 (repo hygiene)
#   reviews/2026-05-14-second-opinion.md Part 5 Phase 1

set -euo pipefail

# --------------------------------------------------------------------- #
# Pre-flight: must run from repo root.
# --------------------------------------------------------------------- #

if [[ ! -f DECISIONS.md || ! -f pyproject.toml ]]; then
  echo "error: run this from the skydive-logbook repo root" >&2
  echo "       (cwd missing DECISIONS.md and/or pyproject.toml)" >&2
  exit 1
fi

echo "==> Running phase 1 repo cleanup from $(pwd)"
echo

# --------------------------------------------------------------------- #
# 1. Delete zero-byte .bak* files under frontend/src/.
#    Defensive: assert size 0 per file before delete so a real backup
#    accidentally matching the pattern is preserved.
# --------------------------------------------------------------------- #

echo "==> 1/8  Deleting zero-byte .bak* files under frontend/src/"
bak_count=0
while IFS= read -r -d '' f; do
  if [[ -s "$f" ]]; then
    echo "    SKIP (not zero bytes): $f" >&2
    continue
  fi
  rm -f -- "$f"
  bak_count=$((bak_count + 1))
done < <(find frontend/src -type f -name '*.bak*' -print0)
echo "    deleted: $bak_count file(s)"
echo

# --------------------------------------------------------------------- #
# 2. Delete Vite/Vitest transient timestamp configs.
# --------------------------------------------------------------------- #

echo "==> 2/8  Deleting Vite/Vitest config-timestamp files in frontend/"
ts_count=0
while IFS= read -r -d '' f; do
  rm -f -- "$f"
  ts_count=$((ts_count + 1))
done < <(find frontend -maxdepth 1 -type f \
  \( -name 'vite.config.js.timestamp-*.mjs' \
  -o -name 'vitest.config.js.timestamp-*.mjs' \) -print0)
echo "    deleted: $ts_count file(s)"
echo

# --------------------------------------------------------------------- #
# 3. Delete .DS_Store leftovers (gitignored, but still on disk).
# --------------------------------------------------------------------- #

echo "==> 3/8  Deleting .DS_Store files"
ds_count=0
while IFS= read -r -d '' f; do
  rm -f -- "$f"
  ds_count=$((ds_count + 1))
done < <(find . -type f -name '.DS_Store' \
  -not -path './node_modules/*' \
  -not -path './frontend/node_modules/*' \
  -not -path './.venv*/*' \
  -print0)
echo "    deleted: $ds_count file(s)"
echo

# --------------------------------------------------------------------- #
# 4. Delete the stray pytest cache directory at root.
#    The canonical .pytest_cache/ is already gitignored; this is a
#    different numeric-suffix form left by an old plugin run.
# --------------------------------------------------------------------- #

echo "==> 4/8  Deleting pytest-cache-files-*/"
cache_dir_count=0
for d in pytest-cache-files-*/; do
  [[ -d "$d" ]] || continue
  rm -rf -- "$d"
  cache_dir_count=$((cache_dir_count + 1))
done
echo "    deleted: $cache_dir_count directory(ies)"
echo

# --------------------------------------------------------------------- #
# 5. Delete six inert frontend test stubs.
#    These are 3-8 LOC files containing only ``it.skip(...)`` markers.
#    The real frontend tests (views.smoke, identityEditFull/Orchestrator,
#    d60-starred-dropzone) are NOT in this list.
# --------------------------------------------------------------------- #

echo "==> 5/8  Deleting inert frontend test stubs"
stub_count=0
for f in \
  frontend/test/sanity.test.js \
  frontend/test/api-import.test.js \
  frontend/test/careerstats-import.test.js \
  frontend/test/import-only.test.js \
  frontend/test/lucide.test.js \
  frontend/test/profile.test.jsx
do
  if [[ -f "$f" ]]; then
    rm -f -- "$f"
    stub_count=$((stub_count + 1))
  fi
done
echo "    deleted: $stub_count file(s)"
echo

# --------------------------------------------------------------------- #
# 6. Move HANDOFF.md to docs/internal/session-handoffs/.
#    Internal session-to-session notes shouldn't sit at repo root in
#    a public repo. Preserves the file by date.
# --------------------------------------------------------------------- #

echo "==> 6/8  Moving HANDOFF.md → docs/internal/session-handoffs/2026-04-30.md"
mkdir -p docs/internal/session-handoffs
if [[ -f HANDOFF.md ]]; then
  mv HANDOFF.md docs/internal/session-handoffs/2026-04-30.md
  echo "    moved"
else
  echo "    SKIP (already moved or never present)"
fi
echo

# --------------------------------------------------------------------- #
# 7. Move review HTML reports + ui-mockup.html into docs/.
#    The .md sources stay in reviews/. The rendered HTML versions move
#    to docs/reviews/ so a stranger landing on the repo doesn't think
#    we ship build artefacts.
# --------------------------------------------------------------------- #

echo "==> 7/8  Moving review HTML reports + ui-mockup.html into docs/"
mkdir -p docs/reviews docs/mockups
moved=0
for f in \
  reviews/2026-04-29-progress.html \
  reviews/2026-04-29-tech-debt-audit.html \
  reviews/2026-04-30-progress.html
do
  if [[ -f "$f" ]]; then
    mv "$f" "docs/reviews/$(basename "$f")"
    moved=$((moved + 1))
  fi
done
if [[ -f ui-mockup.html ]]; then
  mv ui-mockup.html docs/mockups/ui-mockup.html
  moved=$((moved + 1))
fi
echo "    moved: $moved file(s)"
echo

# --------------------------------------------------------------------- #
# 8. Remove stale .venv-* directories (INFRA-8 from 2026-04-30 audit).
#    Canonical .venv/ is preserved. If you've been running tests with
#    a different name, comment out the matching line below.
# --------------------------------------------------------------------- #

echo "==> 8/8  Removing stale .venv-* directories (INFRA-8 closeout)"
venv_count=0
for d in .venv-fresh .venv-linux .venv-sandbox .venv-test; do
  if [[ -d "$d" ]]; then
    rm -rf -- "$d"
    venv_count=$((venv_count + 1))
    echo "    removed: $d"
  fi
done
echo "    deleted: $venv_count directory(ies)"
echo

# --------------------------------------------------------------------- #
# Done. Show a short summary the user can spot-check.
# --------------------------------------------------------------------- #

echo "==> Cleanup complete."
echo
echo "Verify with:"
echo "  uv run ruff check backend"
echo "  uv run pyright backend"
echo "  uv run pytest backend/tests"
echo "  (cd frontend && npm test)"
echo
echo "Then commit. Suggested message:"
echo "  chore: phase 1 repo hygiene — remove bak/timestamp cruft, organize docs/"
