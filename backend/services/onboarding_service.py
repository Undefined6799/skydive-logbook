"""Onboarding service — wizard state + sentinel (D64).

Owns two responsibilities:

  1. **State read.** :func:`get_state` returns whether the user has
     completed the wizard (sentinel present) and whether the logbook
     already has a jumper / dropzone / rig. The SPA uses the combined
     answer to decide between (wizard) / (resumption banner) / (no UI).
  2. **State write.** :func:`complete` stamps the sentinel JSON
     document at ``<root>/.onboarding_completed`` with the current
     timestamp and the supplied :class:`OnboardingStatus`. Subsequent
     reads see ``completed=True`` and the wizard never reopens.

The sentinel is plain JSON written through ``atomic_write`` (D10).
Its presence is load-bearing; its body is informational. A malformed
sentinel still counts as "completed" — we log the parse failure but
do not fall back to "show the wizard again", because re-opening the
wizard after a user said "done" would be a worse failure mode than
displaying ``null`` timestamps in Settings.

Per D64 this is the smallest viable per-logbook state layer. A
``settings.xml`` with an XSD becomes worthwhile when a second flag
arrives (the migration is one read of the sentinel + one write into
the new file).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import cast

from ..models.onboarding import OnboardingComplete, OnboardingState, OnboardingStatus
from ..storage.filesystem import atomic_write
from ..storage.index import open_index
from ._timestamps import now_utc_iso
from ._write_lock import with_writer_lock

_SENTINEL_NAME = ".onboarding_completed"
_RIGS_DIR = "rigs"
_JUMPERS_DIR = "jumpers"

_logger = logging.getLogger("backend.services.onboarding")


def _sentinel_path(logbook_root: Path) -> Path:
    """Resolve the sentinel file's location at the logbook root.

    ``_SENTINEL_NAME`` is a module-level constant, not user input,
    so CLAUDE.md §5's "every path derived from user input goes
    through ``safe_join``" invariant does not apply here. The bare
    ``/`` operator is correct — adding ``safe_join`` would be a
    misleading copy of the pattern used for entity files (where
    the leaf name IS user-derived).
    """
    return logbook_root / _SENTINEL_NAME


def _read_sentinel(logbook_root: Path) -> tuple[bool, str | None, OnboardingStatus | None]:
    """Return ``(completed, completed_at, status)`` from the sentinel.

    ``completed`` is True iff the file exists. ``completed_at`` and
    ``status`` are best-effort parses of the JSON body — either may
    be ``None`` even when ``completed`` is True, if the file was
    truncated, hand-edited, or written by a future version with an
    incompatible shape. The wizard never re-opens because of a parse
    failure (see module docstring).
    """
    path = _sentinel_path(logbook_root)
    if not path.is_file():
        return (False, None, None)

    try:
        raw = path.read_bytes()
    except OSError as exc:
        _logger.warning(
            "onboarding_sentinel_unreadable",
            extra={"path": str(path), "error": str(exc)},
        )
        return (True, None, None)

    try:
        # ``json.loads`` is stubbed as returning ``Any``, which leaks
        # ``Unknown`` through every downstream read under pyright's
        # strict mode. Cast the parse result to ``object`` immediately
        # so the ``isinstance`` narrows below produce concrete types.
        parsed: object = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _logger.warning(
            "onboarding_sentinel_malformed",
            extra={"path": str(path), "error": str(exc)},
        )
        return (True, None, None)

    # ``json.loads`` returns ``Any``; narrow to a typed dict so the
    # downstream reads have a known shape. A non-object payload (list,
    # string, number) collapses to "presence only" — sentinel exists,
    # but neither timestamp nor status is recoverable. Pyright's
    # ``isinstance`` narrowing produces ``dict[Unknown, Unknown]``;
    # the ``cast`` pins the value-type so per-field ``isinstance``
    # checks below return concrete types instead of ``Unknown``.
    if not isinstance(parsed, dict):
        return (True, None, None)
    body = cast(dict[str, object], parsed)

    raw_completed_at = body.get("completed_at")
    completed_at = raw_completed_at if isinstance(raw_completed_at, str) else None

    raw_status = body.get("status")
    status: OnboardingStatus | None
    if isinstance(raw_status, str):
        try:
            status = OnboardingStatus(raw_status)
        except ValueError:
            _logger.warning(
                "onboarding_sentinel_unknown_status",
                extra={"path": str(path), "status": raw_status},
            )
            status = None
    else:
        status = None

    return (True, completed_at, status)


def _has_any_dropzone(logbook_root: Path) -> bool:
    """True when the dropzones index reports ≥1 row.

    Mirrors :func:`dropzone_service._count_dropzones` but without
    requiring a service import (avoiding the writer-lock decoration
    that wraps that helper through its parent functions).

    TODO (OB.2+): if a third "has_X via SQLite" check arrives,
    factor this and ``_count_dropzones`` into a shared helper in
    ``storage.index`` rather than duplicating the open/close shape.
    Held off for now — one query in two places is below the
    threshold where abstraction pays for itself.
    """
    result = open_index(logbook_root)
    try:
        row = result.conn.execute(
            "SELECT 1 FROM dropzones LIMIT 1"
        ).fetchone()
    finally:
        result.conn.close()
    return row is not None


def _has_any_subfolder(logbook_root: Path, dir_name: str) -> bool:
    """True when ``<root>/<dir_name>/`` contains at least one subfolder.

    Used for both ``jumpers/`` and ``rigs/`` since both store records
    as ``<dir>/<id>/<…>.xml``. The check is "any entry that is a
    directory" — partial-create stubs (folder exists, inner XML
    missing) still count as "present" since the wizard's job is to
    avoid re-prompting, not to validate disk integrity.
    """
    target = logbook_root / dir_name
    if not target.is_dir():
        return False
    return any(entry.is_dir() for entry in target.iterdir())


def get_state(logbook_root: Path, user_id: str) -> OnboardingState:
    """Return the current wizard state for ``logbook_root`` (D64).

    Pure read — does not take the writer lock. The three ``has_*``
    flags are derived from disk + index at call time; the SPA polls
    on every mount so a user who logged their first jump in another
    window sees the wizard go away on next render.
    """
    del user_id  # v0.1: onboarding is per-logbook, not per-user
    completed, completed_at, status = _read_sentinel(logbook_root)
    return OnboardingState(
        completed=completed,
        completed_at=completed_at,
        status=status,
        has_jumper=_has_any_subfolder(logbook_root, _JUMPERS_DIR),
        has_dropzones=_has_any_dropzone(logbook_root),
        has_rigs=_has_any_subfolder(logbook_root, _RIGS_DIR),
    )


@with_writer_lock
def complete(
    logbook_root: Path,
    user_id: str,
    payload: OnboardingComplete,
) -> OnboardingState:
    """Stamp the sentinel and return the updated state (D64).

    Idempotent — calling twice with the same status rewrites the
    sentinel with a fresh ``completed_at`` but leaves the
    "completed" bit unchanged. Calling with a different status
    overwrites the recorded value (e.g. user skips, then later
    walks the resumption banner to "finished").

    The write goes through ``atomic_write`` (D10) so a crash mid-
    write leaves either the prior sentinel (if any) or no sentinel
    at all — never a torn file the parser can't read.
    """
    # ``user_id`` is accepted for forward compatibility (D8 — every
    # service takes it from day one) but unused in v0.1: the sentinel
    # is per-logbook, not per-user. Forwarding it into ``get_state``
    # below keeps the function-call shape uniform with the rest of
    # the service surface; that helper will ``del`` its own copy.
    body = json.dumps(
        {"completed_at": now_utc_iso(), "status": payload.status.value},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    atomic_write(_sentinel_path(logbook_root), body)

    _logger.info(
        "onboarding_completed",
        extra={"status": payload.status.value},
    )
    return get_state(logbook_root, user_id)
