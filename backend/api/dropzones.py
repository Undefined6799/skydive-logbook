"""REST endpoints for dropzones (R.D.2, D44).

Thin by design (D7): each handler is a translation layer — pull
``logbook_root`` and ``user_id`` from dependencies, call
``dropzone_service``, let ``ServiceError`` subclasses bubble up to the
``on_service_error`` handler in ``rest.py`` which emits RFC 9457
problem+json (D16).

Routes:

  * ``POST   /api/v1/dropzones``                → ``create_dropzone``.
    JSON body matching ``DropzoneCreate``. 201 + ``Location`` header.
  * ``GET    /api/v1/dropzones``                → ``list_dropzones``,
    paginated. Returns ``DropzoneSummary`` (compact projection — full
    record is one extra GET away).
  * ``GET    /api/v1/dropzones/{dropzone_id}``  → ``get_dropzone``.
  * ``PUT    /api/v1/dropzones/{dropzone_id}``  → ``update_dropzone``.
    JSON body matching ``DropzoneUpdate`` — full replace, every field
    must be supplied.
  * ``DELETE /api/v1/dropzones/{dropzone_id}``  → ``delete_dropzone``.
    204 on success; soft-delete to ``.trash/dropzones/`` per D19+D44.

Unlike jumps, dropzones do not use ``multipart/form-data`` — there
are no attachments in v0.1 (D44 §Storage). A future "DZ logo" or
"DZ map photo" feature would migrate POST to multipart, mirroring
the jumps pattern.
"""
from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from ..models.dropzone import Dropzone, DropzoneCreate, DropzoneSummary, DropzoneUpdate
from ..services import dropzone_service
from .deps import get_logbook_root, get_user_id
from .openapi import ERR_CREATE, ERR_DELETE, ERR_LIST, ERR_READ, ERR_UPDATE

router = APIRouter(prefix="/api/v1/dropzones", tags=["dropzones"])


@router.post(
    "",
    response_model=Dropzone,
    operation_id="create_dropzone",
    responses=ERR_CREATE,
    status_code=status.HTTP_201_CREATED,
    summary="Create a dropzone",
    description=(
        "Persist a new dropzone record. Body is JSON matching "
        "``DropzoneCreate``. The server mints the ``id`` (UUIDv4) and "
        "stamps ``created_at`` / ``updated_at``, then writes "
        "``dropzones/<uuid>.xml`` atomically (D10) after XSD "
        "validation (D2). Returns the canonical ``Dropzone`` "
        "including the server-assigned id and timestamps. The "
        "``Location`` header points at the new resource."
    ),
)
def create_dropzone_route(
    response: Response,
    payload: DropzoneCreate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Dropzone:
    created = dropzone_service.create_dropzone(logbook_root, user_id, payload)
    response.headers["Location"] = f"/api/v1/dropzones/{created.id}"
    return created


@router.get(
    "",
    response_model=list[DropzoneSummary],
    operation_id="list_dropzones",
    responses=ERR_LIST,
    summary="List dropzones",
    description=(
        "Return dropzones as compact summaries, ordered alphabetically "
        "by name (case-insensitive) then by city. R.D.1 walks the "
        "``dropzones/`` directory; R.D.3 will swap in the SQLite "
        "``dropzones`` table for O(rows) at SQLite speed without "
        "changing this contract. Use ``limit`` + ``offset`` for "
        "pagination."
    ),
)
def list_dropzones_route(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> list[DropzoneSummary]:
    return dropzone_service.list_dropzones(
        logbook_root, user_id, limit=limit, offset=offset
    )


@router.get(
    "/{dropzone_id}",
    response_model=Dropzone,
    operation_id="get_dropzone",
    responses=ERR_READ,
    summary="Read a dropzone by id",
    description=(
        "Fetch the full dropzone including every optional field. "
        "The XML is parsed through the hardened parser and "
        "XSD-validated (D2). Returns 404 with "
        "``code=not_found`` when the id has no record (or has been "
        "soft-deleted to ``.trash/dropzones/``)."
    ),
)
def get_dropzone_route(
    dropzone_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Dropzone:
    return dropzone_service.get_dropzone(logbook_root, user_id, dropzone_id)


@router.put(
    "/{dropzone_id}",
    response_model=Dropzone,
    operation_id="update_dropzone",
    responses=ERR_UPDATE,
    summary="Update a dropzone",
    description=(
        "Full replace. Body is JSON matching ``DropzoneUpdate`` — "
        "every editable field must be supplied. ``id`` and "
        "``created_at`` are preserved server-side; ``updated_at`` is "
        "bumped. A 404 means the dropzone doesn't exist (or has "
        "already been trashed); a 422 means the payload failed XSD "
        "or Pydantic validation."
    ),
)
def update_dropzone_route(
    dropzone_id: UUID,
    payload: DropzoneUpdate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Dropzone:
    return dropzone_service.update_dropzone(
        logbook_root, user_id, dropzone_id, payload
    )


@router.put(
    "/{dropzone_id}/star",
    response_model=Dropzone,
    operation_id="star_dropzone",
    responses=ERR_UPDATE,
    summary="Star a dropzone as the logbook default (D60)",
    description=(
        "Set the target dropzone as the single starred default for "
        "the jump-log form. Idempotent — starring the already-starred "
        "DZ is a no-op write. The previously starred DZ (if any) is "
        "atomically unstarred under the same writer lock so observers "
        "never see a transient \"two starred\" state. There is no "
        "DELETE counterpart (D60 forbids explicit unstar): the star "
        "moves only by starring a different DZ or by deleting the "
        "currently starred one. Returns 200 with the updated "
        "``Dropzone``; 404 RFC 9457 problem when the target is "
        "missing or has been soft-deleted."
    ),
)
def star_dropzone_route(
    dropzone_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Dropzone:
    return dropzone_service.set_star(logbook_root, user_id, dropzone_id)


@router.delete(
    "/{dropzone_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="delete_dropzone",
    responses=ERR_DELETE,
    response_class=Response,
    summary="Soft-delete a dropzone (D19, D44)",
    description=(
        "Move ``dropzones/<uuid>.xml`` to "
        "``.trash/dropzones/<timestamp>_<uuid>.xml/<uuid>.xml``. "
        "Subsequent ``GET`` returns 404 and ``list`` no longer shows "
        "the dropzone. **No cascade**: jumps that reference the "
        "trashed ``<dropzone_id>`` keep their reference; the wear "
        "math (D45) falls back to the main's default flags on next "
        "reindex. The user can restore by moving the file back to "
        "``dropzones/`` and (when R.D.3 lands) running reindex."
    ),
)
def delete_dropzone_route(
    dropzone_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Response:
    dropzone_service.delete_dropzone(logbook_root, user_id, dropzone_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
