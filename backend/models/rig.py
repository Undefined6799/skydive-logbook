"""Pydantic model for a rig (D33, D37, D38, R.2.0a).

A rig is the assembly: stable identity (id + nickname + jurisdiction +
repack history) plus four current component references that rotate
over the rig's life. The four refs are required at create time per
D37; the service layer enforces "every component is in zero or one
rigs" by setting each component's ``assigned_rig_id`` when the rig is
written (R.2.0c).

The rig is NOT a component — D34 is explicit. There is no ``status``
field, no ``assigned_rig_id``. ``notes_log`` reuses the
:class:`NotesLogEntry` shape from the component base since the use
case (free-text audit log) is identical.

D38 governs ``repack_history``: an ordered list (oldest first) where
the latest entry's ``date`` drives the repack-due clock per the rig's
``jurisdiction``. ``RigCreate`` accepts an initial list at onboarding;
``RigUpdate`` deliberately omits it — that mutation belongs to the R.5
write flow, not to the metadata-only update surface.
"""
from __future__ import annotations

from datetime import date as _date
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from ._component_base import NotesLogEntry


class Jurisdiction(StrEnum):
    """Closed enum of regulatory jurisdictions (D33, D38).

    Mirrors the ``Jurisdiction`` simpleType in ``SCHEMA.v1.xsd``.
    Reused for both the rig's ``jurisdiction`` (which rule set
    governs this rig today) and a repack event's
    ``jurisdiction_seal`` (which jurisdiction's seal the rigger
    affixed). Different concepts, same value space.
    """

    USPA = "USPA"
    """United States Parachute Association. 180-day repack clock."""

    CSPA = "CSPA"
    """Canadian Sport Parachuting Association. 270-day repack clock."""

    BOTH = "both"
    """Rig is jumped under both jurisdictions; both clocks apply and
    the soonest-due wins for status colors."""


class RepackEntry(BaseModel):
    """One repack event in a rig's append-only repack history (D38).

    Records rigger administrative action only — the consequences to
    the reserve / AAD are reflected on the component itself via a
    normal update to ``*_initial`` (D38 + Alex's 2026-04-24
    direction). Mirrors the ``RepackEntryType`` complex type in the
    XSD.
    """

    model_config = ConfigDict(extra="forbid")

    # Calendar date the rigger sealed the rig. Drives the rig's
    # next-repack-due clock per jurisdiction (D33 / D38).
    date: _date

    # Free-text rigger name. v0.1 does not track rigger credentials
    # per D33's non-decisions list. 1..120 chars matches the XSD.
    rigger: str = Field(min_length=1, max_length=120)

    # Closed enum: which jurisdiction's seal was affixed.
    jurisdiction_seal: Jurisdiction

    # Optional free-text. Used for "new cypres battery", "full
    # inspection", etc.
    notes: str | None = None


class Rig(BaseModel):
    """Canonical rig shape — serialized to ``rigs/<nickname>/rig.xml``.

    Identity is stable across component rotations: the UUID, the
    nickname (folder name), the jurisdiction, and the repack history
    don't change when the jumper swaps a main or a rigger swaps a
    reserve. Only the four ``current_*_id`` references rotate.
    """

    model_config = ConfigDict(extra="forbid")

    # Stable identifier. Never changes; the folder name (per D4
    # sanitize-and-rename) is derived from ``nickname``.
    id: UUID = Field(default_factory=uuid4)

    # Display name and folder name source. Required (a rig folder
    # must have a name); free text bounded at 120 chars to match
    # JumpTitle / DropzoneName.
    nickname: str = Field(min_length=1, max_length=120)

    # Closed enum: which regulatory rule set applies. Drives the
    # repack calendar (180 d USPA, 270 d CSPA, both ⇒ both).
    jurisdiction: Jurisdiction

    # Four current component references. Required at create time
    # per D37: a rig is conceptually a complete assembly. Nullable
    # variants for "rig under construction" can land later as an
    # additive change if the use case appears.
    current_main_id: UUID
    current_reserve_id: UUID
    current_aad_id: UUID
    current_container_id: UUID

    # D58: the "default rig for the jump-log form" flag. Service-
    # controlled — not on RigCreate / RigUpdate. The invariant
    # "≥1 non-trashed rig ⇒ exactly one starred" is maintained by
    # three transitions in the rig service: auto-star on create
    # when the logbook is empty, PUT /rigs/{id}/star to move the
    # flag, and auto-move when the starred rig is soft-deleted.
    starred: bool = False

    # D59: carousel position (left-to-right; 0 is leftmost). Service-
    # controlled — not on RigCreate / RigUpdate. ``None`` means
    # "this rig predates D59" — list_rigs sorts those after rigs
    # with an explicit value via the tiebreaker chain
    # (display_order ASC, then created_at ASC, then id). create_rig
    # stamps ``max(existing) + 1`` so new rigs land rightmost;
    # reorder_rigs is the only other mutator.
    display_order: int | None = None

    # D38: ordered list of repack events (oldest first). Empty by
    # default; the XML elides the wrapper element entirely so a
    # freshly-created rig's XML stays compact.
    repack_history: list[RepackEntry] = []

    # Rig-level free-text audit log. Reuses :class:`NotesLogEntry`
    # from the component base. Empty by default; wrapper elides
    # when empty.
    notes_log: list[NotesLogEntry] = []

    # D32 audit timestamps in canonical UTC ms form. Authored only
    # by the service layer; optional on the model so a hand-crafted
    # file without them validates.
    created_at: str | None = None
    updated_at: str | None = None


class RigCreate(BaseModel):
    """Request body for ``POST /api/v1/rigs`` (R.2.0b+).

    Per D38 onboarding accepts an initial ``repack_history`` so
    used-gear setup can land a rig with its existing repack record
    without hand-editing rig.xml.
    """

    model_config = ConfigDict(extra="forbid")

    nickname: str = Field(min_length=1, max_length=120)
    jurisdiction: Jurisdiction
    current_main_id: UUID
    current_reserve_id: UUID
    current_aad_id: UUID
    current_container_id: UUID
    repack_history: list[RepackEntry] = []
    notes_log: list[NotesLogEntry] = []


class RigUpdate(BaseModel):
    """Request body for ``PUT /api/v1/rigs/{id}`` (R.2.0c+, D66).

    Full-replace metadata shape. The four ``current_*_id`` refs are
    on this body but the service routes a swap intent through the
    dedicated swap path (D37): a direct PUT changing
    ``current_main_id`` is rejected with a 409.

    Per D66, ``repack_history`` is on this body — clients can
    replace the on-disk list to set/correct the repack record
    without hand-editing rig.xml. R.5's dedicated append + counter-
    side-effects flow remains deferred; this path is the metadata-
    only "I'm editing my own logbook" surface. The service uses an
    empty payload list to mean "preserve on-disk" rather than
    "clear", so pre-D66 clients that don't send the field don't
    accidentally wipe history (see ``update_rig``).
    """

    model_config = ConfigDict(extra="forbid")

    nickname: str = Field(min_length=1, max_length=120)
    jurisdiction: Jurisdiction
    current_main_id: UUID
    current_reserve_id: UUID
    current_aad_id: UUID
    current_container_id: UUID
    repack_history: list[RepackEntry] = []
    notes_log: list[NotesLogEntry] = []


class RigSummary(BaseModel):
    """Compact projection for the rigs list / picker.

    Mirrors the columns the SQLite ``rigs`` index will cache (R.3+)
    so list endpoints read at SQLite speed rather than walking
    ``rigs/*/rig.xml`` per request (D3).
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    nickname: str
    jurisdiction: Jurisdiction
    # D58: surfaced on the summary so GET /rigs is enough for the
    # LogJumpModal preselect — no extra fetch.
    starred: bool = False
    # D59: surfaced so the frontend carousel can render the
    # left-to-right order from a single list call.
    display_order: int | None = None
