"""Jumper service — folder-with-manifest layout (D33, D47, Phase C.1).

A jumper is now a folder under
``logbook_root/jumpers/<uuid>/`` containing:

    jumper.xml      — authoritative XML record (D2 invariants apply)
    SHA256SUMS      — manifest covering jumper.xml + every attachment
    attachments/    — credential card / medical certificate files

This shape mirrors the rig folder pattern (D33) so that ``bootstrap``,
``verify``, and the soft-delete trash all see a single uniform layout
across rigs and jumpers. The C.1 slice migrated the prior flat
``jumpers/<uuid>.xml`` files into this shape — see
``backend/storage/jumper_migration.py`` for the migration semantics.

R.2.0b shipped create + get; R.2.0c extended with list + update +
delete + the auto-bump rule for ``exit_weight_updated_at``. C.1
preserved every public service signature; only the on-disk paths
shifted.

Storage shape:

    logbook_root/
      jumpers/
        <uuid>/
          jumper.xml
          SHA256SUMS
          attachments/        # populated by C.2 / C.3
      .trash/
        jumpers/
          <ts>_<uuid>/        # post-soft-delete folder

Invariants (every write):
  * XSD validation BEFORE the atomic write (D2).
  * ``jumper.xml`` is written via ``atomic_write`` (D10), then
    ``SHA256SUMS`` is regenerated from the on-disk shape via
    ``manifest.generate`` (correct call on the write path per D25 —
    we just wrote the bytes, hashing what we wrote equals hashing
    what's there).
  * Order: jumper.xml first, manifest second. A crash between leaves
    jumper.xml on disk and the next write regenerates the manifest;
    no data loss.
  * No SQLite index in C.1 — index work for credentials lands in
    Phase D.

Service-side behaviour (unchanged from R.2.0c except path math):
  * ``create_jumper`` server-assigns the UUID, stamps both
    ``created_at`` and ``updated_at`` together, and stamps
    ``exit_weight_updated_at`` to "today" if the caller didn't
    supply one.
  * ``get_jumper`` reads ``jumpers/<id>/jumper.xml``.
  * ``list_jumpers`` walks ``jumpers/`` for subfolders that look
    like UUIDs and parses each ``jumper.xml``. Subfolders without
    a parseable jumper.xml log a WARNING and are skipped.
  * ``update_jumper`` is full-replace (id+created_at preserved,
    updated_at bumped). The auto-bump rule for
    ``exit_weight_updated_at`` is unchanged from R.2.0c.
  * ``delete_jumper`` soft-deletes the entire folder to
    ``.trash/jumpers/<ts>_<uuid>/``. Attachments come along for
    the ride.
  * ``add_attachment_to_jumper`` (D47, Phase C.3) stream-writes one
    file under ``attachments/`` and appends a ``JumperAttachment``
    entry to ``jumper.xml``, regenerating the manifest from the new
    XML claims. Mirrors ``jump_service.add_attachments`` but for
    one file at a time — credential cards arrive one-per-credential
    rather than as a batch.
  * ``delete_attachment_from_jumper`` (D47, Phase C.3) removes one
    attachment from the canonical record + the disk + the manifest.
    Refuses to delete an attachment that any credential references
    via ``card_attachment_id`` (409 with FieldError pointers
    enumerating the references) so the credential never ends up
    pointing at nothing.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import date as _date
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError

from ..api.errors import (
    ConflictError,
    FieldError,
    NotFoundError,
    ValidationFailedError,
    validation_failed_from_pydantic,
)
from ..models.jumper import Jumper, JumperAttachment, JumperCreate, JumperUpdate
from ..storage import manifest as _manifest
from ..storage.filesystem import atomic_write
from ..storage.jumper_attachments import (
    delete_attachment_file,
    write_attachment_stream,
)
from ..storage.jumper_migration import (
    ATTACHMENTS_DIRNAME,
    JUMPER_XML_NAME,
    JUMPERS_DIRNAME,
)
from ..storage.trash import soft_delete
from ..xml.serialize import element_to_jumper, jumper_to_bytes, jumper_to_element
from ..xml.validator import XMLError, validate
from ..xml.validator import parse as xml_parse
from ._timestamps import now_utc_iso
from ._write_lock import with_writer_lock

# Same trash subdir as before D47 — soft-delete still groups jumpers
# under ``.trash/jumpers/`` so existing trashed records keep working.
_TRASH_SUBDIR = "jumpers"

_logger = logging.getLogger("backend.services.jumper")




def _today_utc() -> _date:
    """Today's calendar date in UTC.

    ``exit_weight_updated_at`` is xs:date precision (D33's 365-day
    staleness window doesn't need clock time). UTC over local time
    keeps the value stable across timezones — a jumper who logs
    their weight from Vancouver (UTC-8) and then again from Paris
    (UTC+2) shouldn't see the date jump back-and-forth depending
    on the device's clock.
    """
    return datetime.now(UTC).date()


def _jumper_folder(logbook_root: Path, jumper_id: UUID) -> Path:
    """Resolve the on-disk folder path for one jumper.

    UUIDs are guaranteed safe across every filesystem we target (D4),
    so the UUID string is the folder name unchanged.
    """
    return logbook_root / JUMPERS_DIRNAME / str(jumper_id)


def _read_jumper(folder: Path) -> Jumper:
    """Parse + XSD-validate the ``jumper.xml`` inside ``folder``.

    Raises:
      NotFoundError: ``jumper.xml`` is missing — either the folder
        doesn't exist or it does but is incomplete (a half-failed
        write where the folder was created without the document
        landing). Either way the jumper isn't readable.
      ValidationFailedError: hardened parser or XSD rejected the
        contents — surface as 422 so an operator can re-edit or
        restore from backup. Same posture as the inventory
        component services.
    """
    xml_path = folder / JUMPER_XML_NAME
    if not xml_path.is_file():
        raise NotFoundError(f"jumper.xml not found in {folder.name}")
    try:
        element = xml_parse(xml_path.read_bytes())
        validate(element)
    except XMLError as exc:
        raise ValidationFailedError(
            f"jumper {folder.name} is invalid: {exc}",
        ) from exc
    return element_to_jumper(element)


def _write_jumper(logbook_root: Path, j: Jumper) -> None:
    """Serialize, XSD-validate, and atomically write a Jumper to disk.

    Order:
      1. Build + XSD-validate the element. A bad shape leaves disk
         untouched (D2 + D10).
      2. ``mkdir`` the folder + empty ``attachments/`` subfolder.
         Idempotent on update — the folder already exists.
      3. ``atomic_write`` ``jumper.xml`` (the canonical document).
      4. Regenerate SHA256SUMS via ``manifest.generate`` (the
         write-path manifest call per D25 — the bytes were just
         written, hashing what we wrote equals hashing what's there).
      5. ``atomic_write`` ``SHA256SUMS``.

    Crash semantics: a crash between step 3 and step 5 leaves
    ``jumper.xml`` on disk plus a stale or absent ``SHA256SUMS``.
    The next successful write regenerates the manifest; ``verify``
    flags the divergence in the meantime.
    """
    element = jumper_to_element(j)
    validate(element)
    folder = _jumper_folder(logbook_root, j.id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ATTACHMENTS_DIRNAME).mkdir(exist_ok=True)
    atomic_write(folder / JUMPER_XML_NAME, jumper_to_bytes(j))
    manifest_bytes = _manifest.generate(folder)
    atomic_write(folder / _manifest.MANIFEST_NAME, manifest_bytes)


@with_writer_lock
def create_jumper(
    logbook_root: Path,
    user_id: str,
    payload: JumperCreate,
) -> Jumper:
    """Create a new jumper at ``jumpers/<uuid>/jumper.xml``.

    Server-assigns the UUID, stamps ``created_at`` / ``updated_at``,
    and stamps ``exit_weight_updated_at`` to today's UTC date if the
    caller didn't supply one — see ``_today_utc`` for the timezone
    rationale.

    ``user_id`` is accepted per D8 for forward compatibility but
    unused in v0.1 — there is exactly one jumper folder per logbook
    by convention, and the folder is implicitly "the logbook owner."

    Raises:
      ValidationFailedError: Pydantic / XSD rejected the shape.
    """
    del user_id  # v0.1: single-jumper convention
    now = now_utc_iso()
    weight_stamp = (
        payload.exit_weight_updated_at
        if payload.exit_weight_updated_at is not None
        else _today_utc()
    )
    try:
        # ``model_validate`` accepts a single dict whose shape pyright
        # cannot narrow per-key (the ``**`` spread of payload.model_dump()
        # erases per-field types into ``Any | date``); validate-from-dict
        # avoids the kwargs cascade and Pydantic still re-validates every
        # field at runtime.
        j = Jumper.model_validate(
            {
                "id": uuid4(),
                **payload.model_dump(),
                "exit_weight_updated_at": weight_stamp,
                "created_at": now,
                "updated_at": now,
            }
        )
    except ValidationError as exc:
        # Defensive — JumperCreate already validated the payload.
        raise validation_failed_from_pydantic(exc, "jumper validation failed") from exc

    try:
        _write_jumper(logbook_root, j)
    except XMLError as exc:
        raise ValidationFailedError(
            f"generated jumper XML failed XSD validation: {exc}",
        ) from exc

    _logger.info(
        "jumper_created",
        extra={
            "jumper_id": str(j.id),
            "exit_weight_lb": j.exit_weight_lb,
            "exit_weight_updated_at": (
                j.exit_weight_updated_at.isoformat()
                if j.exit_weight_updated_at is not None
                else None
            ),
        },
    )
    return j


def get_jumper(
    logbook_root: Path,
    user_id: str,
    jumper_id: UUID,
) -> Jumper:
    """Return the jumper with the given id, or raise NotFoundError."""
    del user_id  # v0.1: see create_jumper
    folder = _jumper_folder(logbook_root, jumper_id)
    if not folder.is_dir():
        raise NotFoundError(f"jumper {jumper_id} not found")
    return _read_jumper(folder)


def list_jumpers(
    logbook_root: Path,
    user_id: str,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[Jumper]:
    """List every jumper under ``jumpers/``.

    Walks the directory for UUID-named subfolders and parses each
    ``jumper.xml``. Returns full :class:`Jumper` objects rather than
    a compact summary — for v0.1's single-jumper workload the parse
    cost is negligible, and the multi-jumper forward-compat shape
    (the rig-snapshot reader) wants every field anyway.

    Ordering: ``created_at`` descending (newest first), mirroring
    the inventory list services. Jumpers without a timestamp (e.g.
    legacy hand-edited files) sort last.

    ``limit`` / ``offset`` apply at the service layer after parsing
    + sorting. v0.1 inventory sizes (one jumper, growing to a small
    handful in multi-jumper) make this fine; SQLite-backed
    pagination is a future optimization.

    Subfolders that fail XSD validation (or are missing
    ``jumper.xml`` entirely — a half-failed migration) are logged
    at WARNING and skipped — the list endpoint stays useful even if
    one folder is corrupt; an operator runs ``verify`` to diagnose.
    """
    del user_id  # v0.1: see create_jumper
    folder = logbook_root / JUMPERS_DIRNAME
    if not folder.is_dir():
        # Bootstrap should have created this; tolerate the absent
        # case anyway (a fresh logbook root before bootstrap, a
        # deleted dir, etc.) — return empty rather than raising.
        return []

    parsed: list[Jumper] = []
    for entry in folder.iterdir():
        if not entry.is_dir():
            # Stray top-level files (e.g. unmigrated legacy
            # ``<uuid>.xml`` that bootstrap couldn't migrate
            # because the content was corrupt) are not jumpers in
            # the new layout. ``verify`` is the right tool for
            # surfacing them; the list endpoint stays clean.
            continue
        try:
            parsed.append(_read_jumper(entry))
        except (NotFoundError, ValidationFailedError) as exc:
            _logger.warning(
                "jumper_skip_invalid",
                extra={
                    "jumper_folder": str(entry),
                    "reason": str(exc),
                },
            )
            continue

    # Newest first; None timestamps sort last.
    parsed.sort(key=lambda j: j.created_at or "", reverse=True)

    if offset:
        parsed = parsed[offset:]
    if limit is not None:
        parsed = parsed[:limit]
    return parsed


@with_writer_lock
def update_jumper(
    logbook_root: Path,
    user_id: str,
    jumper_id: UUID,
    payload: JumperUpdate,
) -> Jumper:
    """Full-replace update of a jumper's editable fields.

    Preserves ``id`` and ``created_at``; bumps ``updated_at``. Also
    preserves the credential collections (memberships, cops,
    ratings, tandem_ratings, medicals, attachments) — those are
    edited through their dedicated endpoints (D47, Phase D), not
    through this PUT.

    Re-validates Pydantic + XSD before writing.

    **Auto-bump rule for ``exit_weight_updated_at``** (D33):

    * If the caller's ``exit_weight_lb`` differs from the on-disk
      value AND the caller did NOT supply
      ``exit_weight_updated_at``, the service stamps it to today's
      UTC date. The 365-day staleness clock resets on every weight
      change.
    * If the caller supplies an explicit
      ``exit_weight_updated_at``, it wins — used-gear correction
      paths (the user knows the historical confirmation date) are
      preserved.
    * If the weight is unchanged AND the caller didn't supply a
      date, the on-disk timestamp is preserved (a metadata-only
      edit like changing the name doesn't reset the staleness
      clock).

    Raises :class:`NotFoundError` if no jumper with this id is on
    disk; :class:`ValidationFailedError` if Pydantic / XSD rejects
    the merged shape.
    """
    del user_id  # v0.1: see create_jumper
    folder = _jumper_folder(logbook_root, jumper_id)
    if not folder.is_dir():
        raise NotFoundError(f"jumper {jumper_id} not found")
    current = _read_jumper(folder)

    # Resolve the auto-bump rule. The payload's
    # exit_weight_updated_at is None ↔ "caller did not supply one".
    weight_changed = payload.exit_weight_lb != current.exit_weight_lb
    if payload.exit_weight_updated_at is not None:
        weight_stamp = payload.exit_weight_updated_at
    elif weight_changed:
        weight_stamp = _today_utc()
    else:
        weight_stamp = current.exit_weight_updated_at

    try:
        # ``model_validate`` from a single dict avoids the dict-spread
        # kwargs typing cascade (see ``create_jumper`` for the full
        # rationale). Pydantic still re-validates every field at
        # runtime.
        merged = Jumper.model_validate(
            {
                "id": current.id,                   # immutable
                "created_at": current.created_at,   # immutable (D32)
                "updated_at": now_utc_iso(),
                **payload.model_dump(),
                "exit_weight_updated_at": weight_stamp,
                # Preserve credential collections — JumperUpdate is
                # identity-only per D47. The dedicated endpoints in
                # Phase D edit these.
                "memberships": current.memberships,
                "cops": current.cops,
                "ratings": current.ratings,
                "tandem_ratings": current.tandem_ratings,
                "medicals": current.medicals,
                "attachments": current.attachments,
            }
        )
    except ValidationError as exc:
        raise validation_failed_from_pydantic(exc, "jumper validation failed") from exc

    try:
        _write_jumper(logbook_root, merged)
    except XMLError as exc:
        raise ValidationFailedError(
            f"generated jumper XML failed XSD validation: {exc}",
        ) from exc

    _logger.info(
        "jumper_updated",
        extra={
            "jumper_id": str(merged.id),
            "exit_weight_lb": merged.exit_weight_lb,
            "exit_weight_changed": weight_changed,
            "exit_weight_updated_at": (
                merged.exit_weight_updated_at.isoformat()
                if merged.exit_weight_updated_at is not None
                else None
            ),
        },
    )
    return merged


@with_writer_lock
def delete_jumper(
    logbook_root: Path,
    user_id: str,
    jumper_id: UUID,
) -> Path:
    """Soft-delete a jumper folder to ``.trash/jumpers/`` (D19).

    Per D47, the entire folder (jumper.xml + SHA256SUMS +
    attachments/) moves to trash atomically — no separate handling
    of attachments. ``soft_delete`` uses ``shutil.move`` which is
    a directory rename when source and destination are on the same
    filesystem; the move is effectively atomic at the kernel level
    on Unix and on most Windows volumes.

    Returns the new path inside ``.trash`` so callers can log the
    move. No cascade — historical jumps and their rig snapshots
    keep their denormalized jumper data (D36 makes rig-snapshot
    immutable post-create), and other entities don't reference the
    jumper by id in v0.1.

    Raises :class:`NotFoundError` when the folder doesn't exist.
    """
    del user_id  # v0.1: see create_jumper
    folder = _jumper_folder(logbook_root, jumper_id)
    if not folder.is_dir():
        raise NotFoundError(f"jumper {jumper_id} not found")
    try:
        trashed = soft_delete(folder, logbook_root, subdir=_TRASH_SUBDIR)
    except FileNotFoundError as exc:
        # Race with a concurrent delete or out-of-band folder move.
        raise NotFoundError(f"jumper {jumper_id} not found") from exc

    _logger.info(
        "jumper_deleted",
        extra={
            "jumper_id": str(jumper_id),
            "trashed_to": trashed.relative_to(logbook_root).as_posix(),
        },
    )
    return trashed


@dataclass(frozen=True)
class Upload:
    """A single inbound file upload on its way to the attachments folder.

    Framework-agnostic so the service layer never imports FastAPI (D7).
    The REST adapter (Phase C.4) builds one of these per UploadFile
    in the request; tests build them from plain ``list[bytes]``.

    The ``chunks`` iterable is consumed exactly once — that matches
    the HTTP upload reality (bytes flow past, then they're gone) and
    matches what ``atomic_write_stream`` needs.
    """

    filename: str
    content_type: str | None
    chunks: Iterable[bytes]


def _credentials_referencing(jumper: Jumper, attachment_id: UUID) -> list[FieldError]:
    """Return FieldError pointers for every credential whose
    ``card_attachment_id`` matches ``attachment_id``.

    Used by :func:`delete_attachment_from_jumper` to enumerate
    dangling-reference risks before the deletion. An empty list
    means the attachment is safe to delete.

    Pointer paths use RFC 6901 / Pydantic indexing so the API
    consumer can trace each reference back to a specific record
    (e.g. ``#/memberships/0/card_attachment_id``).
    """
    refs: list[FieldError] = []
    # Each credential collection has a different element type but a
    # shared ``card_attachment_id`` field accessed via ``getattr``.
    # ``Sequence[object]`` keeps the iteration type-clean while leaving
    # the duck-typing intentional.
    collections: list[tuple[str, Sequence[object]]] = [
        ("memberships", jumper.memberships),
        ("cops", jumper.cops),
        ("ratings", jumper.ratings),
        ("tandem_ratings", jumper.tandem_ratings),
        ("medicals", jumper.medicals),
    ]
    for name, items in collections:
        for i, item in enumerate(items):
            if getattr(item, "card_attachment_id", None) == attachment_id:
                refs.append(
                    FieldError(
                        pointer=f"#/{name}/{i}/card_attachment_id",
                        detail=(
                            f"this attachment is referenced by "
                            f"{name}[{i}].card_attachment_id"
                        ),
                    )
                )
    return refs


@with_writer_lock
def add_attachment_to_jumper(
    logbook_root: Path,
    user_id: str,
    jumper_id: UUID,
    upload: Upload,
) -> Jumper:
    """Attach one file to a jumper's ``attachments/`` folder (D47, Phase C.3).

    Order of operations (mirrors ``jump_service.add_attachments``
    crash semantics in D25 §B applied to the jumper folder):

      1. Resolve the existing Jumper (404 if missing).
      2. Mint an attachment UUID server-side.
      3. Stream-hash-write the bytes via
         :func:`backend.storage.jumper_attachments.write_attachment_stream`.
         The on-disk filename is ``<uuid>__<sanitized-filename>`` so
         two uploads sharing a user filename never collide. The user
         filename is sanitized through D4 rules; failure raises
         ``ValidationFailedError`` BEFORE any bytes touch disk.
      4. Build a :class:`JumperAttachment` with the returned sha256
         + size. The Pydantic model re-runs sanitize_filename for
         defense-in-depth.
      5. Append the attachment to the Jumper's ``attachments`` list.
      6. Bump ``updated_at``.
      7. Serialize + XSD-validate the new shape (D2 invariant).
      8. ``atomic_write`` the new ``jumper.xml``.
      9. Regenerate ``SHA256SUMS`` from the new XML claims via
         :func:`backend.storage.manifest.from_jumper_xml` (D25 — the
         claim-based form preserves jumper.xml as the authoritative
         witness if attachment bytes have rotted).

    Crash semantics (D25 §B applied to jumper folder):
      * Crash between step 3 and step 8 leaves an orphan file on
        disk in ``attachments/`` not referenced by any
        ``<attachment>`` in jumper.xml. ``verify`` flags it; no
        automatic cleanup.
      * Crash between step 8 and step 9 leaves ``jumper.xml`` with
        the new attachment claim plus a stale manifest. The next
        successful write regenerates the manifest; ``verify`` flags
        the divergence in the meantime.

    Returns the updated Jumper (with the new attachment in its list).

    Raises:
      NotFoundError (404): jumper not found.
      ValidationFailedError (422): user filename failed D4
        sanitization, or the resulting Jumper failed Pydantic / XSD
        validation.
    """
    del user_id  # v0.1: see create_jumper

    folder = _jumper_folder(logbook_root, jumper_id)
    if not folder.is_dir():
        raise NotFoundError(f"jumper {jumper_id} not found")
    current = _read_jumper(folder)

    attachment_id = uuid4()

    # Stream-write the bytes. write_attachment_stream applies
    # sanitize_filename and may raise ValueError pre-write — translate
    # to a 422 with a precise pointer at #/filename.
    try:
        stream_result = write_attachment_stream(
            folder, attachment_id, upload.filename, upload.chunks
        )
    except ValueError as exc:
        raise ValidationFailedError(
            "invalid attachment filename",
            errors=[FieldError(pointer="#/filename", detail=str(exc))],
        ) from exc

    # Build the JumperAttachment record. The Pydantic model also runs
    # sanitize_filename via the field validator; defense-in-depth.
    try:
        new_attachment = JumperAttachment(
            id=attachment_id,
            filename=upload.filename,
            sha256=stream_result.sha256,
            size=stream_result.size,
            content_type=upload.content_type,
        )
    except ValidationError as exc:
        raise validation_failed_from_pydantic(exc, "jumper validation failed") from exc

    # Append to the Jumper's attachments list and rebuild the model.
    # Existing attachments stay first; order is preserved on
    # serialize so subsequent reads see a stable list.
    updated = current.model_copy(
        update={
            "attachments": [*current.attachments, new_attachment],
            "updated_at": now_utc_iso(),
        }
    )

    # Serialize + XSD-validate. Bad shape leaves the new file on disk
    # (orphan) but jumper.xml unchanged — verify flags the orphan
    # later. Same posture as jump_service.add_attachments.
    element = jumper_to_element(updated)
    try:
        validate(element)
    except XMLError as exc:
        raise ValidationFailedError(
            f"updated jumper XML failed XSD validation: {exc}",
        ) from exc

    atomic_write(folder / JUMPER_XML_NAME, jumper_to_bytes(updated))

    # Regenerate manifest from the on-disk XML claims (D25 recovery
    # form — accepts that attachment bytes might rot; the manifest
    # still reflects what jumper.xml says they ought to hash to).
    manifest_bytes = _manifest.from_jumper_xml(folder, logbook_root=logbook_root)
    atomic_write(folder / _manifest.MANIFEST_NAME, manifest_bytes)

    _logger.info(
        "jumper_attachment_added",
        extra={
            "jumper_id": str(jumper_id),
            "attachment_id": str(attachment_id),
            "attachment_filename": new_attachment.filename,
            "size": new_attachment.size,
        },
    )
    return updated


@with_writer_lock
def delete_attachment_from_jumper(
    logbook_root: Path,
    user_id: str,
    jumper_id: UUID,
    attachment_id: UUID,
) -> Jumper:
    """Remove one attachment from the jumper's record + disk + manifest.

    Steps:
      1. Resolve the existing Jumper (404 if missing).
      2. Locate the matching JumperAttachment by id (404 if not in
         the canonical list).
      3. Scan all credential collections for any
         ``card_attachment_id`` referencing this attachment. If any
         match, raise 409 with a FieldError per reference so the
         caller can clear them first. Refusing is conservative — the
         alternative (cascade-clear the references) would silently
         mutate credential records, which the caller may not expect.
      4. Rebuild the Jumper without that attachment, bump
         ``updated_at``.
      5. Serialize + XSD-validate (D2).
      6. ``atomic_write`` ``jumper.xml`` (without the entry).
      7. Regenerate ``SHA256SUMS`` from the new XML claims.
      8. ``unlink`` the file from disk.

    Crash semantics: a crash between step 7 and step 8 leaves the
    file on disk as an orphan (not referenced by any
    ``<attachment>``). ``verify`` flags it; the user can clean it
    up via the file manager. The canonical record is internally
    consistent at every intermediate state.

    Hard delete — no soft-delete. Attachments are individually small
    and typically restorable from external backup; the folder-level
    soft-delete (``delete_jumper``) covers the wholesale loss case.

    Raises:
      NotFoundError (404): jumper not found, or attachment_id not in
        the canonical list.
      ConflictError (409): one or more credentials reference this
        attachment via ``card_attachment_id``. The ``errors`` array
        carries one FieldError per reference.
      ValidationFailedError (422): the post-delete shape failed
        Pydantic / XSD validation (defensive — should not happen
        since we only removed an entry).
    """
    del user_id  # v0.1: see create_jumper

    folder = _jumper_folder(logbook_root, jumper_id)
    if not folder.is_dir():
        raise NotFoundError(f"jumper {jumper_id} not found")
    current = _read_jumper(folder)

    # Locate the attachment record. List comprehension intentional —
    # a future v0.1 jumper might carry many attachments; linear scan
    # is fine.
    target = next(
        (a for a in current.attachments if a.id == attachment_id),
        None,
    )
    if target is None:
        raise NotFoundError(
            f"attachment {attachment_id} not found on jumper {jumper_id}"
        )

    # Cross-reference check: refuse if any credential refs this id.
    refs = _credentials_referencing(current, attachment_id)
    if refs:
        raise ConflictError(
            "attachment is referenced by one or more credentials; "
            "clear the reference before deleting",
            errors=refs,
        )

    # Rebuild the Jumper without this attachment.
    updated = current.model_copy(
        update={
            "attachments": [
                a for a in current.attachments if a.id != attachment_id
            ],
            "updated_at": now_utc_iso(),
        }
    )

    element = jumper_to_element(updated)
    try:
        validate(element)
    except XMLError as exc:
        raise ValidationFailedError(
            f"updated jumper XML failed XSD validation: {exc}",
        ) from exc

    atomic_write(folder / JUMPER_XML_NAME, jumper_to_bytes(updated))

    manifest_bytes = _manifest.from_jumper_xml(folder, logbook_root=logbook_root)
    atomic_write(folder / _manifest.MANIFEST_NAME, manifest_bytes)

    # Last step: unlink the file. A crash before this leaves an
    # orphan file in attachments/ that ``verify`` flags. Tolerate
    # FileNotFoundError so a re-run after a half-completed delete
    # (unlink already happened) idempotently completes.
    try:
        delete_attachment_file(folder, attachment_id, target.filename)
    except FileNotFoundError:
        _logger.warning(
            "jumper_attachment_file_already_missing",
            extra={
                "jumper_id": str(jumper_id),
                "attachment_id": str(attachment_id),
                "attachment_filename": target.filename,
            },
        )

    _logger.info(
        "jumper_attachment_deleted",
        extra={
            "jumper_id": str(jumper_id),
            "attachment_id": str(attachment_id),
            "filename": target.filename,
        },
    )
    return updated

