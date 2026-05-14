"""Phase B.3 — D47 credential collections wired into JumperContent + is_tandem on jump.

This slice extends the production schema in two places:

  * `JumperContent` gains six optional sibling collections — memberships,
    cops, ratings, tandem_ratings, medicals, attachments — between
    `exit_weight_updated_at` and `created_at`. The new shape is consumed
    by the top-level <jumper> element AND by <rig_snapshot>/<jumper>
    (D36 reuses JumperContent). Per the D36 pattern, the snapshot
    writer leaves the credential collections empty; the XSD tolerates
    either populated or empty, and the per-context invariant lives in
    the writer.
  * `<jump>` gains an optional <is_tandem> boolean child placed
    immediately after <discipline>. Absent ≡ false. The currency
    calculator (Phase E) counts is_tandem=true jumps inside each
    manufacturer's window.

Test surface:

  * backward compat — minimal pre-D47 jumper.xml validates; jump.xml
    without is_tandem validates;
  * additive shape — maximally populated jumper.xml validates with
    all six collections; jump.xml with is_tandem true / false both
    validate;
  * empty wrappers — collections present but holding zero entries
    validate (writers may emit empty wrappers in edge cases);
  * rig-snapshot tolerance — <rig_snapshot>/<jumper> with and without
    the new collections both validate;
  * structural placement — new collections sit between
    exit_weight_updated_at and created_at, in the order
    memberships / cops / ratings / tandem_ratings / medicals /
    attachments. is_tandem sits between discipline and
    exit_altitude_m on <jump>.

The cross-field constraint (card_attachment_id must point at an
existing attachments/<attachment>/<id>) is NOT enforced at this layer
— XSD 1.0 has no xs:keyref-style integrity check across our shape.
That lives in the service layer (Phase C / D).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from backend.xml.validator import _load_schema

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "xml" / "schema" / "SCHEMA.v1.xsd"
NS = "https://skydive-logbook.org/schema/v1"
XS_NS = "http://www.w3.org/2001/XMLSchema"

SAMPLE_UUID = "11111111-1111-4111-8111-111111111111"
JUMPER_UUID = "22222222-2222-4222-8222-222222222222"
ATTACH_UUID_CSPA = "33333333-3333-4333-8333-333333333333"
ATTACH_UUID_USPA = "44444444-4444-4444-8444-444444444444"
ATTACH_UUID_MEDICAL = "55555555-5555-4555-8555-555555555555"
SAMPLE_SHA256 = "a" * 64


@pytest.fixture(scope="module")
def schema() -> etree.XMLSchema:
    return _load_schema(SCHEMA_PATH)


@pytest.fixture(scope="module")
def schema_root() -> etree._Element:
    return etree.parse(str(SCHEMA_PATH)).getroot()


def _validates(schema: etree.XMLSchema, xml: str) -> bool:
    return schema.validate(etree.fromstring(xml.encode()))


def _validation_errors(schema: etree.XMLSchema, xml: str) -> str:
    schema.validate(etree.fromstring(xml.encode()))
    return str(schema.error_log)


# --------------------------------------------------------------------- #
# Backward-compat: pre-D47 jumper.xml shapes
# --------------------------------------------------------------------- #

class TestJumperBackwardCompat:
    """Pre-D47 jumper.xml files (no credential collections) must still validate."""

    def test_minimal_jumper_validates(self, schema: etree.XMLSchema) -> None:
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<jumper xmlns="{NS}">
  <id>{JUMPER_UUID}</id>
  <exit_weight_lb>180.5</exit_weight_lb>
</jumper>"""
        assert _validates(schema, xml), _validation_errors(schema, xml)

    def test_pre_d47_full_shape_validates(self, schema: etree.XMLSchema) -> None:
        # The shape D33 + D32 produced before D47: identity, weight,
        # weight-confirmed-on, audit timestamps, generator. No
        # collections yet; the schema must accept this shape unchanged.
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<jumper xmlns="{NS}">
  <id>{JUMPER_UUID}</id>
  <name>Alex</name>
  <exit_weight_lb>180.5</exit_weight_lb>
  <exit_weight_updated_at>2026-01-15</exit_weight_updated_at>
  <created_at>2026-01-15T12:00:00.000Z</created_at>
  <updated_at>2026-04-01T09:00:00.000Z</updated_at>
  <generator>skydive-logbook/0.1.0</generator>
</jumper>"""
        assert _validates(schema, xml), _validation_errors(schema, xml)


# --------------------------------------------------------------------- #
# Additive: max-populated jumper.xml
# --------------------------------------------------------------------- #

class TestJumperMaxPopulated:
    """Every D47 collection populated with at least one realistic entry."""

    def test_jumper_with_every_collection_validates(self, schema: etree.XMLSchema) -> None:
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<jumper xmlns="{NS}">
  <id>{JUMPER_UUID}</id>
  <name>Alex</name>
  <exit_weight_lb>180.5</exit_weight_lb>
  <exit_weight_updated_at>2026-01-15</exit_weight_updated_at>

  <memberships>
    <membership>
      <id>aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa</id>
      <org>CSPA</org>
      <member_number>12345</member_number>
      <expiry_date>2027-04-29</expiry_date>
      <card_attachment_id>{ATTACH_UUID_CSPA}</card_attachment_id>
    </membership>
    <membership>
      <id>bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb</id>
      <org>USPA</org>
      <member_number>987654</member_number>
      <expiry_date>2026-12-31</expiry_date>
      <card_attachment_id>{ATTACH_UUID_USPA}</card_attachment_id>
    </membership>
  </memberships>

  <cops>
    <cop>
      <id>cccccccc-cccc-4ccc-8ccc-cccccccccccc</id>
      <org>CSPA</org>
      <level>d</level>
      <issued_date>2024-06-15</issued_date>
    </cop>
    <cop>
      <id>dddddddd-dddd-4ddd-8ddd-dddddddddddd</id>
      <org>USPA</org>
      <level>d</level>
      <issued_date>2024-09-20</issued_date>
    </cop>
  </cops>

  <ratings>
    <rating>
      <id>eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee</id>
      <org>CSPA</org>
      <code>pffi</code>
      <expiry_date>2027-03-31</expiry_date>
    </rating>
    <rating>
      <id>ffffffff-ffff-4fff-8fff-ffffffffffff</id>
      <org>USPA</org>
      <code>affi</code>
      <expiry_date>2026-12-31</expiry_date>
    </rating>
  </ratings>

  <tandem_ratings>
    <tandem_rating>
      <id>10101010-1010-4010-8010-101010101010</id>
      <system>upt_sigma</system>
      <expiry_date>2027-04-29</expiry_date>
      <currency_reset_at>2026-04-15</currency_reset_at>
      <notes>Recurrency jump with examiner.</notes>
    </tandem_rating>
    <tandem_rating>
      <id>20202020-2020-4020-8020-202020202020</id>
      <system>upt_vector</system>
      <expiry_date>2027-04-29</expiry_date>
    </tandem_rating>
  </tandem_ratings>

  <medicals>
    <medical>
      <id>30303030-3030-4030-8030-303030303030</id>
      <kind>class_iii</kind>
      <issuing_authority>Transport Canada</issuing_authority>
      <expiry_date>2028-06-15</expiry_date>
      <card_attachment_id>{ATTACH_UUID_MEDICAL}</card_attachment_id>
    </medical>
  </medicals>

  <attachments>
    <attachment>
      <filename>cspa-card-2026.pdf</filename>
      <sha256>{SAMPLE_SHA256}</sha256>
      <size>234567</size>
      <content_type>application/pdf</content_type>
      <id>{ATTACH_UUID_CSPA}</id>
    </attachment>
    <attachment>
      <filename>uspa-card-2026.pdf</filename>
      <sha256>{"b" * 64}</sha256>
      <size>198432</size>
      <content_type>application/pdf</content_type>
      <id>{ATTACH_UUID_USPA}</id>
    </attachment>
    <attachment>
      <filename>class-iii-medical.pdf</filename>
      <sha256>{"c" * 64}</sha256>
      <size>87654</size>
      <content_type>application/pdf</content_type>
      <id>{ATTACH_UUID_MEDICAL}</id>
    </attachment>
  </attachments>

  <created_at>2026-01-15T12:00:00.000Z</created_at>
  <updated_at>2026-04-29T09:00:00.000Z</updated_at>
  <generator>skydive-logbook/0.1.0</generator>
</jumper>"""
        assert _validates(schema, xml), _validation_errors(schema, xml)

    def test_jumper_with_only_memberships_validates(self, schema: etree.XMLSchema) -> None:
        # Each collection independently optional — populating just one
        # must validate.
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<jumper xmlns="{NS}">
  <id>{JUMPER_UUID}</id>
  <exit_weight_lb>180</exit_weight_lb>
  <memberships>
    <membership>
      <id>{SAMPLE_UUID}</id>
      <org>CSPA</org>
      <member_number>12345</member_number>
      <expiry_date>2027-04-29</expiry_date>
    </membership>
  </memberships>
</jumper>"""
        assert _validates(schema, xml), _validation_errors(schema, xml)

    def test_empty_collection_wrappers_validate(self, schema: etree.XMLSchema) -> None:
        # Writers may legitimately emit empty wrappers in some flows
        # (e.g. clearing the last membership leaves the wrapper). The
        # XSD must accept zero entries inside any wrapper.
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<jumper xmlns="{NS}">
  <id>{JUMPER_UUID}</id>
  <exit_weight_lb>180</exit_weight_lb>
  <memberships></memberships>
  <cops></cops>
  <ratings></ratings>
  <tandem_ratings></tandem_ratings>
  <medicals></medicals>
  <attachments></attachments>
</jumper>"""
        assert _validates(schema, xml), _validation_errors(schema, xml)


# --------------------------------------------------------------------- #
# Rig-snapshot tolerance — JumperContent reused inside <rig_snapshot>
# --------------------------------------------------------------------- #

class TestRigSnapshotJumperTolerance:
    """JumperContent is reused by <rig_snapshot>/<jumper> per D36.

    The snapshot writer skips credential collections — they're jumper-
    state, not jump-state. The XSD must tolerate both shapes (with and
    without collections) so the per-context invariant lives in the
    writer, not the schema. This mirrors how MainContent.lineset_history
    is empty inside snapshots and populated at top level.
    """

    def _wrap_in_snapshot(self, jumper_inner: str) -> str:
        # Minimal rig_snapshot envelope around a <jumper> body. The
        # other component children (main, reserve, aad, container, rig)
        # need to be present for the snapshot to validate; we use the
        # smallest valid forms.
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<rig_snapshot xmlns="{NS}">
  <snapshot_at>2026-04-29T10:00:00.000Z</snapshot_at>
  <rig>
    <id>00000000-0000-4000-8000-000000000001</id>
    <nickname>my-rig</nickname>
    <jurisdiction>CSPA</jurisdiction>
  </rig>
  <main>
    <id>00000000-0000-4000-8000-000000000002</id>
    <status>active</status>
    <jump_count_initial>0</jump_count_initial>
  </main>
  <reserve>
    <id>00000000-0000-4000-8000-000000000003</id>
    <status>active</status>
    <repack_count_initial>0</repack_count_initial>
    <ride_count_initial>0</ride_count_initial>
  </reserve>
  <aad>
    <id>00000000-0000-4000-8000-000000000004</id>
    <status>active</status>
    <jump_count_initial>0</jump_count_initial>
    <fire_count_initial>0</fire_count_initial>
  </aad>
  <container>
    <id>00000000-0000-4000-8000-000000000005</id>
    <status>active</status>
    <jump_count_initial>0</jump_count_initial>
  </container>
  <jumper>
    {jumper_inner}
  </jumper>
</rig_snapshot>"""

    def test_snapshot_jumper_without_collections_validates(
        self, schema: etree.XMLSchema
    ) -> None:
        # The shape the snapshot writer produces today: no credential
        # collections, just identity + weight.
        inner = f"""<id>{JUMPER_UUID}</id>
    <exit_weight_lb>180</exit_weight_lb>"""
        xml = self._wrap_in_snapshot(inner)
        assert _validates(schema, xml), _validation_errors(schema, xml)

    def test_snapshot_jumper_with_collections_also_validates(
        self, schema: etree.XMLSchema
    ) -> None:
        # The XSD permits either shape. If a future feature wants to
        # carry credentials into the snapshot (e.g. proof-of-rating at
        # log time for a regulator audit), it can land additively
        # without touching the XSD. v0.1's writer doesn't emit this,
        # but the shape is reachable.
        inner = f"""<id>{JUMPER_UUID}</id>
    <exit_weight_lb>180</exit_weight_lb>
    <ratings>
      <rating>
        <id>{SAMPLE_UUID}</id>
        <org>CSPA</org>
        <code>pffi</code>
        <expiry_date>2027-04-29</expiry_date>
      </rating>
    </ratings>"""
        xml = self._wrap_in_snapshot(inner)
        assert _validates(schema, xml), _validation_errors(schema, xml)


# --------------------------------------------------------------------- #
# <jump> + is_tandem
# --------------------------------------------------------------------- #

class TestJumpIsTandem:
    """The is_tandem boolean is the jump-side input to the currency calculator."""

    def _minimal_jump_with(self, extra: str) -> str:
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<jump xmlns="{NS}">
  <id>{SAMPLE_UUID}</id>
  <jump_number>1</jump_number>
  <date>2026-04-29</date>
  <dropzone>Skydive Test</dropzone>
  {extra}
  <exit_altitude_m>4000</exit_altitude_m>
  <deployment_altitude_m>900</deployment_altitude_m>
</jump>"""

    def test_jump_without_is_tandem_validates(self, schema: etree.XMLSchema) -> None:
        # Backward compat: every existing jump.xml on disk lacks this
        # field. They must validate unchanged.
        xml = self._minimal_jump_with("")
        assert _validates(schema, xml), _validation_errors(schema, xml)

    def test_jump_with_is_tandem_true_validates(self, schema: etree.XMLSchema) -> None:
        xml = self._minimal_jump_with("<is_tandem>true</is_tandem>")
        assert _validates(schema, xml), _validation_errors(schema, xml)

    def test_jump_with_is_tandem_false_validates(self, schema: etree.XMLSchema) -> None:
        # Absent ≡ false in the calculator, but a writer that always
        # emits the field must still produce a valid file.
        xml = self._minimal_jump_with("<is_tandem>false</is_tandem>")
        assert _validates(schema, xml), _validation_errors(schema, xml)

    def test_jump_rejects_non_boolean_is_tandem(
        self, schema: etree.XMLSchema
    ) -> None:
        # xs:boolean accepts true / false / 1 / 0. Anything else fails.
        xml = self._minimal_jump_with("<is_tandem>maybe</is_tandem>")
        assert not _validates(schema, xml)


# --------------------------------------------------------------------- #
# Structural placement — fail loud if a future edit reorders the schema
# --------------------------------------------------------------------- #

class TestStructuralPlacement:
    """The order of fields in JumperContent and <jump> is a contract.

    A reviewer inspecting a maximally populated jumper.xml on disk
    should see fields in the order this test enforces. Reordering is
    treated the same as renaming — additive within v1 means new
    optional fields go in the documented position, not somewhere a
    casual edit happened to drop them.
    """

    def test_jumper_content_field_order(self, schema_root: etree._Element) -> None:
        jumper = schema_root.find(
            f"{{{XS_NS}}}complexType[@name='JumperContent']"
        )
        assert jumper is not None
        seq = jumper.find(f"{{{XS_NS}}}sequence")
        assert seq is not None
        names = [child.get("name") for child in seq.findall(f"{{{XS_NS}}}element")]
        expected = [
            "id",
            "name",
            "exit_weight_lb",
            "exit_weight_updated_at",
            "memberships",
            "cops",
            "ratings",
            "tandem_ratings",
            "medicals",
            "attachments",
            "created_at",
            "updated_at",
            "generator",
        ]
        assert names == expected, (
            f"JumperContent field order changed.\nexpected: {expected}\n     got: {names}"
        )

    def test_is_tandem_position_in_jump(self, schema_root: etree._Element) -> None:
        jump_el = schema_root.find(f"{{{XS_NS}}}element[@name='jump']")
        assert jump_el is not None
        complex_type = jump_el.find(f"{{{XS_NS}}}complexType")
        assert complex_type is not None
        seq = complex_type.find(f"{{{XS_NS}}}sequence")
        assert seq is not None
        names = [child.get("name") for child in seq.findall(f"{{{XS_NS}}}element")]
        # is_tandem must sit immediately after discipline so it groups
        # with the "kind of jump" descriptors. Catches accidental
        # placement at the end alongside audit fields.
        idx_disc = names.index("discipline")
        idx_tandem = names.index("is_tandem")
        assert idx_tandem == idx_disc + 1, (
            f"is_tandem must immediately follow discipline; "
            f"got {names[idx_disc:idx_tandem + 1]}"
        )


# --------------------------------------------------------------------- #
# Cross-reference (structural only — XSD doesn't enforce keyref here)
# --------------------------------------------------------------------- #

def test_card_attachment_id_cross_reference_passes_xsd(
    schema: etree.XMLSchema,
) -> None:
    """A credential's `card_attachment_id` referencing an `<id>` inside
    `<attachments>` validates. XSD 1.0 doesn't enforce that the id
    actually exists — that's a service-layer concern (Phase C / D) —
    but the structural shape must validate."""
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<jumper xmlns="{NS}">
  <id>{JUMPER_UUID}</id>
  <exit_weight_lb>180</exit_weight_lb>
  <memberships>
    <membership>
      <id>{SAMPLE_UUID}</id>
      <org>CSPA</org>
      <member_number>12345</member_number>
      <expiry_date>2027-04-29</expiry_date>
      <card_attachment_id>{ATTACH_UUID_CSPA}</card_attachment_id>
    </membership>
  </memberships>
  <attachments>
    <attachment>
      <filename>cspa-card.pdf</filename>
      <sha256>{SAMPLE_SHA256}</sha256>
      <size>1024</size>
      <id>{ATTACH_UUID_CSPA}</id>
    </attachment>
  </attachments>
</jumper>"""
    assert _validates(schema, xml), _validation_errors(schema, xml)
