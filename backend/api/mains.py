"""REST endpoints for mains (R.1d, D33+D34).

Mirrors ``backend/api/containers.py`` with main-specific shape:
``size_sqft`` (canopy area), ``default_environment`` (D45 wear-math
fallback), and the nested ``current_lineset`` + ``lineset_history``
state (D34).

Routes:

  * ``POST   /api/v1/mains``                → ``create_main``.
  * ``GET    /api/v1/mains``                → ``list_mains``.
  * ``GET    /api/v1/mains/{main_id}``      → ``get_main``.
  * ``PUT    /api/v1/mains/{main_id}``      → ``update_main``.
  * ``DELETE /api/v1/mains/{main_id}``      → ``delete_main``.

The dedicated reline workflow (move current_lineset into
lineset_history atomically, install a new current_lineset) lands as
a separate phase; this PUT accepts any shape that validates so a
client can drive a reline manually by sending the desired final
state.
"""
from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from ..models.main import Main, MainCreate, MainUpdate
from ..services import main_service
from .deps import get_logbook_root, get_user_id

router = APIRouter(prefix="/api/v1/mains", tags=["mains"])


@router.post(
    "",
    response_model=Main,
    status_code=status.HTTP_201_CREATED,
    summary="Create a main canopy",
    description=(
        "Persist a new main record. Body is JSON matching ``MainCreate``. "
        "Server mints the ``id`` and stamps timestamps."
    ),
)
def create_main_route(
    response: Response,
    payload: MainCreate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Main:
    created = main_service.create_main(logbook_root, user_id, payload)
    response.headers["Location"] = f"/api/v1/mains/{created.id}"
    return created


@router.get(
    "",
    response_model=list[Main],
    summary="List main canopies",
    description=(
        "Return every main newest first by ``created_at``. Walks "
        "``inventory/mains/`` and parses each XML."
    ),
)
def list_mains_route(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> list[Main]:
    return main_service.list_mains(
        logbook_root, user_id, limit=limit, offset=offset
    )


@router.get(
    "/{main_id}",
    response_model=Main,
    summary="Read a main canopy by id",
    description=(
        "Fetch the full main including current_lineset and "
        "lineset_history. 404 with ``code=not_found`` when the id "
        "has no record."
    ),
)
def get_main_route(
    main_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Main:
    return main_service.get_main(logbook_root, user_id, main_id)


@router.put(
    "/{main_id}",
    response_model=Main,
    summary="Update a main canopy",
    description=(
        "Full replace. Lineset state is part of the payload; clients "
        "that want to preserve the existing current_lineset UUID "
        "must echo it in the request. ``id`` and ``created_at`` "
        "preserved server-side; ``updated_at`` bumped."
    ),
)
def update_main_route(
    main_id: UUID,
    payload: MainUpdate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Main:
    return main_service.update_main(logbook_root, user_id, main_id, payload)


@router.delete(
    "/{main_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Soft-delete a main canopy (D19)",
    description=(
        "Move ``inventory/mains/<uuid>.xml`` to "
        "``.trash/inventory/mains/<timestamp>_<uuid>.xml/<uuid>.xml``. "
        "**No cascade** to rigs that referenced the main by id."
    ),
)
def delete_main_route(
    main_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Response:
    main_service.delete_main(logbook_root, user_id, main_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
