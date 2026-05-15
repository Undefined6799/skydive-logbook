"""Pydantic model for a single skydive jump.

The single source of truth for the jump shape. XSD (SCHEMA.v1.xsd) and the
SQLite index schema derive from this (D2).

Field naming convention: unit-suffixed integers (`exit_altitude_m`,
`freefall_time_s`) so there is no ambiguity on the wire (D12). The UI
converts at the edge.
"""
from __future__ import annotations

from datetime import date as _date
from datetime import time as _time
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..storage.filesystem import sanitize_filename
from .common import IANA_TZ_PATTERN, SHA256_HEX_PATTERN

# D57 removed the per-jump <environment> override, so this module no
# longer imports the ``Environment`` enum from ``dropzone``. The enum
# itself still lives in ``backend/models/dropzone.py`` and is used by
# ``Dropzone`` and ``Main.default_environment``.

# --------------------------------------------------------------------------- #
# D53 enums (Phase 1 added the matching XSD simple types)
# --------------------------------------------------------------------------- #

class JumpType(StrEnum):
    """Closed enum for the role/purpose of a jump (D53).

    Disjoint from ``Jump.discipline`` (free text — captures *how*
    you flew: angle, tracking, belly, freefly, …); ``jump_types``
    captures *what the jump was for*. Multi-valued at the jump
    level — a camera flyer on an angle is one jump with two values.

    Tandem stays on ``Jump.is_tandem`` (D47, Phase B.4); the
    currency calculator reads it. Two sources of truth for the
    same fact would be a bug factory, so ``TANDEM`` is intentionally
    absent from this enum.
    """

    REGULAR_JUMP = "regular_jump"
    COACHING = "coaching"
    INSTRUCTING = "instructing"
    CAMERA = "camera"
    ORGANIZING = "organizing"
    COACHED = "coached"
    INSTRUCTED = "instructed"


class Attachment(BaseModel):
    """One uploaded file on a jump (FlySight CSV, video, photo).

    Filename is validated with the same D4 rules as folder names (Q5):
    no forbidden characters, no Windows reserved device names, no
    trailing space/period, 1..255 bytes. The XSD enforces the broad
    shape; Pydantic enforces the full rule set.
    """
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    sha256: str = Field(pattern=SHA256_HEX_PATTERN)
    size: int = Field(ge=0)
    content_type: str | None = None

    @field_validator("filename")
    @classmethod
    def _validate_filename(cls, v: str) -> str:
        # sanitize_filename normalizes NFC and raises ValueError on any
        # cross-platform unsafe name. We return the normalized form so
        # equal logical filenames land on equal bytes in the XML.
        return sanitize_filename(v)


class Jump(BaseModel):
    """Canonical jump shape — serialized to jump.xml and rows in the SQLite index."""
    model_config = ConfigDict(extra="forbid")

    # Stable identifier. Never changes, even if jump_number or date are edited.
    id: UUID = Field(default_factory=uuid4)

    # The jumper's log number. Human-facing, editable, 1-indexed.
    jump_number: int = Field(gt=0)

    # Optional human-readable label (D4). Free text, not unique,
    # 120-char cap to stay well under the 255-byte filename limit.
    # The folder name is derived as `[<jump#>] <title>` (or bare
    # `[<jump#>]` when empty) at creation time.
    #
    # Title ↔ folder-name relationship is asymmetric (D4):
    #   * API title edit rewrites XML AND atomically renames the
    #     folder (same flow as jump_number correction, D23 §Renames).
    #   * Manual filesystem rename (Finder, mv, etc.) touches ONLY
    #     the folder name; XML is untouched, preserving any D6
    #     signature on jump.xml.
    # Data changes propagate to cosmetic layers; cosmetic changes
    # don't propagate back to data.
    title: str | None = Field(default=None, max_length=120)

    # Local calendar date — no timezone (D17).
    date: _date

    # Local clock time + IANA timezone (both optional, both or neither).
    time: _time | None = None
    timezone: str | None = Field(default=None, pattern=IANA_TZ_PATTERN)

    dropzone: str = Field(min_length=1)
    # D44: optional UUID reference to a dropzone record under
    # ``logbook_root/dropzones/<uuid>.xml``. The string ``dropzone``
    # field above stays as a free-text label for human-readable
    # display and for jumps logged before a DZ entity exists; the
    # reference is the structured side of the same fact.
    dropzone_id: UUID | None = None
    # D33 (R.2.2-light): optional UUID reference to a rig under
    # ``logbook_root/rigs/<nickname>/rig.xml``. Links the jump to
    # the assembly worn at log time so the UI can display the main
    # canopy and (later, R.2.3) write a frozen rig-snapshot.xml.
    # Absent for jumps logged before R.2.2 and for users who don't
    # track rigs.
    rig_id: UUID | None = None
    # D45 / D57: the per-jump environment override was removed by D57
    # alongside the Phase 1 LogJumpModal redesign. The wear-math
    # resolution order now reads from the linked dropzone's
    # ``environment`` and the main canopy's ``default_environment``
    # only. The ``Environment`` enum is still imported because
    # ``Dropzone`` carries one.
    # D45: Peelman's second modifier. When True, this jump's lineset
    # wear is incremented by +0.20 lb regardless of where the jump
    # took place. None and False are equivalent for the wear math;
    # they are distinguished only so a hand-crafted file with the
    # element absent round-trips identically (no spurious False
    # written on serialize).
    packed_in_poor_conditions: bool | None = None
    aircraft: str | None = None
    discipline: str | None = None
    # D47 / Phase B.4: marks this jump as one the jumper performed as
    # the Tandem Instructor. The currency calculator (Phase E) counts
    # is_tandem=True jumps inside each manufacturer's window
    # (UPT 90 d + 365 d, Strong tiered). Absent ≡ False in the
    # calculator and on disk; the absent-equals-false convention
    # matches packed_in_poor_conditions. v0.1 does not split the count
    # by tandem system — a jumper rated on both Vector and Sigma
    # flies tandems on whichever rig they have that day; jump.xml
    # does not carry the rig-system identity. Per-system counts can
    # land additively if needed.
    is_tandem: bool | None = None

    # Altitudes are stored in meters on the wire and in XML (D12).
    # Float (xs:decimal on the wire) so unit conversion round-trips
    # cleanly: 13500 ft → 4114.8 m → 13500 ft. Integer storage
    # would round 4114.8 to 4115 and re-display as 13501 ft.
    exit_altitude_m: float = Field(ge=0)
    deployment_altitude_m: float = Field(ge=0)
    freefall_time_s: int | None = Field(default=None, ge=0)

    notes: str | None = None
    attachments: list[Attachment] = []

    # D53: jump_types is multi-valued (a camera flyer on an angle is
    # one jump with two facets). Empty list ≡ unset on disk: the XSD
    # wrapper elides when the list is empty so a hand-crafted
    # jump.xml without it round-trips byte-stable.
    jump_types: list[JumpType] = []
    # D53 / D57: landing accuracy. Magnitude only (meters per D12).
    # The directional half (``landing_direction``) was removed by
    # D57 — the redesign captures landing accuracy as a single
    # magnitude. On-target landings leave this field None.
    landing_distance_m: float | None = Field(default=None, ge=0)
    # D53: packer reference. Absent ≡ self-packed; the logbook owner
    # is never a Person record (D54). Stale references (deleted
    # person) render soft-warned per D54 — the model layer does not
    # validate resolvability.
    packed_by: UUID | None = None
    # D53 / D57: ``group_size`` (the headline jumper count) was
    # removed by D57 — the count is implied by ``group_members``
    # and a redundant scalar invites contradiction.
    # ``group_members`` is the named subset (UUID refs to <person>).
    group_members: list[UUID] = []

    # Reserved per D6. Not yet read or written by any code path.
    signature: str | None = None

    # D32: audit timestamps in D17 canonical form
    # (YYYY-MM-DDThh:mm:ss.sssZ). Authored only by the service layer.
    # Optional on the model so a Jump parsed from a pre-D32 XML file
    # validates; reindex fills them from file mtime with a warning.
    # Clients never set these directly (JumpCreate / JumpUpdate do
    # not expose them).
    created_at: str | None = None
    updated_at: str | None = None


class JumpSummary(BaseModel):
    """Compact projection of a jump for list views (Phase 3.1).

    Populated directly from the SQLite index — no per-row XML read —
    so list endpoints stay O(rows) in SQLite time rather than O(rows)
    in parser time. Every field is also in the full ``Jump``; a
    summary is a strict subset with the fields a browsing user
    actually needs at a glance.
    """
    model_config = ConfigDict(extra="forbid")

    id: UUID
    jump_number: int = Field(gt=0)
    title: str | None = None
    date: _date
    dropzone: str
    # Index-cached fields (v4, 2026-04-28) so the Jumps log can render
    # discipline pills + aircraft and so client-side search has more
    # to match against without per-row XML reads. All optional —
    # older jumps logged before discipline became standard practice
    # carry None.
    aircraft: str | None = None
    discipline: str | None = None
    freefall_time_s: int | None = None
    # v7 (R.2.2-light.d.1, D33): cached rig reference. Lets the
    # JumpsLog list view render the main canopy per row by resolving
    # rig_id → rig.current_main_id → main on the client. None for
    # legacy jumps and quick-log jumps without a rig pick.
    rig_id: UUID | None = None


class JumpCreate(BaseModel):
    """Request body for POST /api/v1/jumps. Same shape as Jump but `id` is server-assigned."""
    model_config = ConfigDict(extra="forbid")

    jump_number: int = Field(gt=0)
    title: str | None = Field(default=None, max_length=120)
    date: _date
    time: _time | None = None
    timezone: str | None = Field(default=None, pattern=IANA_TZ_PATTERN)
    dropzone: str = Field(min_length=1)
    # D44 / D45: optional dropzone reference + per-jump wear-math
    # overrides. All three are absent on legacy clients and on quick
    # jump entry; the wear-math resolution order falls back per D45.
    dropzone_id: UUID | None = None
    # D33 (R.2.2-light): optional rig reference, mirrors the field on
    # Jump. The LogJumpModal picker writes this when the user selects
    # one of their rigs.
    rig_id: UUID | None = None
    # D57: per-jump <environment> override removed (see Jump above).
    packed_in_poor_conditions: bool | None = None
    aircraft: str | None = None
    discipline: str | None = None
    # D47 / Phase B.4: see Jump.is_tandem.
    is_tandem: bool | None = None
    exit_altitude_m: float = Field(ge=0)
    deployment_altitude_m: float = Field(ge=0)
    freefall_time_s: int | None = Field(default=None, ge=0)
    notes: str | None = None
    # D53 / D57: ``jump_types``, ``landing_distance_m``, ``packed_by``,
    # and ``group_members`` are the surviving D53 fields. Same
    # posture (all optional, list defaults to empty). Attachments are
    # not on JumpCreate (they arrive via the Upload mechanism).
    jump_types: list[JumpType] = []
    landing_distance_m: float | None = Field(default=None, ge=0)
    packed_by: UUID | None = None
    group_members: list[UUID] = []


class JumpUpdate(BaseModel):
    """Request body for PUT /api/v1/jumps/{id} (Phase 3.5, D31).

    Same field set as ``JumpCreate`` — no ``id`` (taken from the URL)
    and no ``attachments`` (v0.1 update is metadata-only per D31;
    attachment editing ships in a dedicated phase). A PUT is a full
    replace of the metadata: every field must be supplied, with
    unchanged fields echoing their current values. A client that
    wants partial-update semantics sends the current jump back with
    only the intended diff applied.
    """
    model_config = ConfigDict(extra="forbid")

    jump_number: int = Field(gt=0)
    title: str | None = Field(default=None, max_length=120)
    date: _date
    time: _time | None = None
    timezone: str | None = Field(default=None, pattern=IANA_TZ_PATTERN)
    dropzone: str = Field(min_length=1)
    # D44 / D45: same optional fields as JumpCreate. Editing a jump
    # with a DZ reference set keeps it; editing with the field
    # cleared (None) drops the reference and lets the wear math fall
    # back on the next reindex.
    dropzone_id: UUID | None = None
    # D33 (R.2.2-light): same posture as JumpCreate. Editing the
    # rig_id to None detaches the jump from the rig (e.g. legacy
    # cleanup); setting it to a different UUID re-links to a
    # different rig.
    rig_id: UUID | None = None
    # D57: per-jump <environment> override removed (see Jump above).
    packed_in_poor_conditions: bool | None = None
    aircraft: str | None = None
    discipline: str | None = None
    # D47 / Phase B.4: see Jump.is_tandem.
    is_tandem: bool | None = None
    exit_altitude_m: float = Field(ge=0)
    deployment_altitude_m: float = Field(ge=0)
    freefall_time_s: int | None = Field(default=None, ge=0)
    notes: str | None = None
    # D53 / D57: same posture as JumpCreate. Editing a jump with
    # these fields cleared (empty list / None) drops them; setting
    # them again on a later PUT re-attaches.
    jump_types: list[JumpType] = []
    landing_distance_m: float | None = Field(default=None, ge=0)
    packed_by: UUID | None = None
    group_members: list[UUID] = []

    # The four fields below appear on the ``GET /jumps/{id}`` response
    # but are server-controlled — clients can't actually set them.
    # ``extra="forbid"`` would normally reject them on a naive
    # GET-mutate-PUT round-trip (the body returned by GET cannot be
    # POSTed back without surgery). Declaring them here as ignored
    # optional fields preserves the strict-unknown rejection for
    # genuine typos while letting third-party tooling round-trip
    # cleanly. ``update_jump`` discards the payload values and
    # reuses the on-disk ones (id, attachments, signature,
    # created_at) plus a freshly stamped ``updated_at``.
    attachments: list[Attachment] = []
    signature: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
