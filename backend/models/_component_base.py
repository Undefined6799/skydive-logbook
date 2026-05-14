"""Shared base for rig-manager component models (D33, D34).

Each of the four inventory component kinds — Main, Reserve, AAD,
Container — has its own concrete Pydantic model that inherits from
:class:`ComponentBase` and adds its kind-specific fields. The shared
fields live here:

    id                — stable UUID, default-generated on create
    status            — closed lifecycle enum (D34)
    assigned_rig_id   — optional UUID reference; rig-side enforcement
                        of "every component is in zero or one rigs at
                        any time" lands in R.1 (D33)
    notes_log         — append-only audit log of free-text notes
    created_at        — D32 audit timestamp; service-authored
    updated_at        — D32 audit timestamp; service-authored

Per-kind counter ``*_initial`` fields (jump_count_initial,
fire_count_initial, repack_count_initial, ride_count_initial,
jumps_on_lineset_initial — see D35 / D46) live on the concrete
models, not on this base, because the set differs per kind (e.g.
Reserve has no jump counter; Main does).

Module name is leading-underscore because consumers should import
from the per-kind modules — the base is reusable plumbing, not a
public API surface.
"""
from __future__ import annotations

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class ComponentStatus(StrEnum):
    """Closed lifecycle enum for every rig-manager component (D34).

    Mirrors the ``ComponentStatus`` simpleType in ``SCHEMA.v1.xsd``.
    Adding a value here without a matching XSD addition (or vice
    versa) breaks XSD validation on the next round-trip — the test
    suite catches this via ``test_component_base.py``.
    """

    ACTIVE = "active"
    """In service. Visible in swap dropdowns."""

    RETIRED = "retired"
    """End-of-life decision. Hidden from swap pickers; preserved on
    the XML for history."""

    SOLD = "sold"
    """Owner no longer holds it. Same UI treatment as retired;
    distinct value for record-keeping."""

    OUT_OF_SERVICE = "out_of_service"
    """Temporarily unavailable (e.g. awaiting service). Hidden from
    swap pickers; can return to active."""


class NotesLogEntry(BaseModel):
    """One entry in a component's append-only notes log (D34).

    Minimal v0.1 shape per the design memo: a D17 canonical UTC
    timestamp plus the text. Optional attribution / link-to-
    attachment fields are deliberately deferred — they are additive
    within v1 (D18) and the model and XSD will both grow them when
    the use case lands (e.g. D6 signing wanting per-note authorship).
    """

    model_config = ConfigDict(extra="forbid")

    # D17 canonical UTC ISO-8601 timestamp (e.g.
    # ``2026-04-28T14:30:00.000Z``). Stored as a string for the same
    # reason Jump.created_at is a string — it's an opaque token the
    # service layer authors with ``_now_utc_iso()`` and the XSD
    # validates as ``xs:dateTime``. Keeping it as a string avoids a
    # tz-aware ``datetime`` on the wire which would force UTC
    # normalization choices we are not yet ready to make for free-
    # text user content.
    at: str

    # Free-text content. 1..2000 chars matches the XSD bound.
    text: str = Field(min_length=1, max_length=2000)


class ComponentBase(BaseModel):
    """Base shape inherited by every concrete component model.

    Subclasses MUST keep ``model_config = ConfigDict(extra="forbid")``
    to inherit the no-unknown-fields posture. Pydantic v2 carries the
    config through inheritance so a subclass that doesn't redeclare
    it inherits the strict shape, but redeclaring it is a small habit
    that keeps the intent local to each model file.
    """

    model_config = ConfigDict(extra="forbid")

    # Stable identifier. Never changes; the filename is ``<id>.xml``
    # under ``inventory/<kind>s/`` (D33), so renaming via the UI
    # never moves the file.
    id: UUID = Field(default_factory=uuid4)

    # Lifecycle. Defaults to active so a freshly-created component
    # is immediately usable; the user explicitly transitions later.
    status: ComponentStatus = ComponentStatus.ACTIVE

    # Optional reference to the rig that currently holds this
    # component. None means "in inventory, not assigned." The "every
    # component is in zero or one rigs at any time" invariant is
    # enforced at the service layer in R.1 (D33).
    assigned_rig_id: UUID | None = None

    # Append-only notes log. Empty by default; the XML elides the
    # wrapper element entirely when the list is empty so a hand-
    # crafted file without it round-trips byte-stable (matches the
    # ``aircraft`` list pattern on Dropzone, R.D.6). Pydantic v2
    # copies the list per-instance so the literal default is safe.
    notes_log: list[NotesLogEntry] = []

    # D32 audit timestamps in canonical UTC ms form. Authored only
    # by the service layer (the ``Create`` / ``Update`` variants on
    # each concrete model do not expose them). Optional on the model
    # so a hand-crafted file without them validates; reindex fills
    # them from file mtime with a warning.
    created_at: str | None = None
    updated_at: str | None = None
