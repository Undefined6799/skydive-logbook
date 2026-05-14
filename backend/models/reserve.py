"""Pydantic model for a reserve canopy component (D33, D34, R.0.2d).

Reserve covers the manufacturer-spec limits (``repack_limit`` /
``ride_limit``), the D35 wear counters (``repack_count_initial`` /
``ride_count_initial`` — reserves deliberately have no jump counter
per D35 §2553), and a small structured log of re-certification
extensions granted by a rigger or by the manufacturer.

The regulatory repack window (180 / 270 day calendar) lives on the
rig, not on the reserve — D33 makes the rig's ``<jurisdiction>`` the
driver of that clock.
"""
from __future__ import annotations

from datetime import date as _date

from pydantic import BaseModel, ConfigDict, Field

from ._component_base import ComponentBase, ComponentStatus, NotesLogEntry


class ReserveRecertExtension(BaseModel):
    """One recert extension event on a reserve (D34).

    Mirrors the ``ReserveRecertExtension`` complex type in the XSD.
    Minimal v0.1 shape; additive fields (linked-document references,
    rigger-credential snapshots) are deferred until a use case lands.
    """

    model_config = ConfigDict(extra="forbid")

    # D17 canonical UTC ISO-8601 timestamp. Service authored.
    granted_at: str

    # Calendar date the extension is valid through.
    extends_until: _date

    # Free-text rigger / manufacturer name. Optional because legacy
    # paper records may not preserve the granter.
    granted_by: str | None = None

    # Free-text justification. Optional.
    reason: str | None = None


class Reserve(ComponentBase):
    """Canonical reserve canopy shape.

    Serialized to ``inventory/reserves/<id>.xml``.
    """

    model_config = ConfigDict(extra="forbid")

    # Identification triple. Free text — the value space ("PD",
    # "Performance Designs", "Aerodyne") is unbounded enough to keep
    # open in v0.1.
    manufacturer: str | None = None
    model: str | None = None
    serial: str | None = None

    # Canopy area in square feet (e.g. 143, 160). Float so non-
    # integer sizes round-trip cleanly through xs:decimal.
    size_sqft: float | None = Field(default=None, ge=0)

    # Provenance.
    date_of_manufacture: _date | None = None

    # Manufacturer-spec limits. Both interpret as counts. ``None``
    # means "no manufacturer-set limit recorded"; the value lives on
    # the model strictly to drive D39's status-color rules in R.4.
    repack_limit: int | None = Field(default=None, ge=0)
    ride_limit: int | None = Field(default=None, ge=0)

    # D35: editable seeds for the two reserve counters. No
    # ``jump_count`` per D35 §2553. ``ride_count_derived`` stays at
    # zero in v0.1 — rides are entered manually at repack time.
    repack_count_initial: int = Field(default=0, ge=0)
    ride_count_initial: int = Field(default=0, ge=0)

    # Recert extension log. Empty by default; the XML elides the
    # wrapper element entirely when the list is empty so a hand-
    # crafted file without it round-trips byte-stable.
    recert_extensions: list[ReserveRecertExtension] = []


class ReserveCreate(BaseModel):
    """Request body for ``POST /api/v1/reserves`` (R.0.3+).

    R.2.0c.iii.b: ``assigned_rig_id`` is not on this model — see
    :class:`ContainerCreate` for the rig_service-owned-assignment
    rationale.
    """

    model_config = ConfigDict(extra="forbid")

    status: ComponentStatus = ComponentStatus.ACTIVE
    notes_log: list[NotesLogEntry] = []

    manufacturer: str | None = None
    model: str | None = None
    serial: str | None = None
    size_sqft: float | None = Field(default=None, ge=0)
    date_of_manufacture: _date | None = None
    repack_limit: int | None = Field(default=None, ge=0)
    ride_limit: int | None = Field(default=None, ge=0)
    repack_count_initial: int = Field(default=0, ge=0)
    ride_count_initial: int = Field(default=0, ge=0)
    recert_extensions: list[ReserveRecertExtension] = []


class ReserveUpdate(BaseModel):
    """Request body for ``PUT /api/v1/reserves/{id}`` (R.0.3+).

    Full-replace shape per the JumpUpdate / DropzoneUpdate pattern.

    R.2.0c.iii.b: ``assigned_rig_id`` is not on this model. Pydantic's
    ``extra="forbid"`` rejects any PUT body that includes it (422).
    """

    model_config = ConfigDict(extra="forbid")

    status: ComponentStatus
    notes_log: list[NotesLogEntry] = []

    manufacturer: str | None = None
    model: str | None = None
    serial: str | None = None
    size_sqft: float | None = Field(default=None, ge=0)
    date_of_manufacture: _date | None = None
    repack_limit: int | None = Field(default=None, ge=0)
    ride_limit: int | None = Field(default=None, ge=0)
    repack_count_initial: int = Field(default=0, ge=0)
    ride_count_initial: int = Field(default=0, ge=0)
    recert_extensions: list[ReserveRecertExtension] = []
