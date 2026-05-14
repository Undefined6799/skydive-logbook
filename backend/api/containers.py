"""REST endpoints for containers (R.1a, D33+D34).

Thin by design (D7): each handler translates HTTP to a service call,
lets ``ServiceError`` subclasses bubble up to the ``on_service_error``
handler in ``rest.py`` which emits RFC 9457 problem+json (D16).

Routes:

  * ``POST   /api/v1/containers``                  → ``create_container``.
    JSON body matching ``ContainerCreate``. 201 + ``Location`` header.
  * ``GET    /api/v1/containers``                  → ``list_containers``,
    paginated. Returns the full ``Container`` shape per kind (no
    Summary type in v0.1 — for the small inventory size this stays
    simple, and the picker UI gets every field with no follow-up GET).
  * ``GET    /api/v1/containers/{container_id}``   → ``get_container``.
  * ``PUT    /api/v1/containers/{container_id}``   → ``update_container``.
    JSON body matching ``ContainerUpdate`` — full replace.
  * ``DELETE /api/v1/containers/{container_id}``   → ``delete_container``.
    204 on success; soft-delete to ``.trash/inventory/containers/`` (D19).

Containers do not use ``multipart/form-data`` — components are flat
single-file XMLs in v0.1 (D33 §"Out of scope"). A future "container
photo" feature would migrate POST to multipart, mirroring the jumps
pattern.
"""
from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from ..models.container import Container, ContainerCreate, ContainerUpdate
from ..services import container_service
from .deps import get_logbook_root, get_user_id

router = APIRouter(prefix="/api/v1/containers", tags=["containers"])


@router.post(
    "",
    response_model=Container,
    status_code=status.HTTP_201_CREATED,
    summary="Create a container",
    description=(
        "Persist a new container record. Body is JSON matching "
        "``ContainerCreate``. The server mints the ``id`` (UUIDv4) "
        "and stamps ``created_at`` / ``updated_at``, then writes "
        "``inventory/containers/<uuid>.xml`` atomically (D10) after "
        "XSD validation (D2). Returns the canonical ``Container`` "
        "including the server-assigned id and timestamps."
    ),
)
def create_container_route(
    response: Response,
    payload: ContainerCreate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Container:
    created = container_service.create_container(logbook_root, user_id, payload)
    response.headers["Location"] = f"/api/v1/containers/{created.id}"
    return created


@router.get(
    "",
    response_model=list[Container],
    summary="List containers",
    description=(
        "Return every container under ``inventory/containers/``, "
        "newest first by ``created_at``. v0.1 walks the directory and "
        "parses each XML (D2 hardened parser + XSD per file); a future "
        "phase will swap in a SQLite index alongside D35's per-kind "
        "wear projections without changing this contract. Use "
        "``limit`` + ``offset`` for pagination."
    ),
)
def list_containers_route(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> list[Container]:
    return container_service.list_containers(
        logbook_root, user_id, limit=limit, offset=offset
    )


@router.get(
    "/{container_id}",
    response_model=Container,
    summary="Read a container by id",
    description=(
        "Fetch the full container including every optional field. "
        "The XML is parsed through the hardened parser and "
        "XSD-validated (D2). Returns 404 with ``code=not_found`` when "
        "the id has no record (or has been soft-deleted to "
        "``.trash/inventory/containers/``)."
    ),
)
def get_container_route(
    container_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Container:
    return container_service.get_container(logbook_root, user_id, container_id)


@router.put(
    "/{container_id}",
    response_model=Container,
    summary="Update a container",
    description=(
        "Full replace. Body is JSON matching ``ContainerUpdate`` — "
        "every editable field must be supplied. ``id`` and "
        "``created_at`` are preserved server-side; ``updated_at`` is "
        "bumped. A 404 means the container doesn't exist (or has "
        "already been trashed); a 422 means the payload failed "
        "Pydantic / XSD validation. ``assigned_rig_id`` is a "
        "passthrough UUID in v0.1 — cross-entity validation that the "
        "rig actually exists waits for R.2."
    ),
)
def update_container_route(
    container_id: UUID,
    payload: ContainerUpdate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Container:
    return container_service.update_container(
        logbook_root, user_id, container_id, payload
    )


@router.delete(
    "/{container_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Soft-delete a container (D19)",
    description=(
        "Move ``inventory/containers/<uuid>.xml`` to "
        "``.trash/inventory/containers/<timestamp>_<uuid>.xml/<uuid>.xml``. "
        "Subsequent ``GET`` returns 404 and ``list`` no longer shows "
        "the container. **No cascade**: rigs that referenced this "
        "container by id keep the dangling reference until R.2's rig "
        "service handles stale references. The user can restore by "
        "moving the file back."
    ),
)
def delete_container_route(
    container_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Response:
    container_service.delete_container(logbook_root, user_id, container_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
