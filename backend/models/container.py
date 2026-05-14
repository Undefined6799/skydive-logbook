"""Pydantic model for a container component (D33, D34, R.0.2b).

Container is the simplest of the four rig-manager components: an
identification triple (manufacturer / model / serial), a free-text
size, a date of manufacture, and a single jump counter.

Per D34 the universal lifecycle / audit fields live on
:class:`ComponentBase`; this module only adds container-specific
shape on top.
"""
from __future__ import annotations

from datetime import date as _date

from pydantic import BaseModel, ConfigDict, Field

from ._component_base import ComponentBase, ComponentStatus, NotesLogEntry


class Container(ComponentBase):
    """Canonical container shape.

    Serialized to ``inventory/containers/<id>.xml`` and indexed in
    SQLite once R.0.3 lands the create / get services. Manufacturer /
    model / serial / size are all optional because used-gear records
    do not always carry every field — the model defers to the user's
    knowledge rather than rejecting partial provenance.
    """

    model_config = ConfigDict(extra="forbid")

    # The basic identification triple. Free text in v0.1; a future
    # decision could close manufacturer to a known set ("Sun Path",
    # "UPT", "Mirage") but the open shape covers small / vintage
    # makers too.
    manufacturer: str | None = None
    model: str | None = None
    serial: str | None = None

    # Container size is letter-coded across the industry ("M22",
    # "Large", "T1") rather than expressed as an area, so it stays
    # a string here. Mains and reserves use float ``size_sqft``
    # instead — see their respective modules.
    size: str | None = None

    # Date of manufacture. Optional because old gear may have lost
    # its DOM record; required for the AAD service-window math (D39)
    # but not load-bearing on container.
    date_of_manufacture: _date | None = None

    # D35: editable starting value for the jump counter. The derived
    # value lives in the SQLite index and is rebuilt from
    # rig-snapshot.xml entries on every reindex (R.3+); display is
    # ``jump_count_initial + jump_count_derived``.
    jump_count_initial: int = Field(default=0, ge=0)


class ContainerCreate(BaseModel):
    """Request body for ``POST /api/v1/containers`` (R.0.3+).

    Same fields as :class:`Container` minus the server-assigned
    ``id`` and the audit timestamps. Status is optional on create
    and defaults to active — the common case is a freshly-onboarded
    component.

    R.2.0c.iii.b: ``assigned_rig_id`` is not on this model. Brand-new
    components always start unassigned (None); ``rig_service.create_rig``
    sets the field via the sanctioned ``set_assigned_rig_id`` helper.
    """

    model_config = ConfigDict(extra="forbid")

    status: ComponentStatus = ComponentStatus.ACTIVE
    notes_log: list[NotesLogEntry] = []

    manufacturer: str | None = None
    model: str | None = None
    serial: str | None = None
    size: str | None = None
    date_of_manufacture: _date | None = None
    jump_count_initial: int = Field(default=0, ge=0)


class ContainerUpdate(BaseModel):
    """Request body for ``PUT /api/v1/containers/{id}`` (R.0.3+).

    Full-replace shape per the JumpUpdate / DropzoneUpdate pattern.
    Every field must be supplied; ``id`` is taken from the URL,
    audit timestamps are managed server-side.

    R.2.0c.iii.b: ``assigned_rig_id`` is not on this model.
    Pydantic's ``extra="forbid"`` rejects any PUT body that includes
    it (422), keeping that field rig_service-owned per D37.
    """

    model_config = ConfigDict(extra="forbid")

    status: ComponentStatus
    notes_log: list[NotesLogEntry] = []

    manufacturer: str | None = None
    model: str | None = None
    serial: str | None = None
    size: str | None = None
    date_of_manufacture: _date | None = None
    jump_count_initial: int = Field(default=0, ge=0)
