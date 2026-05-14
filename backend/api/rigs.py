"""REST endpoints for rigs (R.2.0c.iv, D33, D37, D38).

Thin by design (D7): each handler translates HTTP to a service call,
lets ``ServiceError`` subclasses bubble up to the ``on_service_error``
handler in ``rest.py`` which emits RFC 9457 problem+json (D16).

Routes:

  * ``POST   /api/v1/rigs``               → ``create_rig``.
    JSON body matching ``RigCreate``. 201 + ``Location`` header.
    Validates the four ``current_*_id`` refs per D37 (component
    exists + active + unassigned); on success, sets each
    component's ``assigned_rig_id`` to the new rig.
  * ``GET    /api/v1/rigs``               → ``list_rigs``,
    paginated. Returns full ``Rig`` shape (including the four
    current refs and ``repack_history``).
  * ``GET    /api/v1/rigs/{rig_id}``      → ``get_rig``.
  * ``PUT    /api/v1/rigs/{rig_id}``      → ``update_rig``.
    JSON body matching ``RigUpdate`` — full replace, but D37
    forbids changes to ``current_*_id`` (use the swap path / R.5
    repack flow). Folder is renamed atomically when the nickname
    changes.
  * ``DELETE /api/v1/rigs/{rig_id}``      → ``delete_rig``.
    204 on success; soft-delete the rig folder to
    ``.trash/rigs/`` (D19) and clears ``assigned_rig_id`` on each
    of the four assigned components per D37.

Rigs do not use ``multipart/form-data`` in v0.1 — the
folder-with-manifest layout is intentionally chosen so future
attachments (seal photos, rigger documents) can land additively
without a transport change (D33).
"""
from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, ConfigDict

from ..models.rig import Rig, RigCreate, RigUpdate
from ..services import rig_service
from .deps import get_logbook_root, get_user_id


class SwapMainRequest(BaseModel):
    """Body for ``POST /api/v1/rigs/{rig_id}/swap_main``.

    A single field — the new main's id. Pydantic ``extra="forbid"``
    so a typo (e.g. ``new_main`` instead of ``new_main_id``) fails
    fast at 422 rather than silently no-op'ing as a ``None`` ref.
    """

    model_config = ConfigDict(extra="forbid")

    new_main_id: UUID


class ReorderRigsRequest(BaseModel):
    """Body for ``POST /api/v1/rigs/reorder`` (D59).

    A single field — the list of rig ids in the user's desired
    left-to-right order. ``rig_ids[0]`` becomes the leftmost rig.
    The list MUST be exactly the set of non-trashed rig ids; the
    service rejects mismatches with a 422 and a precise
    ``FieldError`` pointer.
    """

    model_config = ConfigDict(extra="forbid")

    rig_ids: list[UUID]

router = APIRouter(prefix="/api/v1/rigs", tags=["rigs"])


@router.post(
    "",
    response_model=Rig,
    status_code=status.HTTP_201_CREATED,
    summary="Create a rig",
    description=(
        "Persist a new rig. Body is JSON matching ``RigCreate`` — "
        "must include the four ``current_*_id`` refs (D37). The "
        "server mints the ``id``, stamps ``created_at`` / "
        "``updated_at``, validates each component ref (exists + "
        "active + unassigned), then writes "
        "``rigs/<sanitized-nickname>/{rig.xml,SHA256SUMS}`` "
        "atomically (D10) after XSD validation (D2), and finally "
        "sets ``assigned_rig_id`` on each of the four components. "
        "Returns the canonical ``Rig``.\n\n"
        "Errors: 422 on a bad nickname or on a component ref that "
        "doesn't exist / isn't active. 409 with "
        "``code=rig_nickname_conflict`` on a duplicate nickname. "
        "409 with ``code=component_already_assigned`` (and the "
        "existing rig id in the error details) when a referenced "
        "component is already on another rig."
    ),
)
def create_rig_route(
    response: Response,
    payload: RigCreate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Rig:
    created = rig_service.create_rig(logbook_root, user_id, payload)
    response.headers["Location"] = f"/api/v1/rigs/{created.id}"
    return created


@router.get(
    "",
    response_model=list[Rig],
    summary="List rigs",
    description=(
        "Return every rig under ``rigs/``, newest first by "
        "``created_at``. v0.1 walks the directory and parses each "
        "``rig.xml`` (D2 hardened parser + XSD per file); a future "
        "phase will swap in a SQLite-indexed lookup without "
        "changing this contract. Use ``limit`` + ``offset`` for "
        "pagination.\n\n"
        "Folders that fail XSD validation, or that lack a "
        "``rig.xml`` (partial-create stubs from a crash), are "
        "logged at WARNING and skipped — the list endpoint stays "
        "useful even if one folder is corrupt; an operator runs "
        "``verify`` to diagnose."
    ),
)
def list_rigs_route(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> list[Rig]:
    return rig_service.list_rigs(
        logbook_root, user_id, limit=limit, offset=offset
    )


@router.get(
    "/{rig_id}",
    response_model=Rig,
    summary="Read a rig by id",
    description=(
        "Fetch the full rig including ``repack_history`` and "
        "``notes_log``. Resolves the on-disk folder by walking "
        "``rigs/`` and matching ``<id>``. Returns 404 with "
        "``code=not_found`` when the id has no record (or has "
        "been soft-deleted to ``.trash/rigs/``)."
    ),
)
def get_rig_route(
    rig_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Rig:
    return rig_service.get_rig(logbook_root, user_id, rig_id)


@router.put(
    "/{rig_id}",
    response_model=Rig,
    summary="Update a rig",
    description=(
        "Full replace. Body is JSON matching ``RigUpdate``. Per "
        "D37 the four ``current_*_id`` refs cannot be changed via "
        "this PUT — main swaps go through the dedicated "
        "``swap_main`` operation (a future jumper-facing slice); "
        "reserve / AAD / container changes happen only at a "
        "repack event (R.5). Attempting to swap any ref returns "
        "409 with ``code=rig_component_swap_unsupported`` and a "
        "``FieldError`` pointer per offending ref.\n\n"
        "Editable fields: ``nickname``, ``jurisdiction``, "
        "``notes_log``. ``id`` and ``created_at`` are preserved; "
        "``updated_at`` is bumped. ``repack_history`` is "
        "preserved from on-disk (D38: that mutation belongs to "
        "the R.5 write flow, not the metadata update).\n\n"
        "Nickname changes atomically rename the folder under "
        "``rigs/``. A rename collision returns 409 with "
        "``code=rig_nickname_conflict``."
    ),
)
def update_rig_route(
    rig_id: UUID,
    payload: RigUpdate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Rig:
    return rig_service.update_rig(
        logbook_root, user_id, rig_id, payload
    )


@router.post(
    "/reorder",
    response_model=list[Rig],
    summary="Reorder rigs (D59)",
    description=(
        "Rewrite the carousel order. Body is JSON ``{ \"rig_ids\": "
        "[UUID, …] }`` — the list of rig ids in the user's desired "
        "left-to-right order. ``rig_ids[0]`` becomes the leftmost "
        "rig (``display_order=0``).\n\n"
        "Validation: the list MUST be exactly the set of non-trashed "
        "rig ids. Missing id, unknown id, or a duplicate id returns "
        "422 ``code=validation_failed`` with a precise "
        "``FieldError`` pointer (``#/rig_ids``). The on-disk state "
        "is untouched on validation failure.\n\n"
        "On success, returns 200 with the reordered ``list[Rig]`` "
        "in the new order. Each rig.xml is rewritten atomically "
        "(D10) under the writer lock (D50); a crash mid-pass leaves "
        "a partially-reordered state that the next reorder call "
        "corrects. List remains coherent in the intermediate state "
        "because ``list_rigs`` is total-ordered.\n\n"
        "This is the only client-controlled mutator for "
        "``display_order``. ``create_rig`` stamps the initial "
        "value (max+1 of existing); there is no per-rig PUT to "
        "set the order directly."
    ),
)
def reorder_rigs_route(
    payload: ReorderRigsRequest,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> list[Rig]:
    return rig_service.reorder_rigs(logbook_root, user_id, payload.rig_ids)


@router.post(
    "/{rig_id}/swap_main",
    response_model=Rig,
    summary="Swap a rig's main canopy (D37, S.2)",
    description=(
        "Replace the rig's ``current_main_id`` with a different "
        "main canopy. The dedicated jumper-facing alternative to "
        "PUT (which forbids ``current_*_id`` changes via "
        "``rig_component_swap_unsupported``).\n\n"
        "Body is JSON ``{ \"new_main_id\": UUID }``. Validation: "
        "the new main exists, has ``status == active``, and is "
        "either unassigned or already assigned to *this* rig "
        "(idempotent retry).\n\n"
        "Side effects in atomic order: rig.xml is rewritten with "
        "the new id, the old main has its ``assigned_rig_id`` "
        "cleared, and the new main has its ``assigned_rig_id`` "
        "set to this rig. A crash mid-swap leaves a state that "
        "reconcile (a future slice) can heal; retrying the same "
        "swap converges to a clean state.\n\n"
        "Errors: 404 rig not found. 422 ``new_main_id`` references "
        "a non-existent or non-active main. 409 "
        "``code=component_already_assigned`` when the main is on a "
        "different rig (the existing rig id is in the error "
        "details). Picking the same id that's already on the rig "
        "is a no-op and returns 200 with the unchanged rig."
    ),
)
def swap_main_route(
    rig_id: UUID,
    payload: SwapMainRequest,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Rig:
    return rig_service.swap_main(
        logbook_root, user_id, rig_id, payload.new_main_id
    )


@router.put(
    "/{rig_id}/star",
    response_model=Rig,
    summary="Star a rig as the default for the jump-log form (D58)",
    description=(
        "Move the logbook's single \"starred\" flag to this rig. "
        "Idempotent: PUT'ing the same id twice returns the same rig "
        "unchanged on the second call. No request body.\n\n"
        "Per D58, the invariant is *exactly one starred rig while "
        "≥1 rig exists*. Starring rig B atomically clears the "
        "star on whichever rig had it before (under the writer "
        "lock per D50). There is no DELETE counterpart — the "
        "star moves only by starring a different rig or by "
        "deleting the currently starred rig (which auto-elects a "
        "successor via D58 transition 3).\n\n"
        "A brand-new logbook's first rig auto-stars at creation "
        "time (D58 transition 1), so the jump-log form's preselect "
        "path is usable from the first rig onward without an "
        "explicit star call.\n\n"
        "Errors: 404 with ``code=not_found`` when the rig id has "
        "no record (or has been soft-deleted to ``.trash/rigs/``)."
    ),
)
def set_star_route(
    rig_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Rig:
    return rig_service.set_star(logbook_root, user_id, rig_id)


@router.delete(
    "/{rig_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Soft-delete a rig (D19, D37 cascade)",
    description=(
        "Move ``rigs/<nickname>/`` to "
        "``.trash/rigs/<timestamp>_<nickname>/``. Per D37 the four "
        "assigned components have their ``assigned_rig_id`` "
        "cleared first — they return to inventory as available "
        "for assignment to another rig. A missing component file "
        "(out-of-band edit) is logged at WARNING and the cascade "
        "continues; the rig delete proceeds either way.\n\n"
        "Subsequent GET returns 404 and list no longer shows the "
        "rig. The user can restore by moving the folder back, "
        "but the assigned_rig_id refs on the four components "
        "stay cleared (a manual assignment via a future "
        "``swap_main`` op or a re-create_rig is required)."
    ),
)
def delete_rig_route(
    rig_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Response:
    rig_service.delete_rig(logbook_root, user_id, rig_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
