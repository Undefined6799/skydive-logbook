"""D53 round-trip tests — jump_types, landing, packer, group fields.

Pinned contracts (D53 §Decision, D57 §Decision):
- The four surviving D53 fields are optional; pre-D53 jump.xml
  round-trips byte-stable (no synthetic emission of empty wrappers /
  None scalars).
- ``jump_types`` is multi-valued and elides the wrapper element when
  the list is empty.
- ``jump_types`` accepts every value in the closed enum and rejects
  unknown values at both the Pydantic and XSD layers.
- ``landing_distance_m`` is the sole landing-accuracy field after
  D57; the directional half (``landing_direction``) was removed.
  On-target ≡ absent.
- ``packed_by`` absent ≡ self-packed (D53 §Decision; D54 §Why).
- ``group_members`` is the named subset of jumpers on the dive. The
  scalar ``group_size`` was removed by D57 — the count is implied by
  ``len(group_members)``.

The full pipeline under test:
    Jump → jump_to_bytes → parse → validate → element_to_jump  ==  Jump
"""
from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from backend.models.jump import (
    Jump,
    JumpCreate,
    JumpType,
    JumpUpdate,
)
from backend.xml.serialize import (
    element_to_jump,
    jump_to_bytes,
    jump_to_element,
)
from backend.xml.validator import XSDValidationError, parse, validate

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _minimal_jump(**overrides) -> Jump:
    """Build a minimal valid Jump; tests override the D53 fields."""
    data = dict(
        id=uuid4(),
        jump_number=1,
        date=date(2026, 4, 30),
        dropzone="Elsinore",
        exit_altitude_m=4000,
        deployment_altitude_m=900,
    )
    data.update(overrides)
    return Jump(**data)


def _roundtrip(j: Jump) -> Jump:
    """Serialize → parse → XSD-validate → deserialize."""
    raw = jump_to_bytes(j)
    element = parse(raw)
    validate(element)
    return element_to_jump(element)


# --------------------------------------------------------------------------- #
# Default (no D53 fields set) — back-compat invariant
# --------------------------------------------------------------------------- #

class TestDefaultsAbsent:
    def test_defaults_round_trip(self):
        # Minimal Jump has no D53 fields set. Round-trips identically.
        original = _minimal_jump()
        restored = _roundtrip(original)
        assert restored == original
        # Defaults: empty list / None — explicitly pin so a future
        # change to defaults would fail loudly here. landing_direction
        # and group_size were removed by D57.
        assert restored.jump_types == []
        assert restored.landing_distance_m is None
        assert restored.packed_by is None
        assert restored.group_members == []

    def test_default_emit_omits_d53_elements(self):
        # Pre-D53 jump.xml byte-stability: when no D53 field is set,
        # none of the new elements appear in the serialized XML. A
        # hand-crafted jump.xml without any D53 tags must continue to
        # round-trip unchanged. landing_direction / group_size are
        # not in this list — D57 removed them from the schema, so
        # they cannot appear in XSD-valid XML at all.
        ns = "{https://skydive-logbook.org/schema/v1}"
        element = jump_to_element(_minimal_jump())
        for tag in (
            "jump_types",
            "landing_distance_m",
            "packed_by",
            "group_members",
        ):
            assert element.find(f"{ns}{tag}") is None, (
                f"<{tag}> must elide when the model field is unset"
            )

    def test_pre_d53_xml_parses_after_d53_lands(self):
        # Build a Jump with no D53 fields, write it, drop the bytes
        # back through the parser. Acts as the "old logbook upgraded
        # to D53 build" case — a file written before D53 has none of
        # these elements; the parser must accept that.
        original = _minimal_jump(jump_number=42, dropzone="Old DZ")
        raw = jump_to_bytes(original)
        # Sanity: confirm the raw really has no D53 elements (it
        # shouldn't, given test_default_emit_omits_d53_elements).
        assert b"<jump_types" not in raw
        assert b"<landing_distance_m" not in raw
        assert b"<packed_by" not in raw
        # Now parse + validate + restore.
        element = parse(raw)
        validate(element)
        restored = element_to_jump(element)
        assert restored == original


# --------------------------------------------------------------------------- #
# jump_types — closed enum, multi-value
# --------------------------------------------------------------------------- #

class TestJumpTypes:
    def test_single_value_round_trips(self):
        original = _minimal_jump(jump_types=[JumpType.REGULAR_JUMP])
        restored = _roundtrip(original)
        assert restored.jump_types == [JumpType.REGULAR_JUMP]
        assert restored == original

    def test_multi_value_round_trips_with_order_preserved(self):
        # Camera flyer organizing an angle dive: two facets on one
        # jump. Order is preserved in the wrapper for determinism.
        types = [JumpType.CAMERA, JumpType.ORGANIZING]
        original = _minimal_jump(jump_types=types)
        restored = _roundtrip(original)
        assert restored.jump_types == types

    def test_every_enum_value_round_trips(self):
        # Pin the full closed enum so adding a value (additive per
        # D18) is caught by this test as needing a new entry.
        all_types = list(JumpType)
        assert len(all_types) == 7  # regular_jump, coaching, instructing, camera, organizing, coached, instructed
        original = _minimal_jump(jump_types=all_types)
        restored = _roundtrip(original)
        assert restored.jump_types == all_types

    def test_empty_list_elides_wrapper_in_emit(self):
        ns = "{https://skydive-logbook.org/schema/v1}"
        element = jump_to_element(_minimal_jump(jump_types=[]))
        assert element.find(f"{ns}jump_types") is None

    def test_unknown_value_rejected_by_pydantic(self):
        # StrEnum rejects unknown strings at construction time.
        with pytest.raises(ValidationError):
            _minimal_jump(jump_types=["totally_made_up"])

    def test_xsd_rejects_unknown_value_in_xml(self):
        # Defense in depth: even if a hand-crafted jump.xml smuggles
        # an unknown <jump_type>, the XSD must reject it. Confirms
        # the closed-enum discipline holds at the schema layer.
        from lxml import etree

        ns = "https://skydive-logbook.org/schema/v1"
        element = jump_to_element(_minimal_jump())
        wrap = etree.SubElement(element, f"{{{ns}}}jump_types")
        bad = etree.SubElement(wrap, f"{{{ns}}}jump_type")
        bad.text = "wingsuit"  # not in the v0.1 enum — angle/tracking belong to discipline
        # Reorder is unnecessary because we appended after attachments;
        # XSD validation may still complain about ordering. The point
        # of this test is the enum rejection — patch validate to be
        # robust by inserting the wrapper in the right position.
        # Simplest path: serialize a fresh Jump, hand-edit the bytes
        # to splice in the bad value at the correct position.
        original = _minimal_jump(jump_types=[JumpType.REGULAR_JUMP])
        raw = jump_to_bytes(original)
        # Replace the legitimate "regular_jump" with an invalid token —
        # keeps every other element in the right XSD-sequence slot.
        broken = raw.replace(b"regular_jump", b"wingsuit")
        with pytest.raises(XSDValidationError):
            validate(parse(broken))


# --------------------------------------------------------------------------- #
# Landing accuracy — D57 keeps only the magnitude
# --------------------------------------------------------------------------- #

class TestLandingDistance:
    def test_distance_only_round_trips(self):
        # ``landing_distance_m`` is the sole landing-accuracy field
        # after D57.
        original = _minimal_jump(landing_distance_m=12.5)
        restored = _roundtrip(original)
        assert restored.landing_distance_m == 12.5

    def test_zero_distance_is_valid(self):
        # XSD minInclusive value="0" — exactly on-target with the
        # distance recorded as 0 (rather than absent) is legal. The
        # canonical on-target signal is "absent" but explicit 0 is
        # still a valid round-trip.
        original = _minimal_jump(landing_distance_m=0)
        restored = _roundtrip(original)
        assert restored.landing_distance_m == 0

    def test_integer_distance_emits_without_decimal(self):
        # ``:g`` formatting — same posture as exit_altitude_m. A
        # whole-number value emits as "50", not "50.0".
        original = _minimal_jump(landing_distance_m=50)
        raw = jump_to_bytes(original)
        assert b"<landing_distance_m>50</landing_distance_m>" in raw

    def test_fractional_distance_round_trips_exactly(self):
        # 12.34 m → "12.34" → 12.34 m. Exact like exit_altitude_m.
        original = _minimal_jump(landing_distance_m=12.34)
        restored = _roundtrip(original)
        assert restored.landing_distance_m == 12.34

    def test_negative_distance_rejected_by_pydantic(self):
        with pytest.raises(ValidationError):
            _minimal_jump(landing_distance_m=-1)


# --------------------------------------------------------------------------- #
# Packer — UUID reference; absent ≡ self-packed
# --------------------------------------------------------------------------- #

class TestPackedBy:
    def test_present_round_trips(self):
        person_id = uuid4()
        original = _minimal_jump(packed_by=person_id)
        restored = _roundtrip(original)
        assert restored.packed_by == person_id

    def test_absent_means_self_packed(self):
        # Convention pinned by D53: no packed_by element ≡ self.
        # The model defaults to None; emit elides; parse leaves None.
        original = _minimal_jump()  # packed_by defaults to None
        restored = _roundtrip(original)
        assert restored.packed_by is None

    def test_invalid_uuid_string_rejected_by_pydantic(self):
        with pytest.raises(ValidationError):
            _minimal_jump(packed_by="not-a-uuid")


# --------------------------------------------------------------------------- #
# Group facts — named members only after D57
# --------------------------------------------------------------------------- #

class TestGroupMembers:
    def test_members_round_trip(self):
        # Three friends named on the dive. ``group_size`` was removed
        # by D57 — the headline count is implied by len(members).
        members = [uuid4(), uuid4(), uuid4()]
        original = _minimal_jump(group_members=members)
        restored = _roundtrip(original)
        assert restored.group_members == members

    def test_invalid_member_uuid_rejected_by_pydantic(self):
        with pytest.raises(ValidationError):
            _minimal_jump(group_members=["not-a-uuid"])

    def test_empty_members_list_elides_wrapper(self):
        ns = "{https://skydive-logbook.org/schema/v1}"
        element = jump_to_element(_minimal_jump(group_members=[]))
        assert element.find(f"{ns}group_members") is None


# --------------------------------------------------------------------------- #
# All-surviving-D53-fields-set kitchen-sink round-trip
# --------------------------------------------------------------------------- #

class TestKitchenSink:
    def test_all_d53_fields_round_trip_together(self):
        # The big composability check: every D53 field set at once
        # in a single Jump round-trips end-to-end without loss. Pins
        # field-order independence (each field's emit/parse minds
        # its own siblings). landing_direction and group_size were
        # removed by D57.
        person_a = uuid4()
        person_b = uuid4()
        original = _minimal_jump(
            jump_types=[JumpType.COACHING, JumpType.CAMERA],
            landing_distance_m=15.5,
            packed_by=person_a,
            group_members=[person_a, person_b],
        )
        restored = _roundtrip(original)
        assert restored == original


# --------------------------------------------------------------------------- #
# JumpCreate / JumpUpdate also accept the new fields (REST surface)
# --------------------------------------------------------------------------- #

class TestRequestBodies:
    def test_jump_create_accepts_all_d53_fields(self):
        person_id = uuid4()
        body = JumpCreate(
            jump_number=1,
            date=date(2026, 4, 30),
            dropzone="Elsinore",
            exit_altitude_m=4000,
            deployment_altitude_m=900,
            jump_types=[JumpType.INSTRUCTING],
            landing_distance_m=5,
            packed_by=person_id,
            group_members=[person_id],
        )
        assert body.jump_types == [JumpType.INSTRUCTING]
        assert body.landing_distance_m == 5
        assert body.packed_by == person_id
        assert body.group_members == [person_id]

    def test_jump_update_accepts_all_d53_fields(self):
        body = JumpUpdate(
            jump_number=1,
            date=date(2026, 4, 30),
            dropzone="Elsinore",
            exit_altitude_m=4000,
            deployment_altitude_m=900,
            jump_types=[JumpType.COACHED],
            group_members=[uuid4()],
        )
        assert body.jump_types == [JumpType.COACHED]
        assert len(body.group_members) == 1
        # Unset fields default to None same as on Jump.
        assert body.packed_by is None
        assert body.landing_distance_m is None

    def test_jump_create_defaults_match_jump(self):
        # With no D53 fields supplied, JumpCreate's defaults match
        # Jump's. Pin so a future drift between the two surfaces
        # (e.g. only adding the field to one) fails here. D57
        # removed landing_direction and group_size from both
        # surfaces, so neither appears.
        body = JumpCreate(
            jump_number=1,
            date=date(2026, 4, 30),
            dropzone="Elsinore",
            exit_altitude_m=4000,
            deployment_altitude_m=900,
        )
        assert body.jump_types == []
        assert body.landing_distance_m is None
        assert body.packed_by is None
        assert body.group_members == []


# --------------------------------------------------------------------------- #
# Hand-crafted XML — pre-D53 file shape still validates
# --------------------------------------------------------------------------- #

class TestHandCraftedPreD53XML:
    def test_pre_d53_jump_xml_validates_and_parses(self):
        # A hand-authored jump.xml that pre-dates D53 carries no D53
        # elements. Pin that this is still XSD-valid AND parses to a
        # Jump with all-default D53 field values.
        ns = "https://skydive-logbook.org/schema/v1"
        jump_id = uuid4()
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<jump xmlns="{ns}">
  <id>{jump_id}</id>
  <jump_number>500</jump_number>
  <date>2024-06-15</date>
  <dropzone>Old DZ</dropzone>
  <exit_altitude_m>4000</exit_altitude_m>
  <deployment_altitude_m>900</deployment_altitude_m>
</jump>
""".encode()
        element = parse(xml)
        validate(element)  # raises if XSD-invalid
        jump = element_to_jump(element)
        assert jump.id == jump_id
        assert jump.jump_number == 500
        # All surviving D53 fields default to empty / None.
        assert jump.jump_types == []
        assert jump.landing_distance_m is None
        assert jump.packed_by is None
        assert jump.group_members == []

    def test_d53_fields_in_correct_xsd_order(self):
        # The XSD is an ordered sequence; emitting the D53 fields in
        # the wrong order would fail validate(). Build a Jump with
        # every surviving D53 field populated and confirm the emit
        # order matches XSD's expectation. landing_direction and
        # group_size were removed by D57.
        person_id = uuid4()
        original = _minimal_jump(
            jump_types=[JumpType.REGULAR_JUMP],
            landing_distance_m=10,
            packed_by=person_id,
            group_members=[person_id],
        )
        element = jump_to_element(original)
        # Validate against the XSD — strictest check on element order.
        validate(element)
        # Belt-and-braces: assert emitted order by walking children.
        children = [
            etree_qname_local(c.tag) for c in element
            if etree_qname_local(c.tag) in {
                "attachments",
                "jump_types",
                "landing_distance_m",
                "packed_by",
                "group_members",
                "signature",
                "created_at",
            }
        ]
        # Required positional ordering: jump_types comes before
        # landing_distance_m, packed_by, group_members, signature.
        idx = {tag: i for i, tag in enumerate(children)}
        assert "jump_types" in idx
        assert "landing_distance_m" in idx
        assert "packed_by" in idx
        assert "group_members" in idx
        assert (
            idx["jump_types"]
            < idx["landing_distance_m"]
            < idx["packed_by"]
            < idx["group_members"]
        )


def etree_qname_local(tag: str) -> str:
    """Strip namespace prefix from an lxml-style ``{ns}local`` tag."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


# --------------------------------------------------------------------------- #
# Sanity: the new types are wired into the public model surface
# --------------------------------------------------------------------------- #

class TestPublicSurface:
    def test_jump_type_str_enum_round_trips_to_string(self):
        # StrEnum: JumpType.REGULAR_JUMP == "regular_jump"
        assert JumpType.REGULAR_JUMP == "regular_jump"
        assert JumpType("regular_jump") is JumpType.REGULAR_JUMP

    def test_uuid_field_accepts_uuid_object_or_string(self):
        # Pydantic coerces UUID strings to UUID objects on the field.
        s = "11111111-1111-4111-8111-111111111111"
        j1 = _minimal_jump(packed_by=UUID(s))
        j2 = _minimal_jump(packed_by=s)
        assert j1.packed_by == j2.packed_by == UUID(s)
