"""Pydantic model for a main canopy component (D33, D34, R.0.2e).

Main is the heaviest of the four inventory components because of the
nested lineset state: a single optional ``current_lineset`` (the
lineset in service today) plus a ``lineset_history`` list of
archived prior linesets. Both share the :class:`Lineset` shape per
D34.

The lineset is **not** a first-class entity — it lives nested on
main, never standalone. On reline, the old lineset is appended to
``lineset_history`` and a new ``current_lineset`` is installed. No
field on the archived lineset is mutated; D35's projection layer
keeps computing ``consumed_lb_derived`` for archived linesets even
though no future jump can reference them (rig-snapshot.xml on each
historical jump pins it to the lineset id that was current at log
time, per D36).
"""
from __future__ import annotations

from datetime import date as _date
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from ._component_base import ComponentBase, ComponentStatus, NotesLogEntry
from .dropzone import Environment


class Lineset(BaseModel):
    """Shared shape for a main's current and archived linesets (D34).

    Fields mirror the ``LinesetType`` complex type in the XSD. The
    ``id`` is a stable UUID so jump-time snapshots (R.2+) can pin to
    a specific lineset and reline does not silently mutate the wear
    math of historical jumps (D36).
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)

    # Free text — manufacturer / type, e.g. "V750", "HMA-500",
    # "Vectran 750". Open value space so a future line type doesn't
    # require an XSD change.
    line_type: str = Field(min_length=1)

    # Manufacturer's spec used by D45's lb-budget calculation.
    # Per D46 the starting budget is computed as
    # ``breaking_strength_lb − jumper.exit_weight_lb`` (live from
    # the active Jumper record, not snapshotted on the lineset).
    breaking_strength_lb: float = Field(gt=0)

    # When this lineset went on the main.
    install_date: _date

    # Free-text rigger name. Optional — legacy paper records may
    # not preserve it.
    installed_by: str | None = None

    # D46 wear-counter seed (was ``consumed_lb_initial: float`` per
    # D34/D35; superseded). A count of pre-logbook jumps on this
    # lineset; D45's wear math treats each migrated jump as a
    # baseline 1.0 lb of consumed budget. Defaults to 0 for fresh
    # installs; used-gear setup writes the rigger's hand-counted
    # number.
    jumps_on_lineset_initial: int = Field(default=0, ge=0)

    # D35 / D46: derived count of jumps logged against the rig
    # holding this lineset. Read-only output field — populated by
    # ``main_service`` when reading the parent ``Main``; never
    # persisted to ``main.xml`` (the XSD does not declare it). v0.1
    # approximates "jumps on this lineset" as "jumps on the rig"
    # because rig-snapshot.xml's per-jump lineset attribution is
    # R.4 territory; in the common case where the lineset doesn't
    # change between jumps the approximation matches exactly.
    jumps_on_lineset_derived: int = Field(default=0, ge=0)

    # D35: display value — ``initial + derived``. Plain field (not
    # ``@computed_field``) so ``model_dump()`` stays compatible
    # with the create-services' ``Model(**Create.model_dump())``
    # reconstruction pattern.
    jumps_on_lineset_total: int = Field(default=0, ge=0)


class Main(ComponentBase):
    """Canonical main canopy shape.

    Serialized to ``inventory/mains/<id>.xml``.
    """

    model_config = ConfigDict(extra="forbid")

    # Identification triple. Free text in v0.1.
    manufacturer: str | None = None
    model: str | None = None
    serial: str | None = None

    # Canopy area in square feet (e.g. 170, 150, 107). Float for
    # round-trip cleanliness through xs:decimal — same posture as
    # Reserve.size_sqft and Jump.exit_altitude_m.
    size_sqft: float | None = Field(default=None, ge=0)

    # Provenance.
    date_of_manufacture: _date | None = None

    # D45 / D33: the third fallback in the wear-math resolution
    # chain (jump → DZ → main → clean_grass). Renamed from D33's
    # original "default_environment_flags" on 2026-04-28 (R.0.2e) —
    # the value is a single Environment, not a bit set, so the
    # "flags" suffix was misleading. ``None`` means "no main-default
    # set" and wear math falls through to clean_grass.
    default_environment: Environment | None = None

    # D45 wear-math RDS flag. When True, the per-jump wear budget
    # (computed in R.4 against this main's current_lineset) adds a
    # flat +0.15 lb delta. Stored on the canopy because RDS is a
    # physical property of the assembly, not a per-jump decision.
    # Defaults to False — most canopies are non-RDS — and the XSD
    # elides the element when False so pre-D45-reification main.xml
    # stays byte-stable.
    has_rds: bool = False

    # D35: editable seed for the jump counter.
    jump_count_initial: int = Field(default=0, ge=0)

    # D35: derived count of jumps logged against the rig this main
    # is currently assigned to. Read-only output field — populated
    # by ``main_service`` on get / list from the SQLite jumps index;
    # never persisted to ``main.xml`` (the XSD does not declare it).
    jump_count_derived: int = Field(default=0, ge=0)

    # D35: display value. See
    # :class:`backend.models.container.Container.jump_count_total`
    # for why this is a regular field rather than a computed_field.
    jump_count_total: int = Field(default=0, ge=0)

    # Optional. ``None`` means "main has not been lined since
    # onboarding" (e.g. a brand-new main on its factory lines that
    # nobody has touched yet). Once a rigger installs a lineset,
    # this is populated; a reline appends the previous value to
    # ``lineset_history`` and sets a new ``current_lineset``.
    current_lineset: Lineset | None = None

    # Archived prior linesets, in install order (oldest first).
    # Empty by default; the XML wrapper elides entirely when the
    # list is empty.
    lineset_history: list[Lineset] = []


class MainCreate(BaseModel):
    """Request body for ``POST /api/v1/mains`` (R.0.3+).

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
    default_environment: Environment | None = None
    has_rds: bool = False
    jump_count_initial: int = Field(default=0, ge=0)
    current_lineset: Lineset | None = None
    lineset_history: list[Lineset] = []


class MainUpdate(BaseModel):
    """Request body for ``PUT /api/v1/mains/{id}`` (R.0.3+).

    Full-replace shape per the JumpUpdate / DropzoneUpdate pattern.
    The reline workflow (move ``current_lineset`` into
    ``lineset_history``, install a new ``current_lineset``) lands as
    a dedicated service-layer operation in a later phase; the PUT
    itself is general-purpose and accepts any shape that satisfies
    the model.

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
    default_environment: Environment | None = None
    has_rds: bool = False
    jump_count_initial: int = Field(default=0, ge=0)
    current_lineset: Lineset | None = None
    lineset_history: list[Lineset] = []
