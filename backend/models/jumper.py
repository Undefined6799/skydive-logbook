"""Pydantic models for a jumper and the credentials they hold (D33, D47).

A jumper is an XML record. Per D33 it carried identity (id, optional
name) plus the load-math input (exit_weight_lb + the staleness clock
exit_weight_updated_at). Per D47 (Phase B.4, this slice) the jumper
also carries five parallel credential collections plus an attachments
registry:

    memberships     — federation memberships (CSPA, USPA, "other") with
                      member number, expiry, optional card attachment.
    cops            — Certificates of Proficiency / licenses. CSPA's
                      Solo/A/B/C/D, USPA's A/B/C/D, free text for
                      "other" federations.
    ratings         — federation-issued ratings. CSPA's coach /
                      instructor / examiner / rigger tiers, USPA's
                      Coach / AFFI / IAD-I / S/L-I / TI / Examiner
                      tiers / CD / IECD / PRO / S&TA, free text for
                      "other".
    tandem_ratings  — manufacturer-issued tandem ratings, scoped to
                      a rig system (UPT Vector, UPT Sigma, Strong
                      Dual Hawk, "other"). Carries the optional
                      currency_reset_at — the manual override that
                      lets the user dismiss the not-current warning
                      after a supervised re-currency jump.
    medicals        — government-issued aviation medicals (Class III
                      for v0.1; FAA / Transport Canada / foreign
                      equivalent — issuing authority is free text).
    attachments     — files attached to the jumper (PDFs / images of
                      cards and medical certificates). Credential
                      records reference an attachment by `id`; the
                      bytes live on disk under
                      ``logbook_root/jumpers/<id>/attachments/``.

Cross-field constraints enforced here (XSD 1.0 has no xs:assert; per
D47 the strict rules live at the Pydantic / service layer):

    * `org` ∈ {CSPA, USPA} → `org_other` must be None.
    * `org == OTHER` → `org_other` must be a non-empty string.
    * Cop.level / FederationRating.code: when org=CSPA the value must
      be in CSPACopLevel / CSPARatingCode; when org=USPA the value
      must be in USPACopLevel / USPARatingCode; when org=OTHER the
      value is free text up to 40 chars.
    * `system` ∈ {upt_vector, upt_sigma, strong_dual_hawk} →
      `system_other` must be None.
    * `system == OTHER` → `system_other` must be a non-empty string.

The attachments-must-have-id constraint is enforced at the
JumperAttachment model level (id is required, not optional) — the
shared XSD AttachmentType permits id to elide for jump-side
attachments, but a jumper attachment without an id cannot be
referenced by a credential and would be unreachable garbage. The
"every card_attachment_id resolves to an attachment with that id"
invariant lives in the service layer (Phase D).

v0.1 ships single-jumper per D33. The entity supports multiple
records for forward compat — the file path is keyed on UUID rather
than on "the only jumper".
"""
from __future__ import annotations

from datetime import date as _date
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..storage.filesystem import sanitize_filename
from .common import SHA256_HEX_PATTERN

# --------------------------------------------------------------------------- #
# D47 enums (Phase B.1 added the matching XSD simple types)
# --------------------------------------------------------------------------- #

class OrgEnum(StrEnum):
    """The federation that issued a membership / CoP / rating."""

    CSPA = "CSPA"
    USPA = "USPA"
    OTHER = "OTHER"


class CSPACopLevel(StrEnum):
    """CSPA Certificate of Proficiency levels (cspa.ca/en/cop)."""

    SOLO = "solo"
    A = "a"
    B = "b"
    C = "c"
    D = "d"


class USPACopLevel(StrEnum):
    """USPA license levels (USPA SIM 3-1)."""

    A = "a"
    B = "b"
    C = "c"
    D = "d"


class CSPARatingCode(StrEnum):
    """CSPA-issued ratings — coach / instructor / examiner / rigger /
    EJR. Tandem instructor is intentionally absent (it lives in
    TandemSystem because the rating is manufacturer-issued, not
    federation-issued)."""

    C1 = "c1"
    C2 = "c2"
    C3_WINGSUIT = "c3_wingsuit"
    C3_CANOPY_PILOTING = "c3_canopy_piloting"
    C3_FREEFLY = "c3_freefly"
    C3_CANOPY_FORMATION = "c3_canopy_formation"
    CDC = "cdc"
    JM = "jm"
    JMR = "jmr"
    GCI = "gci"
    SSI = "ssi"
    PFFI = "pffi"
    SSE = "sse"
    LF = "lf"
    RIGGER_A = "rigger_a"
    RIGGER_A1 = "rigger_a1"
    RIGGER_A2 = "rigger_a2"
    RIGGER_B = "rigger_b"
    RIGGER_INSTRUCTOR = "rigger_instructor"
    RIGGER_EXAMINER = "rigger_examiner"
    EJR = "ejr"


class USPARatingCode(StrEnum):
    """USPA-issued ratings — coach + four instructional ratings (with
    examiner tiers) + CD + IECD + PRO + S&TA appointment.

    Master / Senior Rigger are FAA Part 65, not USPA-issued; they do
    not appear here."""

    COACH = "coach"
    AFFI = "affi"
    IAD_I = "iad_i"
    SL_I = "sl_i"
    TI = "ti"
    COACH_EXAMINER = "coach_examiner"
    AFFI_EXAMINER = "affi_examiner"
    IAD_EXAMINER = "iad_examiner"
    SL_EXAMINER = "sl_examiner"
    TI_EXAMINER = "ti_examiner"
    COURSE_DIRECTOR = "course_director"
    IECD = "iecd"
    PRO = "pro"
    STA = "sta"


class TandemSystem(StrEnum):
    """Tandem rig system. UPT (the company) ships Vector and Sigma as
    separate ratings; Strong ships the Dual Hawk. The currency rule
    is system-specific (not company-specific) — see D47 / Phase E."""

    UPT_VECTOR = "upt_vector"
    UPT_SIGMA = "upt_sigma"
    STRONG_DUAL_HAWK = "strong_dual_hawk"
    OTHER = "other"


class MedicalKind(StrEnum):
    """Government-issued aviation medical kind. v0.1 only models
    Class III; higher classes can land additively."""

    CLASS_III = "class_iii"


# --------------------------------------------------------------------------- #
# Shared cross-field helpers
# --------------------------------------------------------------------------- #

def _check_org_other(org: OrgEnum, org_other: str | None) -> None:
    """Enforce: org_other is set iff org=OTHER. Same rule across the
    three federation-credential models so it's worth a helper."""
    if org == OrgEnum.OTHER:
        if not org_other:
            raise ValueError("org_other must be set when org=OTHER")
    else:
        if org_other is not None:
            raise ValueError(f"org_other must be None when org={org.value}")


def _check_level_for_org(org: OrgEnum, level: str) -> None:
    """For org=CSPA / USPA, level must match the federation's closed
    enum. For org=OTHER, level is free text (already length-bounded
    by the field constraint)."""
    if org == OrgEnum.CSPA and level not in CSPACopLevel._value2member_map_:
        allowed = sorted(e.value for e in CSPACopLevel)
        raise ValueError(
            f"level={level!r} not valid for CSPA — expected one of {allowed}"
        )
    if org == OrgEnum.USPA and level not in USPACopLevel._value2member_map_:
        allowed = sorted(e.value for e in USPACopLevel)
        raise ValueError(
            f"level={level!r} not valid for USPA — expected one of {allowed}"
        )
    # OTHER: free text, no per-org enum constraint


def _check_code_for_org(org: OrgEnum, code: str) -> None:
    """Same pattern as _check_level_for_org but for FederationRating.code."""
    if org == OrgEnum.CSPA and code not in CSPARatingCode._value2member_map_:
        allowed = sorted(e.value for e in CSPARatingCode)
        raise ValueError(
            f"code={code!r} not valid for CSPA — expected one of {allowed}"
        )
    if org == OrgEnum.USPA and code not in USPARatingCode._value2member_map_:
        allowed = sorted(e.value for e in USPARatingCode)
        raise ValueError(
            f"code={code!r} not valid for USPA — expected one of {allowed}"
        )


# --------------------------------------------------------------------------- #
# JumperAttachment — required-id variant of AttachmentType
# --------------------------------------------------------------------------- #

class JumperAttachment(BaseModel):
    """One attachment under ``<jumper>/<attachments>``.

    Same on-disk shape as a jump attachment (filename / sha256 / size /
    optional content_type) but with a required `id` UUID. Credentials
    reference attachments by `id` via their `card_attachment_id` field;
    a jumper attachment without an id cannot be referenced and is
    therefore disallowed at the model layer even though the shared XSD
    AttachmentType permits id to elide for jump-side use.

    Filename is sanitized through the same D4 rules as jump attachments
    (sanitize_filename in storage/filesystem.py): NFC, no forbidden
    characters, no Windows reserved names, 1..255 bytes.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    filename: str = Field(min_length=1, max_length=255)
    sha256: str = Field(pattern=SHA256_HEX_PATTERN)
    size: int = Field(ge=0)
    content_type: str | None = None

    @field_validator("filename")
    @classmethod
    def _validate_filename(cls, v: str) -> str:
        return sanitize_filename(v)


# --------------------------------------------------------------------------- #
# Credential sub-models
# --------------------------------------------------------------------------- #

class Membership(BaseModel):
    """A federation membership card.

    `expiry_date` is user-entered from the card. CSPA runs anniversary,
    USPA runs calendar-year (Jan 1 – Dec 31), other federations vary.
    The Profile UI surfaces a 30-day-out warning by reading this field.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    org: OrgEnum
    org_other: str | None = Field(default=None, min_length=1, max_length=120)
    member_number: str = Field(min_length=1, max_length=40)
    expiry_date: _date
    card_attachment_id: UUID | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _validate_org_other(self) -> Membership:
        _check_org_other(self.org, self.org_other)
        return self


class MembershipCreate(BaseModel):
    """Request body for ``POST /api/v1/jumpers/{id}/memberships`` and
    ``PUT /api/v1/jumpers/{id}/memberships/{membership_id}`` (D47, D.1).

    Identical to :class:`Membership` minus ``id``: the server mints
    the membership UUID on POST and uses the path UUID on PUT. Letting
    callers specify the id would invite collisions and silent
    overwrites; the strict no-id contract makes the server the
    single authority for credential identity.
    """

    model_config = ConfigDict(extra="forbid")

    org: OrgEnum
    org_other: str | None = Field(default=None, min_length=1, max_length=120)
    member_number: str = Field(min_length=1, max_length=40)
    expiry_date: _date
    card_attachment_id: UUID | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _validate_org_other(self) -> MembershipCreate:
        _check_org_other(self.org, self.org_other)
        return self


class CopCreate(BaseModel):
    """Request body for ``POST/PUT /api/v1/jumpers/{id}/cops...`` (D47, D.2).

    Identical to :class:`Cop` minus ``id``: server mints on POST,
    URL path id wins on PUT.
    """

    model_config = ConfigDict(extra="forbid")

    org: OrgEnum
    org_other: str | None = Field(default=None, min_length=1, max_length=120)
    level: str = Field(min_length=1, max_length=40)
    issued_date: _date
    card_attachment_id: UUID | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _validate_cross_fields(self) -> CopCreate:
        _check_org_other(self.org, self.org_other)
        _check_level_for_org(self.org, self.level)
        return self


class Cop(BaseModel):
    """A federation Certificate of Proficiency / license.

    CoPs do not expire by date — they become null and void if currency
    lapses (USPA SIM 3-1; CSPA MOI). v0.1 records `issued_date` only;
    the calculator that decides "is your CoP current right now" is a
    future slice. The schema is shaped so that calculator can land
    additively without re-storing on-disk data.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    org: OrgEnum
    org_other: str | None = Field(default=None, min_length=1, max_length=120)
    level: str = Field(min_length=1, max_length=40)
    issued_date: _date
    card_attachment_id: UUID | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _validate_cross_fields(self) -> Cop:
        _check_org_other(self.org, self.org_other)
        _check_level_for_org(self.org, self.level)
        return self


class FederationRatingCreate(BaseModel):
    """Request body for ``POST/PUT /api/v1/jumpers/{id}/ratings...`` (D47, D.2).

    Identical to :class:`FederationRating` minus ``id``.
    """

    model_config = ConfigDict(extra="forbid")

    org: OrgEnum
    org_other: str | None = Field(default=None, min_length=1, max_length=120)
    code: str = Field(min_length=1, max_length=40)
    expiry_date: _date
    card_attachment_id: UUID | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _validate_cross_fields(self) -> FederationRatingCreate:
        _check_org_other(self.org, self.org_other)
        _check_code_for_org(self.org, self.code)
        return self


class FederationRating(BaseModel):
    """A federation-issued rating (Coach, AFFI, JM, PFFI, Rigger
    tiers, etc.).

    Tandem instructor is intentionally NOT modeled here — it's
    manufacturer-issued and lives in TandemRating (D47, the
    architectural correction of 2026-04-29).
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    org: OrgEnum
    org_other: str | None = Field(default=None, min_length=1, max_length=120)
    code: str = Field(min_length=1, max_length=40)
    expiry_date: _date
    card_attachment_id: UUID | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _validate_cross_fields(self) -> FederationRating:
        _check_org_other(self.org, self.org_other)
        _check_code_for_org(self.org, self.code)
        return self


class TandemRatingCreate(BaseModel):
    """Request body for ``POST/PUT /api/v1/jumpers/{id}/tandem-ratings...`` (D47, D.2).

    Identical to :class:`TandemRating` minus ``id``.
    """

    model_config = ConfigDict(extra="forbid")

    system: TandemSystem
    system_other: str | None = Field(default=None, min_length=1, max_length=120)
    expiry_date: _date
    card_attachment_id: UUID | None = None
    currency_reset_at: _date | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _validate_system_other(self) -> TandemRatingCreate:
        if self.system == TandemSystem.OTHER:
            if not self.system_other:
                raise ValueError("system_other must be set when system=OTHER")
        else:
            if self.system_other is not None:
                raise ValueError(
                    f"system_other must be None when system={self.system.value}"
                )
        return self


class TandemRating(BaseModel):
    """A manufacturer-issued tandem instructor rating, scoped to a rig
    system.

    `currency_reset_at` is the manual "I declare I am current after a
    supervised re-currency jump" override. When set within the system's
    currency window (UPT 90 d / Strong 12 mo), the calculator
    suppresses the not-current warning regardless of recent jump
    activity. The reset is the only piece of currency state stored in
    XML; everything else (counts in last 90 d / 365 d) is derived from
    the jump index per D3.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    system: TandemSystem
    system_other: str | None = Field(default=None, min_length=1, max_length=120)
    expiry_date: _date
    card_attachment_id: UUID | None = None
    currency_reset_at: _date | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _validate_system_other(self) -> TandemRating:
        if self.system == TandemSystem.OTHER:
            if not self.system_other:
                raise ValueError("system_other must be set when system=OTHER")
        else:
            if self.system_other is not None:
                raise ValueError(
                    f"system_other must be None when system={self.system.value}"
                )
        return self


class MedicalCreate(BaseModel):
    """Request body for ``POST/PUT /api/v1/jumpers/{id}/medicals...`` (D47, D.2).

    Identical to :class:`Medical` minus ``id``.
    """

    model_config = ConfigDict(extra="forbid")

    kind: MedicalKind
    issuing_authority: str = Field(min_length=1, max_length=120)
    expiry_date: _date
    card_attachment_id: UUID | None = None
    notes: str | None = None


class Medical(BaseModel):
    """A government-issued aviation medical certificate.

    `issuing_authority` is free text — the user types the agency name
    from the card (Transport Canada, FAA, CAA NZ, etc.). The kind enum
    just controls which warning copy the UI surfaces; the calendar
    comes from `expiry_date`.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    kind: MedicalKind
    issuing_authority: str = Field(min_length=1, max_length=120)
    expiry_date: _date
    card_attachment_id: UUID | None = None
    notes: str | None = None


# --------------------------------------------------------------------------- #
# Jumper — the top-level record
# --------------------------------------------------------------------------- #

class Jumper(BaseModel):
    """Canonical jumper shape — serialized to ``jumpers/<id>/jumper.xml``."""

    model_config = ConfigDict(extra="forbid")

    # Stable identifier. Never changes; the folder is named after the
    # id so even renaming via the UI never moves the file.
    id: UUID = Field(default_factory=uuid4)

    # Optional display name. Free text, 1..120 chars when set.
    name: str | None = Field(default=None, min_length=1, max_length=120)

    # Exit weight in pounds. Required: the wear math and wingloading
    # both consume it. Strict greater-than-zero matches the XSD.
    exit_weight_lb: float = Field(gt=0)

    # Calendar date the exit weight was last confirmed by the user.
    # Drives the 365-day staleness prompt per D33.
    exit_weight_updated_at: _date | None = None

    # D47 credential collections. Each defaults to an empty list so a
    # freshly-created Jumper has no credentials and the matching XSD
    # collection wrappers elide on serialize.
    memberships: list[Membership] = []
    cops: list[Cop] = []
    ratings: list[FederationRating] = []
    tandem_ratings: list[TandemRating] = []
    medicals: list[Medical] = []
    attachments: list[JumperAttachment] = []

    # D32 audit timestamps in canonical UTC ms form. Authored only
    # by the service layer; optional on the model.
    created_at: str | None = None
    updated_at: str | None = None


class JumperCreate(BaseModel):
    """Request body for ``POST /api/v1/jumpers`` (R.2.0b+).

    Identity-only. Credential management lands in dedicated REST
    endpoints per D47 (Phase D); this body stays as it was so the
    existing onboarding flow is unchanged.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    exit_weight_lb: float = Field(gt=0)
    exit_weight_updated_at: _date | None = None


class JumperUpdate(BaseModel):
    """Request body for ``PUT /api/v1/jumpers/{id}`` (R.2.0c+).

    Identity-only full-replace, same as JumperCreate. Credential
    edits route through their own POST / PUT / DELETE endpoints (D47,
    Phase D) — the existing PUT does not accept credential collections
    so each gets its own validation surface and hint channel (D24).
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    exit_weight_lb: float = Field(gt=0)
    exit_weight_updated_at: _date | None = None
