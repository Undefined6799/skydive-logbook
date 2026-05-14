"""Storage helpers for jumper attachments (D47, Phase C.2).

Each jumper folder owns an ``attachments/`` subfolder where
credential cards and medical certificates live as files:

    logbook_root/jumpers/<jumper_id>/
      jumper.xml
      SHA256SUMS
      attachments/
        <attachment_uuid>__<safe-filename>.<ext>

The composed disk filename encodes the attachment UUID as a prefix
followed by the user-facing filename, separated by ``__`` (two
underscores). The prefix lets the recovery path identify each file
by its UUID alone — even if ``jumper.xml`` is missing or corrupt
the on-disk filename tells the verifier which attachment record the
file *should* match. The user-facing portion is preserved as-typed
(modulo D4 sanitization) so a human looking at the folder still
sees recognizable names.

Why not flat names like jump attachments use: jump attachments are
keyed on the user filename inside ``jump.xml`` and live next to the
XML in the same folder — there is no parallel UUID to track. Jumper
attachments are referenced by ``card_attachment_id`` (a UUID), so
the disk shape needs to surface that UUID for lookups and recovery.

This module is the pure-function storage layer. It does not touch
Pydantic models, the manifest, or ``jumper.xml``. Composition with
those lives in the C.3 service slice.

Crash semantics inherited from ``atomic_write_stream``: bytes go to
``<dest>.tmp``, fsync, ``os.replace`` into place. A crash mid-write
leaves the previous destination untouched and may leave a stray
``.tmp`` for cleanup on next write or by ``verify``. Streaming +
incremental SHA-256 is required because credential card files
(especially scanned photos) can be multi-megabyte and we do not
want to buffer them fully in RAM (D21).
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from uuid import UUID

from .filesystem import (
    StreamWriteResult,
    atomic_write_stream,
    sanitize_filename,
)
from .jumper_migration import ATTACHMENTS_DIRNAME

# Separator between the attachment UUID prefix and the user-facing
# filename. Two underscores so user filenames containing a single
# underscore ("my_card.pdf", "tandem-rating_2026.pdf") don't
# collide with the prefix delimiter.
_ATTACHMENT_NAME_SEP = "__"

# UUID canonical form is 36 chars; the separator is 2 chars; together
# they consume 38 chars of the 255-byte filesystem cap. The user
# portion is therefore capped at 217 chars to keep the composed name
# inside that limit. sanitize_filename's default cap of 255 would
# allow a too-long composed name to slip through.
_USER_FILENAME_MAX = 255 - 36 - len(_ATTACHMENT_NAME_SEP)


def jumper_attachments_dir(jumper_folder: Path) -> Path:
    """Return the ``attachments/`` subfolder path inside a jumper folder.

    Pure path math; does not create or check the directory. Callers
    that want the directory to exist should ``mkdir`` it themselves
    or rely on ``write_attachment_stream`` (which creates parents
    via ``atomic_write_stream``).
    """
    return jumper_folder / ATTACHMENTS_DIRNAME


def compose_attachment_filename(
    attachment_id: UUID, user_filename: str
) -> str:
    """Build the on-disk filename for a jumper attachment.

    Shape: ``<attachment_uuid>__<sanitized-user-filename>``.

    The user filename goes through ``sanitize_filename`` with a
    tightened cap (217 chars) so the composed result stays under
    the 255-byte filesystem limit. D4 rules apply: NFC normalization,
    no forbidden / control characters, no Windows reserved names, no
    trailing space or period.

    Raises ``ValueError`` if sanitization rejects the user filename
    or if the result somehow lands too long (defensive — the cap
    above should prevent this).
    """
    safe_user = sanitize_filename(user_filename, max_length=_USER_FILENAME_MAX)
    composed = f"{attachment_id}{_ATTACHMENT_NAME_SEP}{safe_user}"
    if len(composed) > 255:
        # Defensive — the cap on safe_user should keep us under 255.
        # This branch fires only if a future contributor changes the
        # constants without updating the math.
        raise ValueError(
            f"composed attachment filename exceeds 255 bytes: "
            f"{len(composed)} chars"
        )
    return composed


def parse_attachment_filename(disk_filename: str) -> tuple[UUID, str]:
    """Inverse of :func:`compose_attachment_filename`.

    Splits ``disk_filename`` on the FIRST ``__`` separator and parses
    the prefix as a UUID. Anything after the separator is the user-
    facing filename verbatim — a user filename that itself contains
    ``__`` round-trips correctly because we split only on the first
    occurrence.

    Used by the recovery path: given a stray file in
    ``attachments/``, resolve which attachment record it belongs to
    without reading ``jumper.xml``.

    Raises ``ValueError`` if the shape doesn't match — no separator,
    or the prefix isn't a parseable UUID.
    """
    sep_idx = disk_filename.find(_ATTACHMENT_NAME_SEP)
    if sep_idx < 0:
        raise ValueError(
            f"jumper attachment filename missing "
            f"{_ATTACHMENT_NAME_SEP!r} separator: {disk_filename!r}"
        )
    uuid_str = disk_filename[:sep_idx]
    user_part = disk_filename[sep_idx + len(_ATTACHMENT_NAME_SEP):]
    try:
        attachment_id = UUID(uuid_str)
    except ValueError as exc:
        raise ValueError(
            f"jumper attachment filename's UUID prefix is not a valid "
            f"UUID: {uuid_str!r}"
        ) from exc
    return attachment_id, user_part


def attachment_disk_path(
    jumper_folder: Path,
    attachment_id: UUID,
    user_filename: str,
) -> Path:
    """Resolve the on-disk path for one attachment under a jumper folder.

    Combines :func:`jumper_attachments_dir` with
    :func:`compose_attachment_filename`. Pure path math; does not
    create directories or files. Raises ``ValueError`` if the user
    filename fails D4 sanitization (propagated from
    ``compose_attachment_filename``).
    """
    return jumper_attachments_dir(jumper_folder) / compose_attachment_filename(
        attachment_id, user_filename
    )


def write_attachment_stream(
    jumper_folder: Path,
    attachment_id: UUID,
    user_filename: str,
    chunks: Iterable[bytes],
) -> StreamWriteResult:
    """Stream-write one attachment file under ``jumper_folder/attachments/``.

    Wraps :func:`atomic_write_stream`. The destination path is
    composed via :func:`attachment_disk_path`. The ``attachments/``
    subfolder is auto-created via the underlying ``parent.mkdir
    (parents=True, exist_ok=True)`` — callers do not need to ensure
    it exists in advance.

    Returns the ``(sha256, size)`` ``StreamWriteResult`` describing
    exactly the bytes that landed on disk. Service-layer callers use
    this pair to populate the ``JumperAttachment`` Pydantic model
    before serializing the new ``jumper.xml``.

    Raises ``ValueError`` (from sanitization) or ``OSError`` (from
    the underlying write) — service layer translates each.
    """
    dest = attachment_disk_path(jumper_folder, attachment_id, user_filename)
    return atomic_write_stream(dest, chunks)


def delete_attachment_file(
    jumper_folder: Path,
    attachment_id: UUID,
    user_filename: str,
) -> None:
    """Remove one attachment file from ``jumper_folder/attachments/``.

    Raises ``FileNotFoundError`` if the file doesn't exist. The
    service layer (C.3) decides whether to translate that into a
    404 NotFoundError or a silent no-op; this module's contract is
    "do exactly one filesystem operation, surface the OS-level
    outcome verbatim."
    """
    path = attachment_disk_path(jumper_folder, attachment_id, user_filename)
    path.unlink()
