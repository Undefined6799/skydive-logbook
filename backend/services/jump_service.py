"""Jump service — all business logic for jumps (D7).

Every function takes ``logbook_root: Path`` (D2 — the folder is the
entire scope of the app) and ``user_id: str`` (D8 — parameter from day
one; default is ``"default"`` until multi-user arrives).

Invariants enforced by every write:
- XSD validation on every read and write (D2).
- Atomic write via ``storage.filesystem.atomic_write`` (D10); attachments
  stream via ``atomic_write_stream`` per D21+D30.
- ``SHA256SUMS`` regenerated in the same transaction as ``jump.xml`` (D5).
- ``summary.md`` rendered best-effort AFTER the authoritative write (D5 —
  deferred for v0.1; service calls may skip it, ``folder_reconcile`` and
  future lazy-render on read handle the absence).
- SQLite row inserted/updated only after XML is on disk (D3).
"""
from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
from collections.abc import Generator, Iterable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from ..api.errors import (
    FieldError,
    JumpNumberConflict,
    NotFoundError,
    ValidationFailedError,
    field_pointer,
)
from ..models.common import SCHEMA_NAMESPACE_V1
from ..models.jump import Attachment, Jump, JumpCreate, JumpSummary, JumpUpdate
from ..storage.filesystem import (
    atomic_write,
    atomic_write_stream,
    jump_folder_name,
    sanitize_filename,
)
from ..storage.index import open_index
from ..storage.manifest import MANIFEST_NAME, from_jump_xml
from ..storage.reconcile import folder_reconcile
from ..storage.trash import soft_delete
from ..xml.serialize import element_to_jump, jump_to_bytes, jump_to_element
from ..xml.validator import parse as xml_parse
from ..xml.validator import validate
from ._timestamps import now_utc_iso
from ._write_lock import with_writer_lock

JUMP_XML_NAME = "jump.xml"
_ACTIVE_JUMPS_DIR = "jumps"

_logger = logging.getLogger("backend.services.jump")


@dataclass(frozen=True)
class Upload:
    """A single inbound file upload on its way to ``atomic_write_stream``.

    Framework-agnostic so the service layer never imports FastAPI (D7).
    The REST adapter builds one of these per ``UploadFile`` in the
    request; tests build them from plain ``list[bytes]``.

    The ``chunks`` iterable is consumed exactly once — that matches
    the HTTP upload reality (bytes flow past, then they're gone) and
    matches what ``atomic_write_stream`` needs.
    """

    filename: str
    content_type: str | None
    chunks: Iterable[bytes]


@contextlib.contextmanager
def _index_conn(logbook_root: Path) -> Generator[sqlite3.Connection]:
    """Open the SQLite index for the lifetime of a ``with`` block.

    Wraps ``open_index`` / ``conn.close`` in a single ``try/finally`` so
    every service site stays a one-liner. The yielded connection is the
    same object ``open_index`` returns on its result.
    """
    result = open_index(logbook_root)
    try:
        yield result.conn
    finally:
        result.conn.close()


def _get_jump_folder(
    logbook_root: Path, jump_id: UUID, user_id: str
) -> Path:
    """Resolve a jump's folder via the SQLite index, or raise ``NotFoundError``.

    The index is authoritative for the ``id → folder`` mapping; the caller
    is responsible for D2-validating any XML read from the resolved folder.
    """
    with _index_conn(logbook_root) as conn:
        row = conn.execute(
            "SELECT folder FROM jumps WHERE id = ? AND user_id = ?",
            (str(jump_id), user_id),
        ).fetchone()
    if row is None:
        raise NotFoundError(f"jump {jump_id} not found")
    return logbook_root / row["folder"]


def _write_jump_and_manifest(
    folder: Path, jump: Jump, logbook_root: Path
) -> None:
    """Serialize, XSD-validate, and atomic-write ``jump.xml`` + ``SHA256SUMS``.

    D25's per-folder write ordering for a single jump's metadata edits:
    ``jump.xml`` first (authoritative), then the derived ``SHA256SUMS``
    manifest. A crash between the two leaves the folder with valid XML
    and a stale manifest — ``folder_reconcile`` heals it on next read.
    Validation runs against the in-memory element so a failed XSD check
    leaves the previous on-disk state untouched (D2).
    """
    element = jump_to_element(jump)
    validate(element)
    atomic_write(folder / JUMP_XML_NAME, jump_to_bytes(jump))
    atomic_write(
        folder / MANIFEST_NAME,
        from_jump_xml(folder, logbook_root=logbook_root),
    )


def _jump_number_is_taken(
    logbook_root: Path, user_id: str, jump_number: int
) -> Path | None:
    """Return the colliding folder if ``[<N>]`` or ``[<N>] *`` already exists.

    D23's filesystem backstop, revised 2026-04-23 for D4's new folder-
    name shape. Title makes folder names vary for the same jump_number,
    so ``mkdir(exist_ok=False)`` alone no longer catches all collisions
    — we scan ``jumps/`` for any entry whose name equals ``[<N>]`` or
    starts with ``[<N>] `` (bracket-number-space prefix).

    ``user_id`` is accepted for forward compatibility. In v0.1 every
    folder belongs to ``default``, so the parameter is unused here; the
    index UNIQUE constraint is what enforces the compound key.
    """
    del user_id  # unused in v0.1; see comment above
    jumps_dir = logbook_root / _ACTIVE_JUMPS_DIR
    if not jumps_dir.is_dir():
        return None
    prefix_exact = f"[{jump_number}]"
    prefix_space = f"[{jump_number}] "
    for entry in jumps_dir.iterdir():
        if entry.name == prefix_exact or entry.name.startswith(prefix_space):
            return entry
    return None


def _raise_jump_number_conflict(jump_number: int) -> None:
    """Shared D23 collision raise site — one message for every path.

    Raises ``JumpNumberConflict`` with D23's canonical ``code`` and a
    field-pointer error payload so the REST layer can surface a 409
    problem+json body with an ``errors`` array pointing at
    ``#/jump_number`` (RFC 9457 §3).
    """
    raise JumpNumberConflict(
        f"jump_number {jump_number} is already in use",
        errors=[
            FieldError(
                pointer="#/jump_number",
                detail="already in use",
            )
        ],
    )


def _sanitize_upload_filenames(
    uploads: list[Upload],
) -> list[str]:
    """Sanitize every upload filename and reject duplicates (D30).

    Runs BEFORE any disk write so a bad filename or duplicate produces
    a 422 with an untouched filesystem. Returns the list of
    canonicalized (NFC-normalized, validated) filenames in upload
    order — callers pair them positionally with the uploads list.

    Raises ``ValidationFailedError`` with pointer
    ``#/files/<index>/filename`` on:
      * forbidden character, Windows reserved name, trailing dot, etc.
      * filename collision within a single request (two uploads map
        to the same name after NFC + sanitize). The filesystem cannot
        hold two files with the same name; D25 step 2 would
        ``atomic_write_stream`` the second over the first, silently
        discarding the first attachment's bytes. Rejecting pre-write
        surfaces the error with a clean error location instead.
    """
    errors: list[FieldError] = []
    sanitized: list[str] = []
    seen: dict[str, int] = {}  # canonical name → first index that claimed it

    for i, up in enumerate(uploads):
        try:
            name = sanitize_filename(up.filename)
        except ValueError as exc:
            errors.append(
                FieldError(
                    pointer=f"#/files/{i}/filename",
                    detail=str(exc),
                )
            )
            sanitized.append("")  # placeholder; never used when errors non-empty
            continue
        if name in seen:
            errors.append(
                FieldError(
                    pointer=f"#/files/{i}/filename",
                    detail=(
                        f"duplicate filename {name!r} "
                        f"(also at index {seen[name]})"
                    ),
                )
            )
        else:
            seen[name] = i
        sanitized.append(name)

    if errors:
        raise ValidationFailedError("invalid attachment filenames", errors=errors)
    return sanitized


@with_writer_lock
def create_jump(
    logbook_root: Path,
    user_id: str,
    payload: JumpCreate,
    uploads: Iterable[Upload] | None = None,
) -> Jump:
    """Persist a new jump and return the canonical ``Jump``.

    Per D25's write-ordering spec, with D30 attachment support:
      1. mkdir target folder with ``exist_ok=False`` (D23 kernel-level
         backstop; prefix scan above is the service-level primary
         gate).
      2. For each upload, in one streaming pass: compute SHA-256 and
         atomic-write to ``<folder>/<filename>`` via
         ``atomic_write_stream``. The hash and the bytes are committed
         together — they agree by construction (D25 step 2).
      3. Build the canonical ``Jump`` with the attachments list, then
         ``atomic_write`` ``jump.xml`` with every
         ``<attachment>/<sha256>`` populated from step 2.
      4. ``atomic_write`` ``SHA256SUMS`` via
         ``manifest.from_jump_xml`` (recovery-path-shaped; a crash
         between steps 3 and 4 heals cleanly on next open via
         ``folder_reconcile``).
      5. ``summary.md`` — deferred (D5).

    Parameters
    ----------
    uploads:
        Optional iterable of :class:`Upload` to attach. ``None`` or an
        empty iterable creates a jump with no attachments (identical
        to the v0.1 behaviour before D30 landed).

    Raises:
      ``ConflictError``: jump_number already in use (index or
        filesystem collision).
      ``ValidationFailedError``: Pydantic rejected the payload, the
        title produces an invalid folder name, or an attachment
        filename is bad or duplicated within the request.
      ``XSDValidationError``: serialized XML failed schema validation
        (should not happen with a Pydantic-validated ``Jump``;
        surfaced as a server bug rather than swallowed).
    """
    from pydantic import ValidationError

    uploads_list: list[Upload] = list(uploads) if uploads else []

    try:
        jump = Jump(
            id=uuid4(),
            **payload.model_dump(),
        )
    except ValidationError as exc:
        field_errors = [
            FieldError(
                pointer=field_pointer(*err["loc"]),
                detail=err["msg"],
            )
            for err in exc.errors()
        ]
        raise ValidationFailedError(
            "invalid jump payload", errors=field_errors
        ) from exc

    # Stamp audit timestamps (D17). ``created_at == updated_at`` on
    # insert; ``update_jump`` bumps ``updated_at`` only.
    now = now_utc_iso()

    # Compute the folder name up-front (D4). ``jump_folder_name`` runs
    # the full string through ``sanitize_folder_name``; a title
    # containing forbidden characters raises ``ValueError`` here.
    try:
        folder_name = jump_folder_name(jump.jump_number, jump.title)
    except ValueError as exc:
        raise ValidationFailedError(
            f"title produces an invalid folder name: {exc}"
        ) from exc

    # Validate every attachment filename BEFORE any disk write. Keeps
    # a 422 from leaving behind a half-made folder.
    sanitized_filenames = _sanitize_upload_filenames(uploads_list)

    jumps_dir = logbook_root / _ACTIVE_JUMPS_DIR
    jump_folder = jumps_dir / folder_name
    rel_folder = f"{_ACTIVE_JUMPS_DIR}/{folder_name}"

    # D23 §Filesystem backstop: scan ``jumps/`` for any entry equal to
    # ``[<N>]`` or starting with ``[<N>] `` before any write. This is
    # the primary uniqueness gate today; the SQLite UNIQUE constraint
    # at INSERT time is the secondary gate (index can drift per D3).
    if _jump_number_is_taken(logbook_root, user_id, jump.jump_number):
        _raise_jump_number_conflict(jump.jump_number)

    # D25 step 1: ``mkdir(exist_ok=False)`` on the target folder.
    # Acts as the kernel-level backstop in case the prefix-scan missed
    # (e.g. a race between scan and mkdir) — for the exact-name case
    # only. For differing-title collisions the prefix-scan above is
    # what catches them.
    jumps_dir.mkdir(parents=True, exist_ok=True)
    try:
        jump_folder.mkdir(exist_ok=False)
    except FileExistsError:
        _raise_jump_number_conflict(jump.jump_number)

    # From here on we're writing authoritative bytes. A crash between
    # steps rolls forward on next open via ``folder_reconcile`` (D25).
    try:
        # D25 step 2: stream-hash-write each attachment. ``hash`` and
        # ``size`` come from ``atomic_write_stream`` in one pass over
        # the bytes, so the values we put in ``jump.xml`` match what
        # actually reached the disk — agreement by construction.
        attachments: list[Attachment] = []
        for up, filename in zip(uploads_list, sanitized_filenames, strict=True):
            stream_result = atomic_write_stream(
                jump_folder / filename, up.chunks
            )
            attachments.append(
                Attachment(
                    filename=filename,
                    sha256=stream_result.sha256,
                    size=stream_result.size,
                    content_type=up.content_type,
                )
            )

        # Rebuild the Jump carrying the attachments (each with the
        # sha256 we just computed) and the D32 audit timestamps
        # (created_at == updated_at on insert; bumped by update_jump).
        # The payload dump does NOT carry attachments today — the REST
        # layer keeps attachments out of JumpCreate per D30 (they
        # arrive as multipart ``files`` parts, not inside the JSON
        # ``jump`` field) — nor D32 timestamps (service-owned).
        jump = jump.model_copy(
            update={
                "attachments": attachments,
                "created_at": now,
                "updated_at": now,
            }
        )

        # D25 steps 3+4: serialize → XSD-validate → atomic_write
        # jump.xml → atomic_write SHA256SUMS from the new XML claims.
        # With attachments present, from_jump_xml produces one manifest
        # line per attachment plus one for jump.xml itself.
        _write_jump_and_manifest(jump_folder, jump, logbook_root)
    except Exception:
        # D25: we do NOT auto-cleanup. A half-written folder (mkdir
        # done, some attachments on disk, no jump.xml) is in the
        # "incomplete folder" crash state — verify flags it; reindex
        # skips it. Auto-rmtree here would mask the underlying error.
        raise

    # D3: index row AFTER XML is durable on disk. A crash between XML
    # write and index insert just means the next reindex picks up the
    # folder (index is rebuildable).
    with _index_conn(logbook_root) as conn:
        try:
            conn.execute(
                "INSERT INTO jumps (id, user_id, jump_number, date, dropzone, "
                "title, aircraft, discipline, freefall_time_s, rig_id, "
                "is_tandem, dropzone_id, folder, schema_ns, created_at, "
                "updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(jump.id),
                    user_id,
                    jump.jump_number,
                    jump.date.isoformat(),
                    jump.dropzone,
                    jump.title,  # may be None — column is nullable
                    jump.aircraft,
                    jump.discipline,
                    jump.freefall_time_s,
                    str(jump.rig_id) if jump.rig_id else None,  # v7
                    # v8 (D47, Phase D.4): cache <is_tandem> for the
                    # currency calculator. ``None`` and ``False``
                    # both map to NULL on disk so a jump.xml that
                    # elides the element round-trips byte-stable.
                    1 if jump.is_tandem else None,
                    # v10 (D60): cache <dropzone_id> for the starred-DZ
                    # successor-election query in dropzone_service.
                    # ``None`` (quick-log jumps without a DZ pick, or
                    # pre-D60 legacy data) maps to NULL on disk.
                    str(jump.dropzone_id) if jump.dropzone_id else None,
                    rel_folder,
                    SCHEMA_NAMESPACE_V1,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            # D23 UNIQUE constraint (user_id, jump_number) — our
            # service-layer scan missed a collision (index-filesystem
            # divergence). Surface the same 409 ``ConflictError``.
            # The partially-written folder is now an orphan; verify
            # flags it on next run. We do NOT rollback the folder
            # because D25 is explicit that XML is truth; a half-built
            # folder with a valid jump.xml is recoverable state.
            _logger.warning(
                "create_jump_index_conflict",
                extra={
                    "jump_id": str(jump.id),
                    "user_id": user_id,
                    "jump_number": jump.jump_number,
                    "folder": rel_folder,
                    "error": str(exc),
                },
            )
            _raise_jump_number_conflict(jump.jump_number)

    _logger.info(
        "jump_created",
        extra={
            "jump_id": str(jump.id),
            "user_id": user_id,
            "jump_number": jump.jump_number,
            "folder": rel_folder,
            "attachment_count": len(jump.attachments),
        },
    )
    return jump


@with_writer_lock
def get_jump(logbook_root: Path, user_id: str, jump_id: UUID) -> Jump:
    """Read a jump by id. Raises ``NotFoundError`` if missing.

    Flow:
      1. SELECT the index row by ``(id, user_id)``. Miss → 404.
      2. Resolve the folder from ``row.folder`` (stored relative to
         ``logbook_root``).
      3. ``folder_reconcile`` — D25's cheap, no-byte-reading heal.
         If ``SHA256SUMS`` is absent or disagrees with ``jump.xml``'s
         claims, regenerate it. Idempotent; no-op on already-reconciled
         folders.
      4. Parse ``jump.xml`` through the hardened parser, XSD-validate
         against the namespace declared in the file.
      5. Deserialize into the ``Jump`` model and return.

    Raises:
      ``NotFoundError``: no index row for ``(id, user_id)``. Per D3 the
        index is authoritative for existence queries (reindex is the
        authoritative repair if a real jump exists on disk but is
        missing from the index).
      ``IntegrityError`` / subclasses: the folder exists but
        ``jump.xml`` is missing, malformed, or XSD-invalid — caller
        likely needs to run ``verify``. Surfaced as 500 per D16.
    """
    folder = _get_jump_folder(logbook_root, jump_id, user_id)

    # D25 on-open reconciliation: heal a stale SHA256SUMS before the
    # caller sees the jump. No attachment bytes are read.
    folder_reconcile(folder, logbook_root=logbook_root)

    # Hardened parse + XSD validate per D2 invariant.
    jump_xml_bytes = (folder / JUMP_XML_NAME).read_bytes()
    element = xml_parse(jump_xml_bytes)
    validate(element)
    return element_to_jump(element)


@dataclass(frozen=True)
class FolderFile:
    """One file inside a jump folder, as seen by ``list_jump_files``.

    Tracked vs untracked:
      * ``tracked = True`` — the file appears in ``jump.xml``'s
        ``<attachments>`` element. ``sha256``, ``size``, and
        ``content_type`` come from the canonical record.
      * ``tracked = False`` — the file exists in the folder but is not
        in the canonical record. Most often this means the user dropped
        it in via Finder / Explorer / ``cp`` after the jump was logged.
        For v0.1 we surface these as read-only — ingesting them into
        the manifest needs the ``update_jump`` attachment-edit flow
        deferred per D31.

    System files (``jump.xml``, ``SHA256SUMS``, dotfiles like
    ``.DS_Store``) are excluded entirely from the listing — they are
    not user-facing attachments.
    """

    filename: str
    size: int
    tracked: bool
    sha256: str | None = None
    content_type: str | None = None


@with_writer_lock
def list_jump_files(
    logbook_root: Path, user_id: str, jump_id: UUID
) -> list[FolderFile]:
    """Return every user-facing file in a jump folder.

    Combines the canonical attachment list (from ``jump.xml``) with a
    fresh filesystem scan, so the response includes both tracked
    attachments and any extra files the user has dropped in via the
    OS file manager since the jump was logged.

    Why scan instead of trusting the XML alone: from a jumper's
    perspective the folder IS their attachment store — if they put a
    video in there yesterday, they expect to see it today regardless
    of whether the manifest has caught up. Per D2 the XML stays the
    source of truth for *tracked* state; this function does not mutate
    anything, it just exposes what's on disk so the UI can be honest.

    Raises ``NotFoundError`` (→ 404 per D16) when the jump id doesn't
    belong to ``user_id``.
    """
    jump = get_jump(logbook_root, user_id, jump_id)

    # Resolve the folder path the same way ``get_jump`` did — defensive,
    # since get_jump above would have raised on a missing id.
    folder = _get_jump_folder(logbook_root, jump_id, user_id)

    tracked_by_name = {a.filename: a for a in jump.attachments}

    files: list[FolderFile] = []
    for path in sorted(folder.iterdir()):
        if not path.is_file():
            continue
        name = path.name
        # Exclude the canonical record + manifest + hidden files.
        # Future system files (e.g. signature.sig per D6) get added
        # here when they ship.
        if name in {JUMP_XML_NAME, MANIFEST_NAME} or name.startswith("."):
            continue
        attachment = tracked_by_name.get(name)
        if attachment is not None:
            files.append(
                FolderFile(
                    filename=name,
                    size=attachment.size,
                    tracked=True,
                    sha256=attachment.sha256,
                    content_type=attachment.content_type,
                )
            )
        else:
            files.append(
                FolderFile(
                    filename=name,
                    size=path.stat().st_size,
                    tracked=False,
                )
            )
    return files


@with_writer_lock
def track_files(
    logbook_root: Path,
    user_id: str,
    jump_id: UUID,
    filenames: list[str],
) -> Jump:
    """Adopt files already on disk into ``jump.xml``'s ``<attachments>`` (D41).

    For each filename in ``filenames`` that exists in the jump folder
    and isn't already tracked, this function:

    1. Reads the file streamingly to compute its SHA-256.
    2. Infers a content type from the file extension (or leaves it
       unset when the standard library doesn't know).
    3. Appends an ``Attachment`` to the jump's ``<attachments>``.

    Then writes the updated ``jump.xml`` atomically, regenerates
    ``SHA256SUMS`` via ``from_jump_xml``, and updates the index's
    ``updated_at`` (the row otherwise stays unchanged — folder, title,
    jump_number, date, dropzone are all the same).

    Idempotent: re-tracking an already-tracked filename is a no-op,
    no rewrite. Returns the canonical ``Jump`` either way.

    Raises:
      ``NotFoundError``: jump not found for this user.
      ``ValidationFailedError`` (422):
        - ``filename_invalid``: a filename failed D4 sanitization.
        - ``filename_not_in_folder``: a filename has no matching file
          on disk.
    """
    import hashlib
    import mimetypes

    # Step 1: load the current Jump (validates ownership, parses XML,
    # returns the canonical attachments list).
    jump = get_jump(logbook_root, user_id, jump_id)

    # Step 2: resolve the folder path. Same pattern as get_jump and
    # update_jump.
    folder = _get_jump_folder(logbook_root, jump_id, user_id)

    # Step 3: validate each requested filename. Two failure modes,
    # both 422 with per-index pointers per D16. Sanitize first so a
    # path-injection attempt fails before we even hit the disk.
    sanitized: list[str] = []
    errors: list[FieldError] = []
    for i, raw in enumerate(filenames):
        try:
            sanitized.append(sanitize_filename(raw))
        except ValueError as exc:
            errors.append(
                FieldError(
                    pointer=f"#/filenames/{i}",
                    detail=f"invalid filename: {exc}",
                )
            )
    if errors:
        raise ValidationFailedError(
            "one or more filenames failed sanitization", errors=errors
        )

    # Step 4: figure out which filenames need tracking, which are no-ops.
    already_tracked = {a.filename for a in jump.attachments}
    to_track: list[str] = []
    for i, name in enumerate(sanitized):
        if name in already_tracked:
            # Idempotent — skip, don't error.
            continue
        path = folder / name
        if not path.is_file():
            errors.append(
                FieldError(
                    pointer=f"#/filenames/{i}",
                    detail=f"{name!r} does not exist in the jump folder",
                )
            )
            continue
        to_track.append(name)
    if errors:
        raise ValidationFailedError(
            "one or more filenames not found in folder", errors=errors
        )
    if not to_track:
        # Nothing to do — all filenames were already tracked. Return
        # the unchanged Jump rather than rewriting XML for nothing.
        return jump

    # Step 5: build new Attachment entries. SHA-256 streamed in 64 KiB
    # chunks to keep memory bounded for large videos (D21).
    new_attachments: list[Attachment] = []
    for name in to_track:
        path = folder / name
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(64 * 1024), b""):
                h.update(chunk)
        size = path.stat().st_size
        # mimetypes returns None when the extension is unfamiliar.
        # Pydantic accepts None on Attachment.content_type so we
        # surface that honestly rather than guessing.
        content_type, _ = mimetypes.guess_type(name)
        new_attachments.append(
            Attachment(
                filename=name,
                sha256=h.hexdigest(),
                size=size,
                content_type=content_type,
            )
        )

    # Step 6: rebuild the Jump model with the appended attachments.
    # Preserve order: existing first, then newly tracked (oldest write
    # first matches the order the user added them, which mirrors how
    # create_jump handles the multipart `files` parts).
    now = now_utc_iso()
    updated = jump.model_copy(
        update={
            "attachments": [*jump.attachments, *new_attachments],
            "updated_at": now,
        }
    )

    # Steps 7-9 (D25 ordering): serialize, XSD-validate, atomic_write
    # jump.xml, then atomic_write SHA256SUMS from the new XML claims.
    _write_jump_and_manifest(folder, updated, logbook_root)

    # Step 10: bump updated_at in the index. The row's other fields
    # (folder, jump_number, date, dropzone, title) didn't change, so
    # we keep the UPDATE narrow.
    with _index_conn(logbook_root) as conn:
        conn.execute(
            "UPDATE jumps SET updated_at = ? WHERE id = ? AND user_id = ?",
            (now, str(jump_id), user_id),
        )
        conn.commit()

    _logger.info(
        "files_tracked",
        extra={
            "jump_id": str(jump_id),
            "user_id": user_id,
            "tracked": to_track,
        },
    )

    return updated


@with_writer_lock
def add_attachments(
    logbook_root: Path,
    user_id: str,
    jump_id: UUID,
    uploads: list[Upload],
) -> Jump:
    """Append uploaded files to an existing jump's attachments (D42).

    Mirrors :func:`create_jump`'s attachment handling but against an
    existing folder. Add-only: this function never removes or
    replaces existing attachments. Filename collisions are rejected
    with structured errors so the caller can rename or use D41's
    track flow.

    Crash semantics (D25 §B): if a stream-write succeeds but
    ``jump.xml`` doesn't get updated, the new file lives on disk as
    an untracked attachment — visible via ``list_jump_files`` (D37),
    flagged by ``verify``, can be ingested via :func:`track_files`.
    No automatic cleanup; XML on disk stays the source of truth.

    Raises:
      ``NotFoundError`` (404): jump not found for this user.
      ``ValidationFailedError`` (422):
        - sanitization failure on any filename (per-file pointers
          ``#/files/<i>/filename``).
        - duplicate filenames within the request.
      ``JumpNumberConflict`` not raised (jump number isn't touched).
      Any of the D25 ``ConflictError`` subclasses on filename
      collisions (already-attached, already-on-disk).
    """
    if not uploads:
        # Empty request — return the unchanged Jump without rewriting
        # XML. Mirrors track_files's no-op-when-nothing-to-track path.
        return get_jump(logbook_root, user_id, jump_id)

    # Step 1: resolve the existing Jump + folder.
    jump = get_jump(logbook_root, user_id, jump_id)
    folder = _get_jump_folder(logbook_root, jump_id, user_id)

    # Step 2: sanitize + de-duplicate within the request (D30).
    sanitized = _sanitize_upload_filenames(uploads)

    # Step 3: reject filenames already in the canonical attachments
    # list. The user wanted "add", not "replace" — replacing is still
    # deferred per D31. Surface the conflict pre-write so no bytes
    # touch disk.
    already_attached = {a.filename for a in jump.attachments}
    errors: list[FieldError] = []
    for i, name in enumerate(sanitized):
        if name in already_attached:
            errors.append(
                FieldError(
                    pointer=f"#/files/{i}/filename",
                    detail=(
                        f"{name!r} is already attached to this jump. "
                        "Replacing existing attachments is deferred to a "
                        "later phase (D31); for now, rename and re-upload."
                    ),
                )
            )
    if errors:
        raise ValidationFailedError(
            "one or more filenames are already attached",
            errors=errors,
        )

    # Step 4: reject filenames that already exist as untracked files
    # on disk. Silent overwrite would lose the user's drop-in; the
    # right path is D41's track flow.
    for i, name in enumerate(sanitized):
        if (folder / name).exists():
            errors.append(
                FieldError(
                    pointer=f"#/files/{i}/filename",
                    detail=(
                        f"{name!r} is already in the folder (not yet tracked). "
                        "Use the Track action to ingest it instead of re-uploading."
                    ),
                )
            )
    if errors:
        raise ValidationFailedError(
            "one or more filenames already exist in the folder",
            errors=errors,
        )

    # Step 5: stream-hash-write each upload. Same one-pass approach
    # create_jump uses — bytes go to disk while sha256 accumulates,
    # so the value we put into jump.xml matches what reached disk.
    new_attachments: list[Attachment] = []
    for up, name in zip(uploads, sanitized, strict=True):
        stream_result = atomic_write_stream(folder / name, up.chunks)
        new_attachments.append(
            Attachment(
                filename=name,
                sha256=stream_result.sha256,
                size=stream_result.size,
                content_type=up.content_type,
            )
        )

    # Step 6: rebuild the Jump with appended attachments + bumped
    # updated_at. Existing attachments stay first (preserves order).
    now = now_utc_iso()
    updated = jump.model_copy(
        update={
            "attachments": [*jump.attachments, *new_attachments],
            "updated_at": now,
        }
    )

    # Steps 7-9 (D25 ordering): serialize, XSD-validate, atomic_write
    # jump.xml, then atomic_write SHA256SUMS from the new XML claims.
    _write_jump_and_manifest(folder, updated, logbook_root)

    # Step 10: bump the index row's updated_at. Other columns are
    # unchanged — folder, jump_number, date, dropzone, title all
    # stay put.
    with _index_conn(logbook_root) as conn:
        conn.execute(
            "UPDATE jumps SET updated_at = ? WHERE id = ? AND user_id = ?",
            (now, str(jump_id), user_id),
        )
        conn.commit()

    _logger.info(
        "attachments_added",
        extra={
            "jump_id": str(jump_id),
            "user_id": user_id,
            "added": [a.filename for a in new_attachments],
        },
    )

    return updated


@with_writer_lock
def delete_attachment(
    logbook_root: Path,
    user_id: str,
    jump_id: UUID,
    filename: str,
) -> Jump:
    """Remove one attachment from a jump's manifest and unlink it (D43).

    Order of operations matters for crash recovery:
      1. Sanitize the filename (D4 character rules).
      2. Resolve the jump + folder.
      3. Verify the filename is in ``<attachments>`` (404 otherwise).
      4. Rebuild Jump without that attachment.
      5. XSD-validate (D2).
      6. ``atomic_write`` jump.xml (without the entry).
      7. Regenerate SHA256SUMS from the new XML claims.
      8. ``os.unlink`` the file from disk.
      9. Bump the index row's ``updated_at``.

    A crash between step 7 and step 8 leaves the file on disk as an
    untracked drop-in — same state as a manually-added file. The
    canonical record (jump.xml + SHA256SUMS) is internally consistent
    and the orphan file shows up in :func:`list_jump_files` with
    ``tracked=False``. Verify flags it as ``extra_file``. The user
    can recover by re-tracking via D41 or by deleting it from the
    file manager.

    Hard delete — no soft-delete to ``.trash/``. Per D43's rationale,
    individual attachments are a small loss that's typically
    recoverable from external backups; folder-level soft-delete is
    a larger blast radius (hence D19's existence).

    Raises:
      ``NotFoundError`` (404):
        - jump not found for this user, OR
        - filename isn't in the jump's ``<attachments>``.
      ``ValidationFailedError`` (422): filename failed D4 sanitization.
    """
    # Step 1: sanitize the filename so a malformed URL parameter
    # cannot escape the folder. ``sanitize_filename`` rejects path
    # separators, control characters, Windows reserved names.
    try:
        safe = sanitize_filename(filename)
    except ValueError as exc:
        raise ValidationFailedError(
            "invalid filename",
            errors=[FieldError(pointer="#/filename", detail=str(exc))],
        ) from exc

    # Step 2: load the existing Jump + folder.
    jump = get_jump(logbook_root, user_id, jump_id)
    folder = _get_jump_folder(logbook_root, jump_id, user_id)

    # Step 3: confirm the filename is actually in <attachments>.
    # Untracked drop-ins are explicitly out of scope (D43) — those
    # are the user's to manage via the file manager, since they were
    # never in the canonical record to begin with.
    matching = [a for a in jump.attachments if a.filename == safe]
    if not matching:
        raise NotFoundError(
            f"attachment {safe!r} not found on jump {jump_id}"
        )

    # Step 4: rebuild the Jump without that attachment. Pydantic
    # creates a new immutable model — we never mutate ``jump`` in
    # place, which keeps reasoning simple.
    now = now_utc_iso()
    remaining = [a for a in jump.attachments if a.filename != safe]
    updated = jump.model_copy(
        update={"attachments": remaining, "updated_at": now}
    )

    # Steps 5-7 (D25 ordering): serialize, XSD-validate, atomic_write
    # jump.xml, then atomic_write SHA256SUMS from the new XML claims
    # (one fewer line, or none if this was the last attachment).
    _write_jump_and_manifest(folder, updated, logbook_root)

    # Step 8: unlink the file. After this point the operation is
    # complete; on-disk state and canonical record agree. A crash
    # before this step leaves an orphan recoverable via D41 or
    # external file management, per D43 §"Crash semantics".
    target = folder / safe
    # Race with manual deletion — the file is already gone, so the
    # user already got what they wanted. Don't error.
    with contextlib.suppress(FileNotFoundError):
        target.unlink()

    # Step 9: bump updated_at in the index row.
    with _index_conn(logbook_root) as conn:
        conn.execute(
            "UPDATE jumps SET updated_at = ? WHERE id = ? AND user_id = ?",
            (now, str(jump_id), user_id),
        )
        conn.commit()

    _logger.info(
        "attachment_deleted",
        extra={
            "jump_id": str(jump_id),
            "user_id": user_id,
            # ``filename`` is a reserved LogRecord attribute (stdlib
            # logging stamps the source file's name into it), so the
            # logger refuses to overwrite it via ``extra=``. Use a
            # distinct key.
            "attachment": safe,
        },
    )

    return updated


def list_jumps(
    logbook_root: Path, user_id: str, *, limit: int = 100, offset: int = 0
) -> list[JumpSummary]:
    """List jumps in reverse-chronological order (Phase 3.1).

    Returns a list of ``JumpSummary`` — the slimmed projection populated
    directly from the SQLite index. No per-row XML read, no per-row
    reconcile. List views stay fast; single-jump reads go through
    ``get_jump`` which does the parse + reconcile.

    Ordering: ``date DESC, jump_number DESC`` so the most recent
    jump appears first with jumps on the same day broken by
    descending number (later jump first).

    ``limit`` and ``offset`` are simple offset pagination. For v0.1 this
    is sufficient; a cursor-based scheme can land additively if
    profiling shows the offset pagination underperforms on large
    logbooks.
    """
    with _index_conn(logbook_root) as conn:
        rows = conn.execute(
            "SELECT id, jump_number, title, date, dropzone, "
            "aircraft, discipline, freefall_time_s, rig_id FROM jumps "
            "WHERE user_id = ? "
            "ORDER BY date DESC, jump_number DESC "
            "LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        ).fetchall()

    return [
        JumpSummary(
            id=row["id"],
            jump_number=row["jump_number"],
            title=row["title"],
            date=row["date"],
            dropzone=row["dropzone"],
            aircraft=row["aircraft"],
            discipline=row["discipline"],
            freefall_time_s=row["freefall_time_s"],
            rig_id=row["rig_id"],  # v7 — None for legacy / no-rig jumps
        )
        for row in rows
    ]


@with_writer_lock
def update_jump(
    logbook_root: Path,
    user_id: str,
    jump_id: UUID,
    payload: JumpUpdate,
) -> Jump:
    """Apply a metadata edit to an existing jump (Phase 3.5, D31).

    v0.1 scope: metadata only — attachments and the ``id`` are
    preserved from the on-disk jump. The attachment-edit flow lands
    in a later phase with its own transport D-entry.

    Nine-step ordering per D31:

      1. Look up the current jump by id; 404 on miss.
      2. Merge payload onto the current jump (attachments preserved).
         Pydantic validation catches bad fields → 422.
      3. D23 prefix-scan for a collision on any new ``jump_number``
         that differs from the current one. 409 on hit. No scan
         needed when the number is unchanged.
      4. Compute the new folder name; bad title → 422.
      5. Serialize + XSD-validate the updated Jump.
      6. ``atomic_write`` ``jump.xml`` at the CURRENT folder path.
      7. ``atomic_write`` ``SHA256SUMS`` at the current folder path
         from ``from_jump_xml`` (recovery-path-shaped; survives a
         crash between 6 and 7 via ``folder_reconcile`` on next
         open).
      8. If the folder name changed, ``os.rename`` it. POSIX-atomic;
         the target must not exist, guaranteed by step 3.
      9. Update the index row: ``jump_number``, ``title``, ``folder``,
         ``updated_at``. ``created_at`` is preserved.

    D6 signature-strip: reserved — when signing lands, this function
    drops any ``<signature>`` element before the serialize step. Not
    yet implemented because signing is deferred.

    Raises:
      ``NotFoundError``: no jump with this ``(id, user_id)``.
      ``JumpNumberConflict``: new jump_number already taken.
      ``ValidationFailedError``: bad payload or title.
    """
    from pydantic import ValidationError

    # Step 1: fetch current jump.
    current = get_jump(logbook_root, user_id, jump_id)

    # D32: preserve created_at (set once at create time, never
    # mutated thereafter) and bump updated_at to "now". Service is
    # the only author of these fields, so they're computed here and
    # not in the payload.
    now = now_utc_iso()

    # Step 2: merge payload onto the current jump. Preserve id,
    # attachments, signature (D6 reserved; signature is stripped by
    # a future D6 implementation before this point), and created_at
    # (D32). Stamp a fresh updated_at.
    try:
        updated = current.model_copy(
            update={
                **payload.model_dump(),
                "id": current.id,
                "attachments": current.attachments,
                "signature": current.signature,
                "created_at": current.created_at,
                "updated_at": now,
            }
        )
        # Re-validate — model_copy bypasses validators by default.
        # This surfaces e.g. a discipline set to an empty string or
        # any other field-level rule that Jump enforces.
        updated = Jump.model_validate(updated.model_dump())
    except ValidationError as exc:
        field_errors = [
            FieldError(
                pointer=field_pointer(*err["loc"]),
                detail=err["msg"],
            )
            for err in exc.errors()
        ]
        raise ValidationFailedError(
            "invalid jump payload", errors=field_errors
        ) from exc

    # Step 3: D23 collision check (only when jump_number changes).
    if (
        updated.jump_number != current.jump_number
        and _jump_number_is_taken(logbook_root, user_id, updated.jump_number)
    ):
        _raise_jump_number_conflict(updated.jump_number)

    # Step 4: new folder name. D4 sanitization on the full string
    # catches forbidden characters in the title.
    try:
        new_folder_name = jump_folder_name(updated.jump_number, updated.title)
    except ValueError as exc:
        raise ValidationFailedError(
            f"title produces an invalid folder name: {exc}"
        ) from exc

    # Resolve current folder path from the index (authoritative for
    # the on-disk location; get_jump already relied on it).
    current_folder = _get_jump_folder(logbook_root, jump_id, user_id)
    new_folder_rel = f"{_ACTIVE_JUMPS_DIR}/{new_folder_name}"
    new_folder = logbook_root / new_folder_rel

    # Steps 5-7 (D25 ordering): serialize, XSD-validate, atomic_write
    # jump.xml at the CURRENT path, then atomic_write SHA256SUMS
    # there too — folder rename happens after.
    _write_jump_and_manifest(current_folder, updated, logbook_root)

    # Step 8: folder rename if needed. POSIX-atomic; target must not
    # exist (step 3's collision check guarantees this for the
    # different-jump_number case; the same-number-different-title
    # case is covered by the uniqueness of [<N>] prefix).
    if new_folder != current_folder:
        os.rename(current_folder, new_folder)

    # Step 9: index row update. Reuse the ``now`` computed for XML
    # step 2 so jump.xml and the index row agree on updated_at — a
    # fresh now_utc_iso() here would produce a ms-off timestamp.
    with _index_conn(logbook_root) as conn:
        try:
            conn.execute(
                "UPDATE jumps SET jump_number = ?, date = ?, dropzone = ?, "
                "title = ?, aircraft = ?, discipline = ?, freefall_time_s = ?, "
                "rig_id = ?, is_tandem = ?, dropzone_id = ?, folder = ?, "
                "updated_at = ? "
                "WHERE id = ? AND user_id = ?",
                (
                    updated.jump_number,
                    updated.date.isoformat(),
                    updated.dropzone,
                    updated.title,
                    updated.aircraft,
                    updated.discipline,
                    updated.freefall_time_s,
                    str(updated.rig_id) if updated.rig_id else None,  # v7
                    # v8 (D47, Phase D.4): see create_jump comment.
                    1 if updated.is_tandem else None,
                    # v10 (D60): see create_jump comment.
                    str(updated.dropzone_id) if updated.dropzone_id else None,
                    new_folder_rel,
                    now,
                    str(jump_id),
                    user_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            # D23 UNIQUE(user_id, jump_number) — index-layer catch
            # for a race between step 3's scan and step 9's update.
            # At this point the jump.xml and folder are already
            # updated (steps 6–8). Leave them: the folder has a
            # valid jump.xml with the new number, and the index is
            # rebuildable. User retries update with a different
            # number.
            _logger.warning(
                "update_jump_index_conflict",
                extra={
                    "jump_id": str(jump_id),
                    "user_id": user_id,
                    "jump_number": updated.jump_number,
                    "folder": new_folder_rel,
                    "error": str(exc),
                },
            )
            _raise_jump_number_conflict(updated.jump_number)

    _logger.info(
        "jump_updated",
        extra={
            "jump_id": str(jump_id),
            "user_id": user_id,
            "jump_number": updated.jump_number,
            "folder": new_folder_rel,
            "folder_renamed": new_folder != current_folder,
        },
    )
    return updated


@with_writer_lock
def delete_jump(
    logbook_root: Path, user_id: str, jump_id: UUID
) -> None:
    """Soft-delete a jump (D19, D31).

    Moves the jump folder into ``.trash/<timestamp>_<original-name>/``
    and deletes the index row. The folder remains on disk and can be
    restored manually (``.trash/.../ → jumps/``) plus a reindex.

    Raises:
      ``NotFoundError``: no jump with this ``(id, user_id)``.
    """
    # Look up the current folder path via the index. Authoritative
    # for existence (D3) — if the index says no, it's a 404 even if
    # the filesystem has a stray folder (which would need a reindex).
    folder = _get_jump_folder(logbook_root, jump_id, user_id)
    folder_rel = str(folder.relative_to(logbook_root))

    # soft_delete (D19) handles the timestamp-prefixed move into
    # .trash/ and the uniquifier on the rare name collision.
    trashed = soft_delete(folder, logbook_root)

    # Index row removal. Done AFTER the move so that, in the unlikely
    # event the move fails partway, the index still points at the
    # old path and the jump remains discoverable. shutil.move does
    # its own cleanup on failure so a partial-move is unlikely, but
    # the ordering is cheap insurance.
    with _index_conn(logbook_root) as conn:
        conn.execute(
            "DELETE FROM jumps WHERE id = ? AND user_id = ?",
            (str(jump_id), user_id),
        )

    _logger.info(
        "jump_deleted",
        extra={
            "jump_id": str(jump_id),
            "user_id": user_id,
            "folder": folder_rel,
            "trashed_to": str(trashed.relative_to(logbook_root)),
        },
    )
