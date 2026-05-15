"""Pydantic models for the first-run onboarding wizard (D64).

Unlike the rest of ``backend/models/``, these are NOT on-disk entities
— there is no XSD, no XML serialiser. The onboarding sentinel lives
as a small JSON document at ``<root>/.onboarding_completed`` and the
endpoint return shape is built directly from filesystem + index
state at request time.

The models here exist for two reasons:

  * The REST adapter wants a typed response shape so FastAPI's
    OpenAPI generator surfaces the wizard contract to third-party
    tooling (D7 + D18).
  * ``OnboardingComplete`` constrains the request body to the
    two-value status enum, so a typo in the SPA surfaces as a 422
    rather than landing as an unknown string in the sentinel.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class OnboardingStatus(StrEnum):
    """End-state of the wizard, recorded inside the sentinel.

    ``finished`` means the user walked every step; ``skipped`` means
    they dismissed the wizard at some point before the rig step
    completed. Both states dismiss the wizard on subsequent launches
    — the value is purely informational for the operator / future
    analytics / the Profile resumption banner.
    """

    FINISHED = "finished"
    SKIPPED = "skipped"


class OnboardingState(BaseModel):
    """Response shape for ``GET /api/v1/onboarding`` (D64).

    The SPA renders the wizard when ``completed`` is False and at
    least one of ``has_jumper`` / ``has_dropzones`` / ``has_rigs`` is
    False. When ``completed`` is True the wizard never shows — the
    Profile banner picks up the resumption nudge.
    """

    completed: bool = Field(
        ...,
        description=(
            "True when the sentinel file ``.onboarding_completed`` "
            "is present at the logbook root. Once true, the wizard "
            "is dismissed permanently (per logbook); the Profile "
            "banner handles in-app resumption."
        ),
    )
    completed_at: str | None = Field(
        default=None,
        description=(
            "ISO 8601 UTC timestamp (D17) parsed from the sentinel, "
            "or null when the sentinel is absent / malformed."
        ),
    )
    status: OnboardingStatus | None = Field(
        default=None,
        description=(
            "``finished`` or ``skipped`` — parsed from the sentinel. "
            "Null when the sentinel is absent or the recorded value "
            "doesn't match the enum."
        ),
    )
    has_jumper: bool = Field(
        ...,
        description="True when ``jumpers/`` contains at least one record.",
    )
    has_dropzones: bool = Field(
        ...,
        description="True when the dropzones index has ≥1 row.",
    )
    has_rigs: bool = Field(
        ...,
        description="True when ``rigs/`` contains at least one folder.",
    )


class OnboardingComplete(BaseModel):
    """Request body for ``POST /api/v1/onboarding/complete`` (D64).

    A single field — the recorded end-state — constrained to the
    two-value :class:`OnboardingStatus` enum. A malformed body fails
    Pydantic validation and surfaces as a 422 RFC 9457 problem.
    """

    status: OnboardingStatus
