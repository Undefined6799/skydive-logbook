"""REST endpoints for jumps (Phase 3.2 + Phase 3.3 + Phase 3.5).

Thin by design (D7): each handler is a translation layer — pull
``logbook_root`` and ``user_id`` from dependencies, call the service
function, let ``ServiceError`` subclasses bubble up to the
``on_service_error`` handler in ``rest.py`` which emits RFC 9457
problem+json (D16).

Routes:

  * ``POST   /api/v1/jumps``           → ``create_jump``.
    ``multipart/form-data`` per D30: one ``jump`` field (JSON body
    matching ``JumpCreate``) plus zero or more ``files`` parts.
    201 + ``Location`` header on success.
  * ``GET    /api/v1/jumps``           → ``list_jumps``, paginated.
  * ``GET    /api/v1/jumps/{jump_id}`` → ``get_jump``.
  * ``PUT    /api/v1/jumps/{jump_id}`` → ``update_jump``. JSON body
    matching ``JumpUpdate`` per D31 (metadata-only in v0.1).
  * ``DELETE /api/v1/jumps/{jump_id}`` → ``delete_jump``. 204 on
    success; soft delete to ``.trash/`` per D19.

Future:

  * Attachment editing via PUT — deferred past v0.1 per D31.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from pydantic import BaseModel, ValidationError

from ..api.errors import FieldError, ValidationFailedError, field_pointer
from ..models.jump import Jump, JumpCreate, JumpSummary, JumpUpdate
from ..services import jump_service
from ..services.jump_service import Upload
from .deps import get_logbook_root, get_user_id


class FolderFileResponse(BaseModel):
    """REST projection of ``FolderFile`` (cannot use the dataclass
    directly because FastAPI's response_model expects a Pydantic shape).
    """

    filename: str
    size: int
    tracked: bool
    sha256: str | None = None
    content_type: str | None = None


class TrackFilesRequest(BaseModel):
    """Request body for ``POST /api/v1/jumps/{id}/attachments/track`` (D41)."""

    filenames: list[str]

router = APIRouter(prefix="/api/v1/jumps", tags=["jumps"])

# 64 KiB per read — small enough to keep memory bounded on
# multi-gigabyte uploads (D21), large enough to avoid per-chunk
# syscall overhead on small files. The exact value is an
# implementation detail; the storage primitive doesn't care.
_UPLOAD_CHUNK_SIZE = 64 * 1024


def _upload_chunks(upload: UploadFile) -> Iterator[bytes]:
    """Yield ``upload.file`` in fixed-size chunks (D21 streaming).

    UploadFile wraps a ``SpooledTemporaryFile`` (default 1 MiB spool
    before spill — see starlette.datastructures.UploadFile) so reads
    are already bounded in memory at the framework layer. We chunk
    here for :func:`atomic_write_stream`'s loop. Reads happen from
    the synchronous ``.file`` attribute so this generator composes
    with sync route handlers — matching ``create_jump``'s sync
    signature (D7).
    """
    f = upload.file
    while True:
        chunk = f.read(_UPLOAD_CHUNK_SIZE)
        if not chunk:
            return
        yield chunk


def _parse_jump_field(raw: str) -> JumpCreate:
    """Parse the multipart ``jump`` text field into a ``JumpCreate`` (D30).

    Two failure modes get mapped to ``ValidationFailedError`` so the
    response body is RFC 9457 problem+json (same shape as every other
    422 the service layer produces, D16):

      * malformed JSON — pointer ``#/jump`` with the parser message;
      * JSON that parses but doesn't match the ``JumpCreate`` schema —
        per-field pointers under ``#/jump/<path>``.

    FastAPI's built-in body validation would produce a different 422
    shape (its own envelope) — routing through ``ValidationFailedError``
    gives D30's multipart POST the same error contract as every other
    service-layer 422, so clients only have to know one shape.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationFailedError(
            "jump field is not valid JSON",
            errors=[FieldError(pointer="#/jump", detail=str(exc))],
        ) from exc

    try:
        return JumpCreate.model_validate(data)
    except ValidationError as exc:
        field_errors = [
            FieldError(
                # RFC 6901: prefix with ``#/jump`` because the JSON lives
                # inside the multipart ``jump`` field, not at the root
                # of the request body. A pointer of ``#/exit_altitude_m``
                # would falsely suggest the client sent a JSON body.
                pointer=field_pointer("jump", *err["loc"]),
                detail=err["msg"],
            )
            for err in exc.errors()
        ]
        raise ValidationFailedError(
            "invalid jump field", errors=field_errors
        ) from exc


@router.post(
    "",
    response_model=Jump,
    status_code=status.HTTP_201_CREATED,
    summary="Create a jump",
    description=(
        "Persist a new jump, optionally with attachments. Request body "
        "is ``multipart/form-data`` per D30 with one ``jump`` field "
        "(JSON matching ``JumpCreate``) and zero or more ``files`` "
        "parts. The server mints the ``id`` (UUIDv4), derives the "
        "on-disk folder name from ``[<jump_number>] <title>`` per D4, "
        "streams each attachment to disk while computing its SHA-256, "
        "and returns the canonical ``Jump`` including the server-"
        "assigned id and the attachments with their computed hashes. "
        "A duplicate ``jump_number`` for the same user returns 409 "
        "with ``code=jump_number_conflict``."
    ),
)
def create_jump_route(
    response: Response,
    jump: str = Form(
        ...,
        description=(
            "JSON body matching the JumpCreate schema. Shipped as a "
            "form field rather than a JSON request body because the "
            "outer request is multipart/form-data (D30)."
        ),
    ),
    files: list[UploadFile] | None = File(
        default=None,
        description=(
            "Zero or more attachment files. Each part's filename "
            "becomes the on-disk filename (after NFC + D4 "
            "sanitization); each part's Content-Type becomes the "
            "attachment content_type. SHA-256 is computed by the "
            "server during the streaming write (D30)."
        ),
    ),
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jump:
    payload = _parse_jump_field(jump)
    uploads = [
        Upload(
            # Empty filename would fail ``sanitize_filename`` downstream
            # with a clean field-pointer error — keep the mapping
            # identity-preserving here rather than inventing a default.
            filename=f.filename or "",
            content_type=f.content_type,
            chunks=_upload_chunks(f),
        )
        for f in (files or [])
    ]
    created = jump_service.create_jump(logbook_root, user_id, payload, uploads=uploads)
    # REST convention: 201 Created returns a Location header pointing
    # at the new resource. Clients can GET Location to fetch the full
    # Jump — useful for SDKs that follow the pointer.
    response.headers["Location"] = f"/api/v1/jumps/{created.id}"
    return created


@router.get(
    "",
    response_model=list[JumpSummary],
    summary="List jumps",
    description=(
        "Return jumps in reverse-chronological order (``date DESC, "
        "jump_number DESC``). Results come from the SQLite index — no "
        "per-row XML parse — so list views stay fast even on large "
        "logbooks. Use ``limit`` + ``offset`` for pagination."
    ),
)
def list_jumps_route(
    # ``le=10000`` mirrors the "give me every jump for stats" pattern
    # callers like MyRig.jsx and Dropzones.jsx already use to compute
    # per-rig / per-DZ jump counts. A 10000-row payload is a few MB
    # at worst — fine for a single-user desktop app. The sibling
    # list endpoints (rigs, dropzones, mains, …) stay at le=1000
    # because those entity sets stay small (1–10 typical, 100s
    # extreme).
    limit: int = Query(100, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> list[JumpSummary]:
    return jump_service.list_jumps(
        logbook_root, user_id, limit=limit, offset=offset
    )


@router.get(
    "/{jump_id}",
    response_model=Jump,
    summary="Read a jump by id",
    description=(
        "Fetch the full jump including every optional field that was "
        "set at create time. The XML is parsed through the hardened "
        "parser and XSD-validated (D2); a stale ``SHA256SUMS`` is "
        "silently healed by ``folder_reconcile`` (D25) before the body "
        "is returned."
    ),
)
def get_jump_route(
    jump_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jump:
    return jump_service.get_jump(logbook_root, user_id, jump_id)


@router.put(
    "/{jump_id}",
    response_model=Jump,
    summary="Update a jump (metadata only, D31)",
    description=(
        "Replace the metadata of an existing jump. Request body is "
        "JSON matching ``JumpUpdate`` — every metadata field must be "
        "supplied (PUT is full replace, not merge-patch). Attachments "
        "and the server-minted ``id`` are preserved from the on-disk "
        "jump; attachment editing is deferred per D31.\n\n"
        "Changing ``jump_number`` or ``title`` triggers a folder "
        "rename (D4); the URL stays stable because it keys on ``id``. "
        "A new ``jump_number`` already in use for this user returns "
        "409 with ``code=jump_number_conflict``."
    ),
)
def update_jump_route(
    jump_id: UUID,
    payload: JumpUpdate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jump:
    return jump_service.update_jump(logbook_root, user_id, jump_id, payload)


@router.get(
    "/{jump_id}/files",
    response_model=list[FolderFileResponse],
    summary="List every user-facing file in a jump folder",
    description=(
        "Combines the canonical ``<attachments>`` list from "
        "``jump.xml`` with a fresh filesystem scan, so the response "
        "includes both tracked attachments and any extra files the "
        "user has dropped in via Finder / Explorer since the jump was "
        "logged. ``tracked = false`` files have no ``sha256`` or "
        "``content_type`` because the canonical record never observed "
        "them; ingesting them into the manifest is deferred to the "
        "attachment-edit phase (D31)."
    ),
)
def list_jump_files_route(
    jump_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> list[FolderFileResponse]:
    files = jump_service.list_jump_files(logbook_root, user_id, jump_id)
    return [FolderFileResponse(**f.__dict__) for f in files]


@router.post(
    "/{jump_id}/attachments",
    response_model=Jump,
    summary="Add new attachments to an existing jump (D42)",
    description=(
        "Multipart POST that appends uploaded files to an existing "
        "jump's ``<attachments>`` element and the folder's "
        "``SHA256SUMS``. Same shape as create_jump's ``files`` parts. "
        "Add-only: filenames that already exist on disk or in the "
        "canonical attachments are rejected with 422 — use the D41 "
        "track endpoint to ingest a drop-in, or rename and retry."
    ),
)
def add_attachments_route(
    jump_id: UUID,
    files: list[UploadFile] = File(  # noqa: B008
        ...,
        description="One or more files to attach. Filenames go through D4 sanitization.",
    ),
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jump:
    uploads = [
        Upload(
            filename=f.filename or "",
            content_type=f.content_type,
            chunks=_upload_chunks(f),
        )
        for f in (files or [])
    ]
    return jump_service.add_attachments(logbook_root, user_id, jump_id, uploads)


@router.post(
    "/{jump_id}/attachments/track",
    response_model=Jump,
    summary="Adopt files already in the folder into the manifest (D41)",
    description=(
        "Take filenames already present in the jump folder and ingest "
        "them into ``jump.xml``'s ``<attachments>`` + regenerate "
        "``SHA256SUMS``. The server reads each file from disk to "
        "compute the SHA-256, infers a content type from the file "
        "extension, and appends an attachment entry without touching "
        "any existing entries.\n\n"
        "Idempotent — re-tracking a filename that is already tracked "
        "is a no-op (no rewrite). Distinct from D31's deferred PUT-"
        "based attachment editing: this endpoint only adopts existing "
        "on-disk files and never receives uploaded bytes."
    ),
)
def track_files_route(
    jump_id: UUID,
    payload: TrackFilesRequest,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jump:
    return jump_service.track_files(
        logbook_root, user_id, jump_id, payload.filenames
    )


@router.delete(
    "/{jump_id}/attachments/{filename}",
    response_model=Jump,
    summary="Remove a single attachment from a jump (D43)",
    description=(
        "Hard-deletes one tracked attachment. Updates ``jump.xml`` "
        "and ``SHA256SUMS`` first, then unlinks the file from disk — "
        "a crash between the manifest update and the unlink leaves "
        "the file as an untracked drop-in (D41/D37 already handle "
        "that state). Untracked files are out of scope; remove those "
        "via the OS file manager."
    ),
)
def delete_attachment_route(
    jump_id: UUID,
    filename: str,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jump:
    return jump_service.delete_attachment(
        logbook_root, user_id, jump_id, filename
    )


@router.delete(
    "/{jump_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Soft-delete a jump (D19)",
    description=(
        "Move the jump folder to ``.trash/<timestamp>_<name>/`` and "
        "remove the index row. Subsequent ``GET`` returns 404 and "
        "``list`` no longer shows the jump. ``verify`` still walks "
        "trashed folders. The user can restore by moving the folder "
        "back to ``jumps/`` and running reindex."
    ),
)
def delete_jump_route(
    jump_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Response:
    jump_service.delete_jump(logbook_root, user_id, jump_id)
    # 204 No Content — no response body. Fast return keeps clients
    # that don't parse a body (common for DELETE) simple.
    return Response(status_code=status.HTTP_204_NO_CONTENT)
