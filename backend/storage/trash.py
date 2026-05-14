"""Soft delete to `<logbook_root>/.trash/` (D19).

Moves a jump folder into `.trash/` instead of removing it. The name
includes a UTC timestamp so multiple deletions of re-created jumps don't
collide.

**Timestamp form.** Trash folder names use the **basic** ISO 8601
shape ``YYYYMMDDTHHMMSS.fffZ`` rather than D17's canonical
``YYYY-MM-DDTHH:MM:SS.fffZ`` form. Folder names cannot contain ``:``
on Windows (D4 ``_FORBIDDEN_CHARS`` rejects it on every platform for
portability), so D17's exact form is structurally illegal here. The
basic form keeps the same precision (UTC, millisecond, ``Z``-suffix)
without separators that filesystems forbid. Every other code path
(XML attributes, SQLite columns, log records) uses the canonical
D17 form via ``_now_utc_iso()``; this module is the one documented
exception, scoped to filesystem-name use only.
"""
from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

TRASH_DIRNAME = ".trash"


def _now_utc_basic_iso() -> str:
    """UTC timestamp in basic ISO 8601 with millisecond precision.

    Produces ``20260429T214832.412Z`` — same precision as the
    canonical D17 form ``2026-04-29T21:48:32.412Z`` minus the
    separators that D4's filesystem-name rules forbid (``:`` and
    leading-zero-padded date dashes are not strictly forbidden, but
    keeping the basic form throughout makes the trash-name shape
    parseable as a single token).
    """
    now = datetime.now(UTC)
    return f"{now.strftime('%Y%m%dT%H%M%S')}.{now.microsecond // 1000:03d}Z"


def soft_delete(
    folder: Path,
    logbook_root: Path,
    subdir: str | None = None,
) -> Path:
    """Move ``folder`` to ``<logbook_root>/.trash[/<subdir>]/<timestamp>_<name>/``.

    Returns the new path. If the source doesn't exist, raises ``FileNotFoundError``.
    If the destination already exists (extremely unlikely), a counter is added.

    The optional ``subdir`` keeps the trash organized when several
    folder-shaped entities live alongside one another. Jumps land flat
    (``.trash/<ts>_<name>/``) because they're the original folder-with-
    manifest entity and the existing v0.1 logbooks already use that
    shape; rigs (R.2.0c.ii) pass ``subdir="rigs"`` so they trash to
    ``.trash/rigs/<ts>_<nickname>/`` and don't crowd the top of
    ``.trash/`` with rig folders.
    """
    folder = Path(folder)
    logbook_root = Path(logbook_root)
    if not folder.is_dir():
        raise FileNotFoundError(f"not a directory: {folder}")

    trash_root = logbook_root / TRASH_DIRNAME
    trash = trash_root if subdir is None else trash_root / subdir
    trash.mkdir(parents=True, exist_ok=True)

    stamp = _now_utc_basic_iso()
    target = trash / f"{stamp}_{folder.name}"
    counter = 1
    while target.exists():
        target = trash / f"{stamp}_{folder.name}_{counter}"
        counter += 1

    shutil.move(str(folder), str(target))
    return target


def soft_delete_file(file_path: Path, logbook_root: Path, subdir: str) -> Path:
    """Move a single file (e.g. ``dropzones/<uuid>.xml``) into the trash.

    Per D44, flat-file entities (dropzones in R.D.1, plus jumpers /
    components when D33's R.0 lands) trash to a per-deletion folder
    so the original filename survives recoverable. Concretely:

      ``dropzones/<uuid>.xml`` →
      ``.trash/<subdir>/<timestamp>_<filename>/<filename>``

    The per-deletion folder lets a later restore put the file back at
    its original name without colliding with a re-created entity that
    happens to share the UUID. Same shape as the jump-folder soft
    delete, just nested.

    Returns the new path of the file inside ``.trash``. Raises
    ``FileNotFoundError`` if the source doesn't exist.
    """
    file_path = Path(file_path)
    logbook_root = Path(logbook_root)
    if not file_path.is_file():
        raise FileNotFoundError(f"not a file: {file_path}")

    trash = logbook_root / TRASH_DIRNAME / subdir
    trash.mkdir(parents=True, exist_ok=True)

    stamp = _now_utc_basic_iso()
    folder = trash / f"{stamp}_{file_path.name}"
    counter = 1
    while folder.exists():
        folder = trash / f"{stamp}_{file_path.name}_{counter}"
        counter += 1
    folder.mkdir()

    target = folder / file_path.name
    shutil.move(str(file_path), str(target))
    return target


def restore(trashed_folder: Path, destination: Path) -> Path:
    """Move a trashed folder back to `destination`. Fails if destination exists."""
    trashed_folder = Path(trashed_folder)
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(f"cannot restore: {destination} already exists")
    shutil.move(str(trashed_folder), str(destination))
    return destination
