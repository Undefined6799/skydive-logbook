"""REST endpoints for reserves (R.1c, D33+D34).

Mirrors ``backend/api/containers.py``. Reserves carry a structured
``recert_extensions`` log; clients append to it by sending the
existing list plus a new entry on PUT.

Routes:

  * ``POST   /api/v1/reserves``                  → ``create_reserve``.
  * ``GET    /api/v1/reserves``                  → ``list_reserves``.
  * ``GET    /api/v1/reserves/{reserve_id}``     → ``get_reserve``.
  * ``PUT    /api/v1/reserves/{reserve_id}``     → ``update_reserve``.
  * ``DELETE /api/v1/reserves/{reserve_id}``     → ``delete_reserve``.

The regulatory repack window (180 / 270 day calendar) lives on the
rig (D33), not on the reserve. Reserve fields here are manufacturer-
specific only (repack_limit / ride_limit) plus the recert log.
"""
from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from ..models.reserve import Reserve, ReserveCreate, ReserveUpdate
from ..services import reserve_service
from .deps import get_logbook_root, get_user_id

router = APIRouter(prefix="/api/v1/reserves", tags=["reserves"])


@router.post(
    "",
    response_model=Reserve,
    status_code=status.HTTP_201_CREATED,
    summary="Create a reserve",
    description=(
        "Persist a new reserve record. Body is JSON matching "
        "``ReserveCreate``. Server mints the ``id`` and stamps "
        "timestamps."
    ),
)
def create_reserve_route(
    response: Response,
    payload: ReserveCreate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Reserve:
    created = reserve_service.create_reserve(logbook_root, user_id, payload)
    response.headers["Location"] = f"/api/v1/reserves/{created.id}"
    return created


@router.get(
    "",
    response_model=list[Reserve],
    summary="List reserves",
    description=(
        "Return every reserve newest first by ``created_at``. Walks "
        "``inventory/reserves/`` and parses each XML."
    ),
)
def list_reserves_route(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> list[Reserve]:
    return reserve_service.list_reserves(
        logbook_root, user_id, limit=limit, offset=offset
    )


@router.get(
    "/{reserve_id}",
    response_model=Reserve,
    summary="Read a reserve by id",
    description=(
        "Fetch the full reserve including the recert_extensions log. "
        "404 with ``code=not_found`` when the id has no record."
    ),
)
def get_reserve_route(
    reserve_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Reserve:
    return reserve_service.get_reserve(logbook_root, user_id, reserve_id)


@router.put(
    "/{reserve_id}",
    response_model=Reserve,
    summary="Update a reserve",
    description=(
        "Full replace. Clients append a recert extension by sending "
        "the existing log plus a new entry. ``id`` and ``created_at`` "
        "preserved server-side; ``updated_at`` bumped."
    ),
)
def update_reserve_route(
    reserve_id: UUID,
    payload: ReserveUpdate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Reserve:
    return reserve_service.update_reserve(
        logbook_root, user_id, reserve_id, payload
    )


@router.delete(
    "/{reserve_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Soft-delete a reserve (D19)",
    description=(
        "Move ``inventory/reserves/<uuid>.xml`` to "
        "``.trash/inventory/reserves/<timestamp>_<uuid>.xml/<uuid>.xml``. "
        "**No cascade** to rigs that referenced the reserve by id."
    ),
)
def delete_reserve_route(
    reserve_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Response:
    reserve_service.delete_reserve(logbook_root, user_id, reserve_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
