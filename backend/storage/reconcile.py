"""On-open reconciliation of jump folders (D25).

A jump folder is **valid** iff ``jump.xml`` parses through the hardened
parser and validates against its declared XSD. Everything else in the
folder — ``SHA256SUMS``, ``summary.md`` — is derived and rebuildable.

``folder_reconcile`` is the cheap, idempotent repair step that runs on
the read path to heal stale or missing derived files *without* reading
attachment bytes:

  * If ``SHA256SUMS`` is missing or disagrees with ``jump.xml``'s
    claims, regenerate it from those claims via
    ``manifest.from_jump_xml`` (D25 §"Critical distinction" — do not
    use ``manifest.generate`` here).
  * ``summary.md`` regeneration is not D25's job and happens in the
    service layer on next read (D5).

What this module does **not** do:

  * Read or re-hash attachment bytes — that is ``scripts/verify.py``.
  * Fix an invalid or missing ``jump.xml`` — that is outside the
    "jump.xml is truth" contract; ``verify`` reports the folder as
    invalid and the user resolves by hand.
  * Detect orphan attachments (files present in the folder, not
    referenced by ``jump.xml``) — also ``verify``'s concern.
"""
from __future__ import annotations

import logging
from pathlib import Path

from . import manifest
from .filesystem import atomic_write

_logger = logging.getLogger("backend.storage.reconcile")


def folder_reconcile(folder: Path, logbook_root: Path | None = None) -> bool:
    """Repair derived files in ``folder`` from ``jump.xml``'s claims.

    Idempotent and cheap: one parse, one structural comparison of the
    manifest, possibly one atomic write. No attachment bytes are read.

    Returns:
      True if ``SHA256SUMS`` was (re)written during this call.
      False if the folder was already in sync with ``jump.xml``.

    Arguments:
      folder: the jump folder to reconcile.
      logbook_root: forwarded to ``manifest.from_jump_xml`` for XSD
        lookup per D18. See that function's docstring.

    Raises:
      FileNotFoundError: ``jump.xml`` is absent — "not a valid jump"
        per D25. Out of reconcile's scope.
      XMLError: the hardened parser / validator rejected
        ``jump.xml``. Folder is broken beyond reconcile's remit;
        ``verify`` will report it.
    """
    folder = Path(folder)

    # Parse + validate + build what the manifest *should* say.
    expected_bytes = manifest.from_jump_xml(folder, logbook_root=logbook_root)
    expected_entries = set(manifest.parse(expected_bytes))

    sums_path = folder / manifest.MANIFEST_NAME
    if sums_path.is_file():
        try:
            existing_entries = set(manifest.parse(sums_path.read_bytes()))
        except ValueError:
            # Malformed on-disk manifest is equivalent to "stale" —
            # rewrite from the authoritative claims.
            existing_entries = None  # type: ignore[assignment]
        if existing_entries == expected_entries:
            # Structural match: any ordering or whitespace differences
            # within ``manifest.parse``'s tolerance are ignored. Nothing
            # to do; leave the bytes untouched.
            return False

    # Missing, malformed, or out-of-sync. Write the D25-shaped manifest.
    atomic_write(sums_path, expected_bytes)
    _logger.info(
        "manifest_regenerated",
        extra={"folder": str(folder)},
    )
    return True
