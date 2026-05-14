"""Pydantic model for a dropzone (D44).

A dropzone is a first-class XML record under ``logbook_root/dropzones/
<uuid>.xml``. Each jump can reference one via ``<dropzone_id>`` in
``jump.xml``, and the dropzone's ``environment`` field feeds line-wear
math (D45) as the per-jump environment fallback.

Field naming convention matches the rest of the schema (D2, D12).
``country`` is ISO 3166-1 alpha-2; the closed enum on ``environment``
is shared with the per-jump ``<environment>`` override on Jump.
"""
from __future__ import annotations

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class Environment(StrEnum):
    """Closed enum of jumping-environment values feeding D45 wear math.

    Values map to additive deltas in :data:`ENV_DELTA_LB` (which
    will live in the line-wear computation module landing in R.4).
    Adding a fourth value here is a contract change — see D45.
    """

    CLEAN_GRASS = "clean_grass"
    DUST_SAND_SALT = "dust_sand_salt"
    DESERT = "desert"


class DropzoneAircraft(BaseModel):
    """One plane in a dropzone's fleet (D44, added 2026-04-28).

    Free-text shape: the jumper types the model the way they'd say
    it ("Twin Otter", "Cessna 208 Caravan"). The optional tail
    number is for the user's own bookkeeping — no registry check.
    Both fields are bounded to keep accidental paste-the-wrong-
    thing input from ballooning the XML.
    """

    model_config = ConfigDict(extra="forbid")

    # 'model' is a domain word here ("the plane's model"). Pydantic
    # v2 reserves the ``model_*`` prefix for its own machinery
    # (model_dump, model_validate, etc.) — bare ``model`` is fine.
    model: str = Field(min_length=1, max_length=120)
    tail_number: str | None = Field(default=None, min_length=1, max_length=32)


# Country code: ISO 3166-1 alpha-2, two uppercase ASCII letters.
# Matches the XSD ``CountryCode`` simple type. We deliberately do NOT
# validate against the live ISO list at this layer — the XSD pattern
# rejects obvious garbage (lowercase, length, non-letters) and the
# few unassigned codes that pass shape (XX, ZZ, AA) are a known
# acceptable false-positive surface for v0.1.
_ISO_3166_ALPHA2_PATTERN = r"^[A-Z]{2}$"


class Dropzone(BaseModel):
    """Canonical dropzone shape — serialized to ``dropzones/<uuid>.xml``."""

    model_config = ConfigDict(extra="forbid")

    # Stable identifier. Never changes; the filename is ``<id>.xml``
    # so even renaming via the UI never moves the file.
    id: UUID = Field(default_factory=uuid4)

    # Display name (e.g. "Parachutisme Adrénaline"). Required, free
    # text; mirrors JumpTitle's 120-char cap so listing UIs lay out
    # predictably and the value stays well under any future filename
    # derivation that wants to embed it.
    name: str = Field(min_length=1, max_length=120)

    # City name. Required because it disambiguates same-named DZs
    # across regions ("Skydive City — Florida" vs "anywhere else").
    city: str = Field(min_length=1, max_length=120)

    # Province / state / region. Optional: not every country uses
    # one, and small-country DZs read fine without it.
    province: str | None = None

    # ISO 3166-1 alpha-2 (D44). The XSD pattern enforces the same
    # shape; this Pydantic check rejects bad input before it reaches
    # serialization.
    country: str = Field(pattern=_ISO_3166_ALPHA2_PATTERN)

    # Closed environment enum (D45). The single source of the
    # per-jump environment when no jump-level override is set.
    environment: Environment

    # D44 (added 2026-04-28): fleet of aircraft typically jumped
    # at this DZ. Empty list when not specified — the XML elides
    # the wrapper element entirely so a hand-crafted file without
    # this addition round-trips byte-stable.
    aircraft: list[DropzoneAircraft] = []

    notes: str | None = None

    # D60: service-controlled "default dropzone" flag. The jump-log
    # form prefills its <dropzone_id> picker with the starred DZ.
    # Maintained by dropzone_service via auto-star-on-create (when
    # the logbook had zero DZs), set_star (clear-then-stamp), and
    # delete-of-starred (auto-move to a successor before the soft-
    # delete commits). Not on DropzoneCreate / DropzoneUpdate so the
    # only mutation path is the dedicated star endpoint — clients
    # can't accidentally flip the flag via a metadata PUT. Defaults
    # False so a hand-crafted dropzone.xml without the element
    # round-trips; the XSD elides the element when False.
    starred: bool = False

    # D32: audit timestamps in canonical UTC ms form, same posture
    # as Jump. Authored by the service layer; optional on the model
    # so a hand-crafted file validates.
    created_at: str | None = None
    updated_at: str | None = None


class DropzoneCreate(BaseModel):
    """Request body for ``POST /api/v1/dropzones`` (R.D.2).

    Same field set as :class:`Dropzone` minus the server-assigned
    ``id`` and the audit timestamps.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    city: str = Field(min_length=1, max_length=120)
    province: str | None = None
    country: str = Field(pattern=_ISO_3166_ALPHA2_PATTERN)
    environment: Environment
    aircraft: list[DropzoneAircraft] = []
    notes: str | None = None


class DropzoneUpdate(BaseModel):
    """Request body for ``PUT /api/v1/dropzones/{id}`` (R.D.2).

    Full replace — every field must be supplied. ID is taken from the
    URL; audit timestamps are managed server-side.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    city: str = Field(min_length=1, max_length=120)
    province: str | None = None
    country: str = Field(pattern=_ISO_3166_ALPHA2_PATTERN)
    environment: Environment
    aircraft: list[DropzoneAircraft] = []
    notes: str | None = None


class DropzoneSummary(BaseModel):
    """Compact projection for the DZ list / picker.

    Mirrors the columns cached on the SQLite ``dropzones`` index
    table (R.D.3) so list endpoints read at SQLite speed rather
    than walking ``dropzones/*.xml`` per request (D3).
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    city: str
    country: str
    environment: Environment
    # D60: surfaced on the summary so the LogJumpModal can find the
    # starred DZ without a second round-trip through GET
    # /dropzones/{id}. Read directly from the dropzones index
    # ``starred`` column (v10 schema).
    starred: bool = False
