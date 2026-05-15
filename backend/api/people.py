"""REST endpoints for people (D54, Phase 2c).

Thin by design (D7): each handler is a translation layer — pull
``logbook_root`` and ``user_id`` from dependencies, call
``people_service``, let ``ServiceError`` subclasses bubble up to the
``on_service_error`` handler in ``rest.py`` which emits RFC 9457
problem+json (D16).

Routes:

  * ``POST   /api/v1/people``              → ``create_person``.
    JSON body matching ``PersonCreate``. 201 + ``Location`` header.
  * ``GET    /api/v1/people``              → ``list_people``,
    paginated. Returns ``PersonSummary`` (compact projection — full
    record is one extra GET away).
  * ``GET    /api/v1/people/{person_id}``  → ``get_person``.
  * ``PUT    /api/v1/people/{person_id}``  → ``update_person``.
    JSON body matching ``PersonUpdate`` — full replace, every field
    must be supplied.
  * ``DELETE /api/v1/people/{person_id}``  → ``delete_person``.
    204 on success; soft-delete to ``.trash/people/`` per D19+D54.

Same posture as the dropzone surface (D44): no multipart, no
attachments. People are flat single-file records under
``logbook_root/people/<uuid>.xml``.
"""
from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from ..models.person import Person, PersonCreate, PersonSummary, PersonUpdate
from ..services import people_service
from .deps import get_logbook_root, get_user_id
from .openapi import ERR_CREATE, ERR_DELETE, ERR_LIST, ERR_READ, ERR_UPDATE

router = APIRouter(prefix="/api/v1/people", tags=["people"])


@router.post(
    "",
    response_model=Person,
    operation_id="create_person",
    responses=ERR_CREATE,
    status_code=status.HTTP_201_CREATED,
    summary="Create a person",
    description=(
        "Persist a new person record. Body is JSON matching "
        "``PersonCreate``. The server mints the ``id`` (UUIDv4) and "
        "stamps ``created_at`` / ``updated_at``, then writes "
        "``people/<uuid>.xml`` atomically (D10) after XSD validation "
        "(D2). The name is NFC-normalized at the storage layer (D4) "
        "so equivalent Unicode forms land on equal bytes. Returns "
        "the canonical ``Person`` including the server-assigned id "
        "and timestamps. The ``Location`` header points at the new "
        "resource."
    ),
)
def create_person_route(
    response: Response,
    payload: PersonCreate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Person:
    created = people_service.create_person(logbook_root, user_id, payload)
    response.headers["Location"] = f"/api/v1/people/{created.id}"
    return created


@router.get(
    "",
    response_model=list[PersonSummary],
    operation_id="list_persons",
    responses=ERR_LIST,
    summary="List people",
    description=(
        "Return people as compact summaries, ordered alphabetically "
        "by name (case-insensitive). Reads from the SQLite ``people`` "
        "index for O(rows) at SQLite speed — no per-row XML parse, "
        "no filesystem walk. Use ``limit`` + ``offset`` for "
        "pagination."
    ),
)
def list_people_route(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> list[PersonSummary]:
    return people_service.list_people(
        logbook_root, user_id, limit=limit, offset=offset
    )


@router.get(
    "/{person_id}",
    response_model=Person,
    operation_id="get_person",
    responses=ERR_READ,
    summary="Read a person by id",
    description=(
        "Fetch the full person including every optional field. The "
        "XML is parsed through the hardened parser and XSD-validated "
        "(D2). Returns 404 with ``code=not_found`` when the id has "
        "no record (or has been soft-deleted to ``.trash/people/``)."
    ),
)
def get_person_route(
    person_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Person:
    return people_service.get_person(logbook_root, user_id, person_id)


@router.put(
    "/{person_id}",
    response_model=Person,
    operation_id="update_person",
    responses=ERR_UPDATE,
    summary="Update a person",
    description=(
        "Full replace. Body is JSON matching ``PersonUpdate`` — "
        "every editable field must be supplied. ``id`` and "
        "``created_at`` are preserved server-side; ``updated_at`` "
        "is bumped. The name is NFC-normalized on every write. A "
        "404 means the person doesn't exist (or has already been "
        "trashed); a 422 means the payload failed XSD or Pydantic "
        "validation."
    ),
)
def update_person_route(
    person_id: UUID,
    payload: PersonUpdate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Person:
    return people_service.update_person(
        logbook_root, user_id, person_id, payload
    )


@router.delete(
    "/{person_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="delete_person",
    responses=ERR_DELETE,
    response_class=Response,
    summary="Soft-delete a person (D19, D54)",
    description=(
        "Move ``people/<uuid>.xml`` to "
        "``.trash/people/<timestamp>_<uuid>.xml``. Subsequent "
        "``GET`` returns 404 and ``list`` no longer shows the "
        "person. **No cascade**: jumps that reference the trashed "
        "id via ``<packed_by>`` or ``<group_members>`` keep their "
        "UUID; the soft-resolution rule (D54) renders them as "
        "``Unknown person <short-uuid>`` until the user edits the "
        "jump or recreates the Person. The user can restore by "
        "moving the file back to ``people/`` and running reindex."
    ),
)
def delete_person_route(
    person_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Response:
    people_service.delete_person(logbook_root, user_id, person_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
