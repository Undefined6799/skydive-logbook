"""Phase B.2 — D47 credential complex types: validation tests.

The five new D47 complex types (MembershipType, CopType,
FederationRatingType, TandemRatingType, MedicalType) plus the extended
AttachmentType are not yet referenced by any production element — the
JumperContent wiring lands in B.3. To validate them now without
polluting SCHEMA.v1.xsd with test-only top-level elements, this module
loads the production schema, dynamically appends wrapper elements
(`<test_membership>`, `<test_cop>`, …) typed against each new complex
type, and validates fragments against the modified schema.

Test surface:

  * structural — each new complex type is present in the schema;
  * positive — minimal-required-field fragment validates for each type;
  * enum coverage — every value in every B.1 enum (OrgEnum,
    CSPACopLevel, USPACopLevel, CSPARatingCode, USPARatingCode,
    TandemSystem, MedicalKind) validates when used in the appropriate
    field of the appropriate complex type;
  * negative — typo'd enum value, missing required field, and out-of-
    range string length each fail validation;
  * backward compat — AttachmentType still validates without `id`
    (existing pre-D47 jump.xml files keep working).

The XSD-level cross-field constraint (e.g. "if org=CSPA then code
must be a CSPARatingCode value") is NOT enforced here — XSD 1.0 has
no xs:assert. That's a Pydantic concern landing in B.4.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from backend.tests.test_xsd_credential_enums import EXPECTED_ENUMS

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "xml" / "schema" / "SCHEMA.v1.xsd"
NS = "https://skydive-logbook.org/schema/v1"
XS_NS = "http://www.w3.org/2001/XMLSchema"

# Complex types this slice introduces, plus the AttachmentType
# extension. Each is mapped to a test-wrapper element name we append
# to the production schema at load time.
TYPE_WRAPPERS = {
    "MembershipType": "test_membership",
    "CopType": "test_cop",
    "FederationRatingType": "test_rating",
    "TandemRatingType": "test_tandem",
    "MedicalType": "test_medical",
    "AttachmentType": "test_attachment",
}


@pytest.fixture(scope="module")
def schema_root() -> etree._Element:
    return etree.parse(str(SCHEMA_PATH)).getroot()


@pytest.fixture(scope="module")
def test_schema() -> etree.XMLSchema:
    """Production schema + dynamically added wrapper elements."""
    doc = etree.parse(str(SCHEMA_PATH))
    root = doc.getroot()
    for type_name, wrapper_name in TYPE_WRAPPERS.items():
        wrapper = etree.SubElement(root, f"{{{XS_NS}}}element")
        wrapper.set("name", wrapper_name)
        wrapper.set("type", type_name)
    return etree.XMLSchema(doc)


def _make_fragment(wrapper_name: str, body: str) -> etree._Element:
    """Wrap a body in the given test-wrapper element with the project namespace."""
    return etree.fromstring(
        f'<{wrapper_name} xmlns="{NS}">{body}</{wrapper_name}>'.encode()
    )


def _validates(test_schema: etree.XMLSchema, wrapper_name: str, body: str) -> bool:
    return test_schema.validate(_make_fragment(wrapper_name, body))


# A reusable UUIDv4 string that satisfies the schema's UUID pattern.
SAMPLE_UUID = "11111111-1111-4111-8111-111111111111"
SAMPLE_UUID_2 = "22222222-2222-4222-8222-222222222222"
SAMPLE_SHA256 = "a" * 64


# --------------------------------------------------------------------- #
# Structural assertions
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("type_name", sorted(TYPE_WRAPPERS.keys()))
def test_complex_type_is_defined(schema_root: etree._Element, type_name: str) -> None:
    el = schema_root.find(f"{{{XS_NS}}}complexType[@name='{type_name}']")
    assert el is not None, (
        f"{type_name} not found in SCHEMA.v1.xsd — D47 / Phase B.2 added it."
    )


def test_attachment_type_id_is_optional(schema_root: etree._Element) -> None:
    """The new `id` field on AttachmentType is optional — backward compat (D18)."""
    el = schema_root.find(f"{{{XS_NS}}}complexType[@name='AttachmentType']")
    assert el is not None
    seq = el.find(f"{{{XS_NS}}}sequence")
    assert seq is not None
    id_el = seq.find(f"{{{XS_NS}}}element[@name='id']")
    assert id_el is not None, "AttachmentType.id was added by D47 / B.2"
    assert id_el.get("minOccurs") == "0", "AttachmentType.id must be optional"


def test_attachment_type_id_at_end_of_sequence(schema_root: etree._Element) -> None:
    """`id` must be appended last so older XML round-trips per D18."""
    el = schema_root.find(f"{{{XS_NS}}}complexType[@name='AttachmentType']")
    assert el is not None
    seq = el.find(f"{{{XS_NS}}}sequence")
    assert seq is not None
    field_names = [child.get("name") for child in seq.findall(f"{{{XS_NS}}}element")]
    assert field_names[-1] == "id", (
        f"AttachmentType.id must be the last field for additive compat. "
        f"Current order: {field_names}"
    )


# --------------------------------------------------------------------- #
# Positive validation — minimal valid instances
# --------------------------------------------------------------------- #

class TestMembershipValidation:
    def test_minimal_valid_membership(self, test_schema: etree.XMLSchema) -> None:
        body = (
            f"<id>{SAMPLE_UUID}</id>"
            f"<org>CSPA</org>"
            f"<member_number>12345</member_number>"
            f"<expiry_date>2027-04-29</expiry_date>"
        )
        assert _validates(test_schema, "test_membership", body)

    def test_membership_with_all_optionals(self, test_schema: etree.XMLSchema) -> None:
        body = (
            f"<id>{SAMPLE_UUID}</id>"
            f"<org>OTHER</org>"
            f"<org_other>British Parachute Association</org_other>"
            f"<member_number>BPA-9999</member_number>"
            f"<expiry_date>2027-04-29</expiry_date>"
            f"<card_attachment_id>{SAMPLE_UUID_2}</card_attachment_id>"
            f"<notes>Renewed at convention.</notes>"
        )
        assert _validates(test_schema, "test_membership", body)

    @pytest.mark.parametrize("org_value", EXPECTED_ENUMS["OrgEnum"])
    def test_every_org_enum_value_validates(
        self, test_schema: etree.XMLSchema, org_value: str
    ) -> None:
        body = (
            f"<id>{SAMPLE_UUID}</id>"
            f"<org>{org_value}</org>"
            f"<member_number>X</member_number>"
            f"<expiry_date>2027-04-29</expiry_date>"
        )
        assert _validates(test_schema, "test_membership", body), (
            f"OrgEnum value {org_value!r} should pass XSD validation"
        )

    def test_membership_rejects_unknown_org(self, test_schema: etree.XMLSchema) -> None:
        body = (
            f"<id>{SAMPLE_UUID}</id>"
            f"<org>FAA</org>"  # FAA isn't a sport-jumping federation
            f"<member_number>X</member_number>"
            f"<expiry_date>2027-04-29</expiry_date>"
        )
        assert not _validates(test_schema, "test_membership", body)

    def test_membership_rejects_missing_member_number(
        self, test_schema: etree.XMLSchema
    ) -> None:
        body = (
            f"<id>{SAMPLE_UUID}</id>"
            f"<org>CSPA</org>"
            f"<expiry_date>2027-04-29</expiry_date>"
        )
        assert not _validates(test_schema, "test_membership", body)

    def test_membership_rejects_member_number_too_long(
        self, test_schema: etree.XMLSchema
    ) -> None:
        body = (
            f"<id>{SAMPLE_UUID}</id>"
            f"<org>CSPA</org>"
            f"<member_number>{'X' * 41}</member_number>"
            f"<expiry_date>2027-04-29</expiry_date>"
        )
        assert not _validates(test_schema, "test_membership", body)


class TestCopValidation:
    @pytest.mark.parametrize("level", EXPECTED_ENUMS["CSPACopLevel"])
    def test_every_cspa_cop_level_validates(
        self, test_schema: etree.XMLSchema, level: str
    ) -> None:
        body = (
            f"<id>{SAMPLE_UUID}</id>"
            f"<org>CSPA</org>"
            f"<level>{level}</level>"
            f"<issued_date>2024-06-15</issued_date>"
        )
        assert _validates(test_schema, "test_cop", body), (
            f"CSPACopLevel value {level!r} should pass XSD validation"
        )

    @pytest.mark.parametrize("level", EXPECTED_ENUMS["USPACopLevel"])
    def test_every_uspa_license_level_validates(
        self, test_schema: etree.XMLSchema, level: str
    ) -> None:
        body = (
            f"<id>{SAMPLE_UUID}</id>"
            f"<org>USPA</org>"
            f"<level>{level}</level>"
            f"<issued_date>2024-06-15</issued_date>"
        )
        assert _validates(test_schema, "test_cop", body)

    def test_cop_other_org_with_arbitrary_level(
        self, test_schema: etree.XMLSchema
    ) -> None:
        # XSD permits any non-empty string up to 40 chars in `level`;
        # the per-org strict check lives in Pydantic (B.4).
        body = (
            f"<id>{SAMPLE_UUID}</id>"
            f"<org>OTHER</org>"
            f"<org_other>British Parachute Association</org_other>"
            f"<level>cat-a</level>"  # not a CSPA/USPA value, but valid for OTHER
            f"<issued_date>2024-06-15</issued_date>"
        )
        assert _validates(test_schema, "test_cop", body)

    def test_cop_rejects_empty_level(self, test_schema: etree.XMLSchema) -> None:
        body = (
            f"<id>{SAMPLE_UUID}</id>"
            f"<org>CSPA</org>"
            f"<level></level>"  # empty string violates minLength=1
            f"<issued_date>2024-06-15</issued_date>"
        )
        assert not _validates(test_schema, "test_cop", body)


class TestFederationRatingValidation:
    @pytest.mark.parametrize("code", EXPECTED_ENUMS["CSPARatingCode"])
    def test_every_cspa_rating_code_validates(
        self, test_schema: etree.XMLSchema, code: str
    ) -> None:
        body = (
            f"<id>{SAMPLE_UUID}</id>"
            f"<org>CSPA</org>"
            f"<code>{code}</code>"
            f"<expiry_date>2027-04-29</expiry_date>"
        )
        assert _validates(test_schema, "test_rating", body)

    @pytest.mark.parametrize("code", EXPECTED_ENUMS["USPARatingCode"])
    def test_every_uspa_rating_code_validates(
        self, test_schema: etree.XMLSchema, code: str
    ) -> None:
        body = (
            f"<id>{SAMPLE_UUID}</id>"
            f"<org>USPA</org>"
            f"<code>{code}</code>"
            f"<expiry_date>2027-04-29</expiry_date>"
        )
        assert _validates(test_schema, "test_rating", body)

    def test_rating_with_attachment_reference(self, test_schema: etree.XMLSchema) -> None:
        body = (
            f"<id>{SAMPLE_UUID}</id>"
            f"<org>CSPA</org>"
            f"<code>pffi</code>"
            f"<expiry_date>2027-04-29</expiry_date>"
            f"<card_attachment_id>{SAMPLE_UUID_2}</card_attachment_id>"
        )
        assert _validates(test_schema, "test_rating", body)

    def test_rating_rejects_missing_code(self, test_schema: etree.XMLSchema) -> None:
        body = (
            f"<id>{SAMPLE_UUID}</id>"
            f"<org>CSPA</org>"
            f"<expiry_date>2027-04-29</expiry_date>"
        )
        assert not _validates(test_schema, "test_rating", body)


class TestTandemRatingValidation:
    @pytest.mark.parametrize("system", EXPECTED_ENUMS["TandemSystem"])
    def test_every_tandem_system_validates(
        self, test_schema: etree.XMLSchema, system: str
    ) -> None:
        body = (
            f"<id>{SAMPLE_UUID}</id>"
            f"<system>{system}</system>"
            f"<expiry_date>2027-04-29</expiry_date>"
        )
        assert _validates(test_schema, "test_tandem", body)

    def test_tandem_with_currency_reset(self, test_schema: etree.XMLSchema) -> None:
        body = (
            f"<id>{SAMPLE_UUID}</id>"
            f"<system>upt_sigma</system>"
            f"<expiry_date>2027-04-29</expiry_date>"
            f"<card_attachment_id>{SAMPLE_UUID_2}</card_attachment_id>"
            f"<currency_reset_at>2026-04-15</currency_reset_at>"
            f"<notes>Recurrency jump with examiner.</notes>"
        )
        assert _validates(test_schema, "test_tandem", body)

    def test_tandem_other_with_system_other(self, test_schema: etree.XMLSchema) -> None:
        body = (
            f"<id>{SAMPLE_UUID}</id>"
            f"<system>other</system>"
            f"<system_other>JumpShack Racer Tandem</system_other>"
            f"<expiry_date>2027-04-29</expiry_date>"
        )
        assert _validates(test_schema, "test_tandem", body)

    def test_tandem_rejects_unknown_system(self, test_schema: etree.XMLSchema) -> None:
        body = (
            f"<id>{SAMPLE_UUID}</id>"
            f"<system>parachutes_de_france</system>"  # not in TandemSystem
            f"<expiry_date>2027-04-29</expiry_date>"
        )
        assert not _validates(test_schema, "test_tandem", body)

    def test_tandem_rejects_missing_system(self, test_schema: etree.XMLSchema) -> None:
        body = (
            f"<id>{SAMPLE_UUID}</id>"
            f"<expiry_date>2027-04-29</expiry_date>"
        )
        assert not _validates(test_schema, "test_tandem", body)


class TestMedicalValidation:
    @pytest.mark.parametrize("kind", EXPECTED_ENUMS["MedicalKind"])
    def test_every_medical_kind_validates(
        self, test_schema: etree.XMLSchema, kind: str
    ) -> None:
        body = (
            f"<id>{SAMPLE_UUID}</id>"
            f"<kind>{kind}</kind>"
            f"<issuing_authority>Transport Canada</issuing_authority>"
            f"<expiry_date>2027-12-31</expiry_date>"
        )
        assert _validates(test_schema, "test_medical", body)

    def test_medical_rejects_unknown_kind(self, test_schema: etree.XMLSchema) -> None:
        # class_i / class_ii are real medicals but not in MedicalKind v0.1
        # (D47, "Out of scope"). Schema must reject them until they're added.
        body = (
            f"<id>{SAMPLE_UUID}</id>"
            f"<kind>class_i</kind>"
            f"<issuing_authority>FAA</issuing_authority>"
            f"<expiry_date>2027-12-31</expiry_date>"
        )
        assert not _validates(test_schema, "test_medical", body)

    def test_medical_rejects_missing_issuing_authority(
        self, test_schema: etree.XMLSchema
    ) -> None:
        body = (
            f"<id>{SAMPLE_UUID}</id>"
            f"<kind>class_iii</kind>"
            f"<expiry_date>2027-12-31</expiry_date>"
        )
        assert not _validates(test_schema, "test_medical", body)


class TestAttachmentBackwardCompat:
    """Pre-D47 jump.xml attachments lacked an `id` field. They must still validate."""

    def test_attachment_without_id_validates(self, test_schema: etree.XMLSchema) -> None:
        body = (
            f"<filename>flysight.csv</filename>"
            f"<sha256>{SAMPLE_SHA256}</sha256>"
            f"<size>4096</size>"
        )
        assert _validates(test_schema, "test_attachment", body)

    def test_attachment_with_id_validates(self, test_schema: etree.XMLSchema) -> None:
        body = (
            f"<filename>cspa-card-2026.pdf</filename>"
            f"<sha256>{SAMPLE_SHA256}</sha256>"
            f"<size>123456</size>"
            f"<content_type>application/pdf</content_type>"
            f"<id>{SAMPLE_UUID}</id>"
        )
        assert _validates(test_schema, "test_attachment", body)

    def test_attachment_with_invalid_id_fails(self, test_schema: etree.XMLSchema) -> None:
        body = (
            f"<filename>card.pdf</filename>"
            f"<sha256>{SAMPLE_SHA256}</sha256>"
            f"<size>10</size>"
            f"<id>not-a-uuid</id>"
        )
        assert not _validates(test_schema, "test_attachment", body)
