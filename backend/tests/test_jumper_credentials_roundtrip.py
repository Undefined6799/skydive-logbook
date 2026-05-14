"""Phase B.4 — D47 Pydantic ↔ XML round-trip + cross-field validation.

The contract this slice locks in:

    Jumper → jumper_to_bytes → parse → element_to_jumper → Jumper
        equals the original Jumper for any valid input.

    Jump → jump_to_bytes → parse → element_to_jump → Jump  (with is_tandem)
        equals the original.

XSD validation runs on every serialized output so a regression in the
serializer that produces invalid XML fails loudly. Cross-field rules
that XSD 1.0 cannot express (org-OTHER ↔ org_other; per-org level /
code enum match; system-OTHER ↔ system_other) are exercised through
Pydantic-side ValueError assertions — the model_validators in
backend/models/jumper.py are the single enforcement point.

Byte-for-byte stability is asserted on the canonical writer output:
serialize → parse → serialize must produce the exact same bytes (no
spurious whitespace, no field-order drift).
"""
from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from backend.models.jump import Jump
from backend.models.jumper import (
    Cop,
    CSPACopLevel,
    CSPARatingCode,
    FederationRating,
    Jumper,
    JumperAttachment,
    Medical,
    MedicalKind,
    Membership,
    OrgEnum,
    TandemRating,
    TandemSystem,
    USPACopLevel,
    USPARatingCode,
)
from backend.xml.serialize import (
    element_to_jump,
    element_to_jumper,
    jump_to_bytes,
    jumper_to_bytes,
)
from backend.xml.validator import parse, validate

VALID_SHA256 = "a" * 64


def _roundtrip_jumper(j: Jumper) -> Jumper:
    """serialize → XSD-validate → parse → deserialize."""
    raw = jumper_to_bytes(j)
    element = parse(raw)
    validate(element)  # Picks the schema by namespace.
    return element_to_jumper(element)


def _roundtrip_jump(j: Jump) -> Jump:
    raw = jump_to_bytes(j)
    element = parse(raw)
    validate(element)
    return element_to_jump(element)


# --------------------------------------------------------------------- #
# Empty / minimal jumper — backward compat
# --------------------------------------------------------------------- #

class TestJumperBackwardCompat:
    def test_empty_jumper_roundtrips(self) -> None:
        original = Jumper(exit_weight_lb=180)
        restored = _roundtrip_jumper(original)
        assert restored == original
        assert restored.memberships == []
        assert restored.cops == []
        assert restored.ratings == []
        assert restored.tandem_ratings == []
        assert restored.medicals == []
        assert restored.attachments == []

    def test_pre_d47_jumper_roundtrips(self) -> None:
        original = Jumper(
            id=UUID("11111111-1111-4111-8111-111111111111"),
            name="Alex",
            exit_weight_lb=180.5,
            exit_weight_updated_at=date(2026, 1, 15),
            created_at="2026-01-15T12:00:00.000Z",
            updated_at="2026-04-01T09:00:00.000Z",
        )
        restored = _roundtrip_jumper(original)
        assert restored == original


# --------------------------------------------------------------------- #
# Per-collection round-trips
# --------------------------------------------------------------------- #

class TestPerCollectionRoundtrip:
    def test_membership_roundtrips(self) -> None:
        original = Jumper(
            exit_weight_lb=180,
            memberships=[
                Membership(
                    org=OrgEnum.CSPA,
                    member_number="12345",
                    expiry_date=date(2027, 4, 29),
                ),
            ],
        )
        restored = _roundtrip_jumper(original)
        assert restored == original

    def test_membership_with_other_org_roundtrips(self) -> None:
        original = Jumper(
            exit_weight_lb=180,
            memberships=[
                Membership(
                    org=OrgEnum.OTHER,
                    org_other="British Parachute Association",
                    member_number="BPA-9999",
                    expiry_date=date(2027, 4, 29),
                ),
            ],
        )
        restored = _roundtrip_jumper(original)
        assert restored == original

    def test_cop_roundtrips(self) -> None:
        original = Jumper(
            exit_weight_lb=180,
            cops=[
                Cop(org=OrgEnum.CSPA, level="d", issued_date=date(2024, 6, 15)),
                Cop(org=OrgEnum.USPA, level="d", issued_date=date(2024, 9, 20)),
            ],
        )
        restored = _roundtrip_jumper(original)
        assert restored == original

    def test_federation_rating_roundtrips(self) -> None:
        original = Jumper(
            exit_weight_lb=180,
            ratings=[
                FederationRating(
                    org=OrgEnum.CSPA, code="pffi", expiry_date=date(2027, 3, 31)
                ),
                FederationRating(
                    org=OrgEnum.USPA, code="affi", expiry_date=date(2026, 12, 31)
                ),
            ],
        )
        restored = _roundtrip_jumper(original)
        assert restored == original

    def test_tandem_rating_roundtrips(self) -> None:
        original = Jumper(
            exit_weight_lb=180,
            tandem_ratings=[
                TandemRating(
                    system=TandemSystem.UPT_SIGMA,
                    expiry_date=date(2027, 4, 29),
                    currency_reset_at=date(2026, 4, 15),
                    notes="Recurrency jump with examiner.",
                ),
            ],
        )
        restored = _roundtrip_jumper(original)
        assert restored == original

    def test_medical_roundtrips(self) -> None:
        original = Jumper(
            exit_weight_lb=180,
            medicals=[
                Medical(
                    kind=MedicalKind.CLASS_III,
                    issuing_authority="Transport Canada",
                    expiry_date=date(2028, 6, 15),
                ),
            ],
        )
        restored = _roundtrip_jumper(original)
        assert restored == original

    def test_attachment_roundtrips(self) -> None:
        att_id = uuid4()
        original = Jumper(
            exit_weight_lb=180,
            attachments=[
                JumperAttachment(
                    id=att_id,
                    filename="cspa-card-2026.pdf",
                    sha256=VALID_SHA256,
                    size=234567,
                    content_type="application/pdf",
                ),
            ],
        )
        restored = _roundtrip_jumper(original)
        assert restored == original


# --------------------------------------------------------------------- #
# Maximally populated round-trip + cross-reference
# --------------------------------------------------------------------- #

class TestMaxPopulatedRoundtrip:
    def test_max_populated_jumper_roundtrips(self) -> None:
        cspa_card_id = uuid4()
        uspa_card_id = uuid4()
        medical_card_id = uuid4()
        original = Jumper(
            id=UUID("22222222-2222-4222-8222-222222222222"),
            name="Alex",
            exit_weight_lb=180.5,
            exit_weight_updated_at=date(2026, 1, 15),
            memberships=[
                Membership(
                    org=OrgEnum.CSPA,
                    member_number="12345",
                    expiry_date=date(2027, 4, 29),
                    card_attachment_id=cspa_card_id,
                ),
                Membership(
                    org=OrgEnum.USPA,
                    member_number="987654",
                    expiry_date=date(2026, 12, 31),
                    card_attachment_id=uspa_card_id,
                ),
            ],
            cops=[
                Cop(org=OrgEnum.CSPA, level="d", issued_date=date(2024, 6, 15)),
                Cop(org=OrgEnum.USPA, level="d", issued_date=date(2024, 9, 20)),
            ],
            ratings=[
                FederationRating(
                    org=OrgEnum.CSPA,
                    code="pffi",
                    expiry_date=date(2027, 3, 31),
                ),
                FederationRating(
                    org=OrgEnum.USPA,
                    code="affi",
                    expiry_date=date(2026, 12, 31),
                ),
            ],
            tandem_ratings=[
                TandemRating(
                    system=TandemSystem.UPT_SIGMA,
                    expiry_date=date(2027, 4, 29),
                    currency_reset_at=date(2026, 4, 15),
                ),
                TandemRating(
                    system=TandemSystem.UPT_VECTOR,
                    expiry_date=date(2027, 4, 29),
                ),
            ],
            medicals=[
                Medical(
                    kind=MedicalKind.CLASS_III,
                    issuing_authority="Transport Canada",
                    expiry_date=date(2028, 6, 15),
                    card_attachment_id=medical_card_id,
                ),
            ],
            attachments=[
                JumperAttachment(
                    id=cspa_card_id,
                    filename="cspa-card-2026.pdf",
                    sha256=VALID_SHA256,
                    size=234567,
                    content_type="application/pdf",
                ),
                JumperAttachment(
                    id=uspa_card_id,
                    filename="uspa-card-2026.pdf",
                    sha256="b" * 64,
                    size=198432,
                    content_type="application/pdf",
                ),
                JumperAttachment(
                    id=medical_card_id,
                    filename="class-iii-medical.pdf",
                    sha256="c" * 64,
                    size=87654,
                    content_type="application/pdf",
                ),
            ],
        )
        restored = _roundtrip_jumper(original)
        assert restored == original


# --------------------------------------------------------------------- #
# Byte-for-byte stability
# --------------------------------------------------------------------- #

class TestByteStability:
    """serialize → parse → serialize must be byte-for-byte identical."""

    def test_minimal_jumper_byte_stable(self) -> None:
        j = Jumper(
            id=UUID("11111111-1111-4111-8111-111111111111"),
            exit_weight_lb=180,
        )
        bytes_a = jumper_to_bytes(j)
        bytes_b = jumper_to_bytes(element_to_jumper(parse(bytes_a)))
        assert bytes_a == bytes_b

    def test_max_populated_jumper_byte_stable(self) -> None:
        att_id = UUID("33333333-3333-4333-8333-333333333333")
        j = Jumper(
            id=UUID("22222222-2222-4222-8222-222222222222"),
            name="Alex",
            exit_weight_lb=180.5,
            exit_weight_updated_at=date(2026, 1, 15),
            memberships=[
                Membership(
                    id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
                    org=OrgEnum.CSPA,
                    member_number="12345",
                    expiry_date=date(2027, 4, 29),
                    card_attachment_id=att_id,
                ),
            ],
            tandem_ratings=[
                TandemRating(
                    id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
                    system=TandemSystem.UPT_SIGMA,
                    expiry_date=date(2027, 4, 29),
                    currency_reset_at=date(2026, 4, 15),
                ),
            ],
            attachments=[
                JumperAttachment(
                    id=att_id,
                    filename="cspa.pdf",
                    sha256=VALID_SHA256,
                    size=1024,
                    content_type="application/pdf",
                ),
            ],
        )
        bytes_a = jumper_to_bytes(j)
        bytes_b = jumper_to_bytes(element_to_jumper(parse(bytes_a)))
        assert bytes_a == bytes_b


# --------------------------------------------------------------------- #
# Enum coverage — every B.1 value round-trips through Pydantic
# --------------------------------------------------------------------- #

class TestEnumCoverage:
    """Every enum value must round-trip through Pydantic. A regression
    that drops a value from a closed enum, or a typo that breaks parse
    on a specific token, surfaces here as a single failing parametrized
    case rather than a hidden silent loss."""

    @pytest.mark.parametrize("level", [e.value for e in CSPACopLevel])
    def test_every_cspa_cop_level(self, level: str) -> None:
        j = Jumper(
            exit_weight_lb=180,
            cops=[Cop(org=OrgEnum.CSPA, level=level, issued_date=date(2024, 6, 15))],
        )
        restored = _roundtrip_jumper(j)
        assert restored.cops[0].level == level

    @pytest.mark.parametrize("level", [e.value for e in USPACopLevel])
    def test_every_uspa_license_level(self, level: str) -> None:
        j = Jumper(
            exit_weight_lb=180,
            cops=[Cop(org=OrgEnum.USPA, level=level, issued_date=date(2024, 6, 15))],
        )
        restored = _roundtrip_jumper(j)
        assert restored.cops[0].level == level

    @pytest.mark.parametrize("code", [e.value for e in CSPARatingCode])
    def test_every_cspa_rating_code(self, code: str) -> None:
        j = Jumper(
            exit_weight_lb=180,
            ratings=[
                FederationRating(
                    org=OrgEnum.CSPA, code=code, expiry_date=date(2027, 4, 29)
                ),
            ],
        )
        restored = _roundtrip_jumper(j)
        assert restored.ratings[0].code == code

    @pytest.mark.parametrize("code", [e.value for e in USPARatingCode])
    def test_every_uspa_rating_code(self, code: str) -> None:
        j = Jumper(
            exit_weight_lb=180,
            ratings=[
                FederationRating(
                    org=OrgEnum.USPA, code=code, expiry_date=date(2027, 4, 29)
                ),
            ],
        )
        restored = _roundtrip_jumper(j)
        assert restored.ratings[0].code == code

    @pytest.mark.parametrize("system", [e.value for e in TandemSystem])
    def test_every_tandem_system(self, system: str) -> None:
        ts = TandemSystem(system)
        if ts == TandemSystem.OTHER:
            t = TandemRating(
                system=ts,
                system_other="JumpShack Racer",
                expiry_date=date(2027, 4, 29),
            )
        else:
            t = TandemRating(system=ts, expiry_date=date(2027, 4, 29))
        j = Jumper(exit_weight_lb=180, tandem_ratings=[t])
        restored = _roundtrip_jumper(j)
        assert restored.tandem_ratings[0].system == ts

    @pytest.mark.parametrize("kind", [e.value for e in MedicalKind])
    def test_every_medical_kind(self, kind: str) -> None:
        j = Jumper(
            exit_weight_lb=180,
            medicals=[
                Medical(
                    kind=MedicalKind(kind),
                    issuing_authority="Transport Canada",
                    expiry_date=date(2028, 6, 15),
                ),
            ],
        )
        restored = _roundtrip_jumper(j)
        assert restored.medicals[0].kind == MedicalKind(kind)


# --------------------------------------------------------------------- #
# Cross-field validation — XSD 1.0 can't express these
# --------------------------------------------------------------------- #

class TestCrossFieldValidation:
    """The Pydantic model_validators are the single enforcement point
    for the rules XSD 1.0 cannot express."""

    def test_membership_cspa_with_org_other_rejected(self) -> None:
        with pytest.raises(ValidationError, match="org_other must be None"):
            Membership(
                org=OrgEnum.CSPA,
                org_other="Should not be set",
                member_number="123",
                expiry_date=date(2027, 4, 29),
            )

    def test_membership_other_without_org_other_rejected(self) -> None:
        with pytest.raises(ValidationError, match="org_other must be set"):
            Membership(
                org=OrgEnum.OTHER,
                member_number="X",
                expiry_date=date(2027, 4, 29),
            )

    def test_cop_cspa_with_invalid_level_rejected(self) -> None:
        # 'banana' is not in CSPACopLevel.
        with pytest.raises(ValidationError, match="not valid for CSPA"):
            Cop(
                org=OrgEnum.CSPA,
                level="banana",
                issued_date=date(2024, 6, 15),
            )

    def test_cop_uspa_with_solo_rejected(self) -> None:
        # 'solo' is a CSPA level, not a USPA level. XSD passes (string),
        # Pydantic catches the per-org mismatch.
        with pytest.raises(ValidationError, match="not valid for USPA"):
            Cop(
                org=OrgEnum.USPA,
                level="solo",
                issued_date=date(2024, 6, 15),
            )

    def test_cop_other_accepts_arbitrary_level(self) -> None:
        # When org=OTHER, level is free text. No enum constraint.
        c = Cop(
            org=OrgEnum.OTHER,
            org_other="British Parachute Association",
            level="cat-a",
            issued_date=date(2024, 6, 15),
        )
        assert c.level == "cat-a"

    def test_rating_cspa_with_uspa_code_rejected(self) -> None:
        # 'affi' is a USPA rating code, not a CSPA one.
        with pytest.raises(ValidationError, match="not valid for CSPA"):
            FederationRating(
                org=OrgEnum.CSPA,
                code="affi",
                expiry_date=date(2027, 4, 29),
            )

    def test_rating_uspa_with_cspa_code_rejected(self) -> None:
        # 'pffi' is a CSPA code (Progressive Free Fall Instructor),
        # not a USPA one.
        with pytest.raises(ValidationError, match="not valid for USPA"):
            FederationRating(
                org=OrgEnum.USPA,
                code="pffi",
                expiry_date=date(2027, 4, 29),
            )

    def test_tandem_other_without_system_other_rejected(self) -> None:
        with pytest.raises(ValidationError, match="system_other must be set"):
            TandemRating(
                system=TandemSystem.OTHER,
                expiry_date=date(2027, 4, 29),
            )

    def test_tandem_upt_sigma_with_system_other_rejected(self) -> None:
        with pytest.raises(ValidationError, match="system_other must be None"):
            TandemRating(
                system=TandemSystem.UPT_SIGMA,
                system_other="Shouldn't be set for a known system",
                expiry_date=date(2027, 4, 29),
            )


# --------------------------------------------------------------------- #
# Jump.is_tandem round-trip
# --------------------------------------------------------------------- #

class TestJumpIsTandem:
    def _base_jump(self, **overrides) -> Jump:
        defaults = dict(
            jump_number=1,
            date=date(2026, 4, 29),
            dropzone="Skydive Test",
            exit_altitude_m=4000,
            deployment_altitude_m=900,
        )
        defaults.update(overrides)
        return Jump(**defaults)

    def test_jump_without_is_tandem_roundtrips(self) -> None:
        # Backward compat: every existing Jump in the codebase / on
        # disk has is_tandem=None on the model. Round-trip preserves
        # that as absent in XML and None on the restored model.
        original = self._base_jump()
        assert original.is_tandem is None
        restored = _roundtrip_jump(original)
        assert restored.is_tandem is None
        assert restored == original

    def test_jump_with_is_tandem_true_roundtrips(self) -> None:
        original = self._base_jump(is_tandem=True)
        restored = _roundtrip_jump(original)
        assert restored.is_tandem is True
        assert restored == original

    def test_jump_with_is_tandem_false_roundtrips(self) -> None:
        # Explicit False round-trips as <is_tandem>false</is_tandem>;
        # parsed back as False, distinct from None.
        original = self._base_jump(is_tandem=False)
        restored = _roundtrip_jump(original)
        assert restored.is_tandem is False
        assert restored == original
