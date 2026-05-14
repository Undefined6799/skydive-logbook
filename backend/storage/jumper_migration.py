"""Idempotent migration: ``jumpers/<uuid>.xml`` → ``jumpers/<uuid>/jumper.xml`` (D47, C.1).

The pre-D47 jumper layout was a single flat file per jumper at
``logbook_root/jumpers/<uuid>.xml``. D47 elevates it to a
folder-with-manifest mirroring the rig folder pattern (D33):

    logbook_root/
      jumpers/
        <uuid>/
          jumper.xml
          SHA256SUMS
          attachments/

This module owns the one-time-and-then-some migration that converts
every legacy flat file into the new shape. It is idempotent and
crash-resistant — safe to call on every bootstrap (D29). A partial-
migration crash leaves a deterministic intermediate state that
re-running the migration completes without data loss.

The migration is filesystem-level: it moves the bytes verbatim,
generates the SHA256SUMS manifest from the on-disk content, and
creates the empty ``attachments/`` subfolder. It does NOT touch
Pydantic models. Validation is structural only — the hardened
parser + XSD confirm the legacy file isn't garbage before promoting
it. If the legacy file is corrupt the migration logs a WARNING and
leaves it in place; ``verify`` will surface it as an orphan and the
user can clean it up via the file manager. Blocking other jumpers
because one is corrupt would be worse.

Why this lives in storage/ rather than services/: the migration
moves bytes and manages folder shape. It needs the hardened parser
and validator (in xml/) and the manifest helper (in storage/), but
no Pydantic types. Keeping it out of services/ also lets bootstrap
(in storage/) call it without dragging the service layer into the
boot path.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..xml.validator import XMLError, validate
from ..xml.validator import parse as xml_parse
from . import manifest as _manifest
from .filesystem import atomic_write

JUMPERS_DIRNAME = "jumpers"
"""Top-level subdirectory that holds every jumper folder."""

JUMPER_XML_NAME = "jumper.xml"
"""Filename of the authoritative jumper document inside a jumper folder."""

ATTACHMENTS_DIRNAME = "attachments"
"""Subfolder name where credential card / medical PDFs live."""

_logger = logging.getLogger("backend.storage.jumper_migration")


def migrate_one_jumper(legacy_xml_path: Path) -> bool:
    """Migrate one ``jumpers/<uuid>.xml`` to the folder shape.

    Steps, in order, with crash-resistance noted at each:

    1. Compute the target folder ``jumpers/<uuid>/``.
    2. If the folder already contains ``jumper.xml``, this is either a
       no-op (already migrated, legacy already gone) or a cleanup
       (legacy survived a previous crash). Unlink the legacy file if
       it exists and return.
    3. If the legacy file isn't there, there's nothing to migrate;
       return False.
    4. Read legacy bytes. Parse + XSD-validate them through the
       hardened pipeline. If validation fails, log a WARNING and
       return False — the file stays in place, ``verify`` flags it,
       the user fixes manually.
    5. ``mkdir`` the folder + ``attachments/`` subfolder
       (``exist_ok=True`` for both).
    6. ``atomic_write`` the legacy bytes to ``jumper.xml`` inside the
       folder. Bytes are unchanged from the legacy file, so no
       round-trip risk.
    7. ``manifest.generate(folder)`` and ``atomic_write`` to
       ``SHA256SUMS``. Generate (write-path manifest) is correct
       here per D25 — we just wrote the bytes, hashing what we see
       on disk equals hashing what we wrote.
    8. ``unlink`` the legacy file. After this point the migration is
       complete from the disk-shape perspective. A crash before this
       step leaves both the new folder and the legacy file; the next
       call enters at step 2 and unlinks the legacy.

    Returns True if disk state changed (full migration OR cleanup of
    a stale legacy), False if no work was done (already migrated and
    legacy already gone, or the legacy file was corrupt and skipped).
    """
    jumper_id = legacy_xml_path.stem
    folder = legacy_xml_path.parent / jumper_id
    folder_xml = folder / JUMPER_XML_NAME

    # Case 1: folder already migrated. Clean up a stale legacy if it
    # survived a previous half-failed migration.
    if folder_xml.is_file():
        if legacy_xml_path.exists():
            legacy_xml_path.unlink()
            _logger.info(
                "jumper_legacy_cleanup",
                extra={
                    "jumper_id": jumper_id,
                    "removed": str(legacy_xml_path),
                },
            )
            return True
        return False

    # Case 2: nothing to migrate (legacy gone, folder didn't get a
    # jumper.xml — likely a half-failed migration from an earlier
    # call where the read or write itself failed before the unlink).
    if not legacy_xml_path.is_file():
        return False

    # Case 3: real migration. Read legacy bytes, validate, write new
    # shape. Validate first so a corrupt legacy doesn't poison the
    # new layout.
    legacy_bytes = legacy_xml_path.read_bytes()
    try:
        element = xml_parse(legacy_bytes)
        validate(element)
    except XMLError as exc:
        _logger.warning(
            "jumper_migration_skip_invalid",
            extra={
                "path": str(legacy_xml_path),
                "reason": str(exc),
            },
        )
        return False

    # Create folder + empty attachments/ subfolder. Both are
    # idempotent so re-running after a partial crash is safe.
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ATTACHMENTS_DIRNAME).mkdir(exist_ok=True)

    # Write jumper.xml first. Crash here: folder + attachments/ exist
    # but no jumper.xml; next call re-enters Case 3 (legacy still
    # present, folder_xml not a file) and re-attempts.
    atomic_write(folder_xml, legacy_bytes)

    # Generate manifest from the on-disk shape. Only jumper.xml is in
    # the manifest at this point — the attachments/ subfolder is
    # empty. Crash between this write and the next leaves a manifest
    # that matches reality plus the legacy file still on disk; next
    # call enters Case 1 and cleans up.
    manifest_bytes = _manifest.generate(folder)
    atomic_write(folder / _manifest.MANIFEST_NAME, manifest_bytes)

    # Last step: drop the legacy file. After this the migration is
    # done.
    legacy_xml_path.unlink()

    _logger.info(
        "jumper_migrated",
        extra={
            "jumper_id": jumper_id,
            "from": str(legacy_xml_path),
            "to": str(folder_xml),
        },
    )
    return True


def migrate_all_jumpers(logbook_root: Path) -> int:
    """Migrate every legacy ``jumpers/*.xml`` flat file to folder shape.

    Walks ``logbook_root/jumpers/`` for top-level ``*.xml`` files —
    each is a pre-C.1 jumper. Subdirectories are left alone (they
    are already in the folder shape; their internal manifests are
    not regenerated by this function).

    Returns the count of state-changes this call made: full
    migrations + stale-legacy cleanups. A fresh logbook with no
    jumpers returns 0. Re-running after a successful migration also
    returns 0 (idempotent steady state).

    Bootstrap calls this every time it runs (D29 says bootstrap is
    idempotent and safe to re-run; the migration matches that
    posture). Tests can call it directly to exercise migration
    paths without the rest of bootstrap.
    """
    folder = logbook_root / JUMPERS_DIRNAME
    if not folder.is_dir():
        # Pre-bootstrap state — nothing to migrate. The bootstrap
        # call that creates the directory will be followed by another
        # migration call (or this one, if reordered) on a later run.
        return 0

    changes = 0
    # Sort for deterministic log order on a logbook with many legacy
    # files; the migration itself doesn't depend on order.
    for entry in sorted(folder.iterdir()):
        if (
            entry.is_file()
            and entry.suffix == ".xml"
            and migrate_one_jumper(entry)
        ):
            changes += 1
    return changes
