"""REST endpoints for the first-run onboarding wizard (D64).

Thin by design (D7): each handler is a translation layer — pull
``logbook_root`` and ``user_id`` from dependencies, call
``onboarding_service``, return the typed model.

Routes:

  * ``GET    /api/v1/onboarding``          → ``get_state``. Reads the
    sentinel + the three "has_*" flags. Pure read; the SPA polls on
    every mount to decide between (wizard) / (banner) / (no UI).
  * ``POST   /api/v1/onboarding/complete`` → ``complete``. Stamps the
    sentinel and returns the updated state. Body matches
    :class:`OnboardingComplete` — ``{"status": "finished" | "skipped"}``.

The wizard's per-step forms (DZ, components, rig) reuse the existing
``/dropzones``, ``/containers``, ``/mains``, ``/reserves``, ``/aads``,
and ``/rigs`` endpoints. This module owns the sentinel only.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, status

from ..models.onboarding import OnboardingComplete, OnboardingState
from ..services import onboarding_service
from .deps import get_logbook_root, get_user_id

router = APIRouter(prefix="/api/v1/onboarding", tags=["onboarding"])


@router.get(
    "",
    response_model=OnboardingState,
    summary="Read first-run wizard state (D64)",
    description=(
        "Return whether the sentinel file ``.onboarding_completed`` "
        "is present at the logbook root, alongside three flags "
        "indicating whether the logbook already has a jumper, a "
        "dropzone, and a rig. The SPA renders the wizard when "
        "``completed`` is False AND at least one ``has_*`` flag is "
        "False; renders the Profile resumption banner when "
        "``completed`` is True AND some flag is still False; "
        "renders neither otherwise. Pure read — does not write."
    ),
)
def get_state_route(
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> OnboardingState:
    return onboarding_service.get_state(logbook_root, user_id)


@router.post(
    "/complete",
    response_model=OnboardingState,
    status_code=status.HTTP_200_OK,
    summary="Mark the wizard complete (D64)",
    description=(
        "Stamp the sentinel file at the logbook root with the "
        "current timestamp (D17) and the supplied "
        ":class:`OnboardingStatus`. ``finished`` records that the "
        "user walked every step; ``skipped`` records dismissal. "
        "Both values dismiss the wizard on subsequent launches. "
        "Idempotent: calling twice rewrites the sentinel with a "
        "fresh ``completed_at`` but the ``completed`` flag does "
        "not flip back. Body shape rejects unknown status values "
        "(422 RFC 9457). Returns the updated state so the SPA can "
        "transition to the next screen without a follow-up GET."
    ),
)
def complete_route(
    payload: OnboardingComplete,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> OnboardingState:
    return onboarding_service.complete(logbook_root, user_id, payload)
