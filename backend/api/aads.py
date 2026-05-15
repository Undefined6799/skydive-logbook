"""REST endpoints for AADs (R.1b, D33+D34).

Mirrors ``backend/api/containers.py``. AADs use ``manufacturer`` for
the maker (D34 amended 2026-04-28 — symmetric with main / reserve /
container, dropping the original ``brand`` wording).

Routes:

  * ``POST   /api/v1/aads``          → ``create_aad``. 201 + Location.
  * ``GET    /api/v1/aads``          → ``list_aads``, paginated.
  * ``GET    /api/v1/aads/{aad_id}`` → ``get_aad``.
  * ``PUT    /api/v1/aads/{aad_id}`` → ``update_aad``. Full replace.
  * ``DELETE /api/v1/aads/{aad_id}`` → ``delete_aad``. 204; soft-delete.

D39's airworthiness rules (service windows, EOL, retirement flags)
are NOT stored on the AAD — they're derived in code from
``manufacturer`` + ``model`` + ``date_of_manufacture`` at read time.
A future status endpoint surfaces those derivations.
"""
from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from ..models.aad import AAD, AADCreate, AADUpdate
from ..services import aad_service
from .deps import get_logbook_root, get_user_id
from .openapi import ERR_CREATE, ERR_DELETE, ERR_LIST, ERR_READ, ERR_UPDATE

router = APIRouter(prefix="/api/v1/aads", tags=["aads"])


@router.post(
    "",
    response_model=AAD,
    operation_id="create_aad",
    responses=ERR_CREATE,
    status_code=status.HTTP_201_CREATED,
    summary="Create an AAD",
    description=(
        "Persist a new AAD record. Body is JSON matching ``AADCreate``. "
        "Server mints the ``id`` (UUIDv4) and stamps timestamps, then "
        "writes ``inventory/aads/<uuid>.xml`` atomically (D10) after "
        "XSD validation (D2)."
    ),
)
def create_aad_route(
    response: Response,
    payload: AADCreate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> AAD:
    created = aad_service.create_aad(logbook_root, user_id, payload)
    response.headers["Location"] = f"/api/v1/aads/{created.id}"
    return created


@router.get(
    "",
    response_model=list[AAD],
    operation_id="list_aads",
    responses=ERR_LIST,
    summary="List AADs",
    description=(
        "Return every AAD newest first by ``created_at``. Walks "
        "``inventory/aads/`` and parses each XML; SQLite indexing "
        "lands later alongside D35's wear projections."
    ),
)
def list_aads_route(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> list[AAD]:
    return aad_service.list_aads(
        logbook_root, user_id, limit=limit, offset=offset
    )


@router.get(
    "/{aad_id}",
    response_model=AAD,
    operation_id="get_aad",
    responses=ERR_READ,
    summary="Read an AAD by id",
    description=(
        "Fetch the full AAD. XSD-validated through the hardened "
        "parser (D2). 404 with ``code=not_found`` when the id has "
        "no record."
    ),
)
def get_aad_route(
    aad_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> AAD:
    return aad_service.get_aad(logbook_root, user_id, aad_id)


@router.put(
    "/{aad_id}",
    response_model=AAD,
    operation_id="update_aad",
    responses=ERR_UPDATE,
    summary="Update an AAD",
    description=(
        "Full replace. ``id`` and ``created_at`` preserved server-"
        "side; ``updated_at`` bumped. ``assigned_rig_id`` is a "
        "passthrough UUID in v0.1 — cross-entity validation waits "
        "for R.2."
    ),
)
def update_aad_route(
    aad_id: UUID,
    payload: AADUpdate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> AAD:
    return aad_service.update_aad(logbook_root, user_id, aad_id, payload)


@router.delete(
    "/{aad_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="delete_aad",
    responses=ERR_DELETE,
    response_class=Response,
    summary="Soft-delete an AAD (D19)",
    description=(
        "Move ``inventory/aads/<uuid>.xml`` to "
        "``.trash/inventory/aads/<timestamp>_<uuid>.xml/<uuid>.xml``. "
        "**No cascade** to rigs that referenced the AAD by id."
    ),
)
def delete_aad_route(
    aad_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Response:
    aad_service.delete_aad(logbook_root, user_id, aad_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
