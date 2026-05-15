"""Pydantic model for an AAD component (D33, D34, R.0.2c).

AAD (Automatic Activation Device) is the cutaway-and-deploy backup
that fires the reserve when freefall speed and altitude indicate the
jumper has not deployed in time. v0.1 covers the three mainstream
brands — Airtec Cypres, Vigil, and MarS — plus retired models that
read ``status="retired"`` (D34).

Field naming: ``manufacturer`` (not ``brand``) per D34's 2026-04-28
amendment, for symmetry with Main / Reserve / Container. D39's
airworthiness rules will key on (manufacturer, model, DOM tier);
storing the field on the model makes the rule lookup independent of
spelling.
"""
from __future__ import annotations

from datetime import date as _date

from pydantic import BaseModel, ConfigDict, Field

from ._component_base import ComponentBase, ComponentStatus, NotesLogEntry


class AAD(ComponentBase):
    """Canonical AAD shape.

    Serialized to ``inventory/aads/<id>.xml``. Service-window / EOL
    outputs are not stored — D39's pure-function lookup derives them
    from manufacturer + model + DOM at read time.
    """

    model_config = ConfigDict(extra="forbid")

    # The basic identification triple. Free text in v0.1; D39's rule
    # set covers a small closed list (Cypres / Vigil / MarS) but
    # leaving the field open avoids breakage when a fourth brand or
    # a vintage / unsupported unit is recorded.
    manufacturer: str | None = None
    model: str | None = None
    serial: str | None = None

    # Date of manufacture — load-bearing for D39's DOM-tier branches
    # (Cypres 2 pre-2016 / 2016 / 2017+ each have different service
    # calendars). Optional on the model so a pre-existing record
    # with a missing DOM still validates; D39 surfaces a "DOM
    # required" yellow status when needed.
    date_of_manufacture: _date | None = None

    # Current mode setting. Per-manufacturer values ("Pro", "Expert",
    # "Tandem", "Student", "Student/Tandem") resolved in D39's
    # implementation — model stays open here so a typo or
    # manufacturer-specific name does not block onboarding.
    mode: str | None = None

    # Whether the unit allows in-field mode changes. Some older units
    # (early Cypres 2s) ship with the mode locked at factory.
    # Optional — None means "unknown / not recorded."
    is_changeable_mode: bool | None = None

    # D35: editable seeds for the two AAD counters. The derived
    # ``jump_count_derived`` is rebuilt from rig-snapshot.xml entries
    # on reindex (R.3+). The derived ``fire_count_derived`` stays at
    # zero in v0.1 — fires are entered manually at repack time per
    # D35 / D38.
    jump_count_initial: int = Field(default=0, ge=0)
    fire_count_initial: int = Field(default=0, ge=0)

    # D35: derived count of jumps logged against the rig this AAD is
    # currently assigned to. Read-only output field — populated by
    # ``aad_service`` on get / list from the SQLite jumps index;
    # never persisted to ``aad.xml`` (the XSD does not declare it).
    jump_count_derived: int = Field(default=0, ge=0)

    # D35: display value. See
    # :class:`backend.models.container.Container.jump_count_total`
    # for why this is a regular field rather than a computed_field.
    jump_count_total: int = Field(default=0, ge=0)


class AADCreate(BaseModel):
    """Request body for ``POST /api/v1/aads`` (R.0.3+).

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
    date_of_manufacture: _date | None = None
    mode: str | None = None
    is_changeable_mode: bool | None = None
    jump_count_initial: int = Field(default=0, ge=0)
    fire_count_initial: int = Field(default=0, ge=0)


class AADUpdate(BaseModel):
    """Request body for ``PUT /api/v1/aads/{id}`` (R.0.3+).

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
    date_of_manufacture: _date | None = None
    mode: str | None = None
    is_changeable_mode: bool | None = None
    jump_count_initial: int = Field(default=0, ge=0)
    fire_count_initial: int = Field(default=0, ge=0)
