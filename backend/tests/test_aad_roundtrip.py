"""End-to-end XML round-trip + XSD validation for AAD (R.0.2c).

Pipeline mirrors the Container round-trip tests:
  AAD → serialize → parse → XSD-validate → AAD  ==  original

Pin the manufacturer-not-brand convention (D34 amended 2026-04-28),
the both-counters-always-emitted invariant (D35), and the boolean
round-trip on ``is_changeable_mode``.
"""
from __future__ import annotations

from datetime import date
from uuid import uuid4

from backend.models._component_base import ComponentStatus, NotesLogEntry
from backend.models.aad import AAD
from backend.xml.serialize import aad_to_bytes, aad_to_element, element_to_aad
from backend.xml.validator import parse, validate


class TestAADRoundTrip:
    def test_minimal_aad_roundtrips(self):
        original = AAD()
        raw = aad_to_bytes(original)
        element = parse(raw)
        validate(element)
        restored = element_to_aad(element)
        assert restored == original

    def test_full_field_set_roundtrips(self):
        # A typical Cypres 2 record with full provenance.
        original = AAD(
            id=uuid4(),
            status=ComponentStatus.ACTIVE,
            assigned_rig_id=uuid4(),
            notes_log=[
                NotesLogEntry(
                    at="2026-04-28T14:30:00.000Z",
                    text="Onboarded; 4-year service current",
                ),
            ],
            manufacturer="Airtec",
            model="Cypres 2",
            serial="C2-987654",
            date_of_manufacture=date(2017, 3, 12),
            mode="Pro",
            is_changeable_mode=True,
            jump_count_initial=420,
            fire_count_initial=0,
            created_at="2026-04-28T14:30:00.000Z",
            updated_at="2026-04-28T14:30:00.000Z",
        )
        element = aad_to_element(original)
        validate(element)
        assert element_to_aad(element) == original

    def test_manufacturer_field_name_is_manufacturer_not_brand(self):
        # Pin the D34 amendment (2026-04-28). If a future refactor
        # accidentally renames the field back to ``brand`` on the
        # XSD or the serializer, this assertion is the canary.
        a = AAD(manufacturer="Vigil")
        raw = aad_to_bytes(a)
        assert b"<manufacturer>Vigil</manufacturer>" in raw
        assert b"<brand>" not in raw

    def test_optional_identification_fields_elide_when_none(self):
        original = AAD()
        raw = aad_to_bytes(original)
        for absent in (
            b"<manufacturer>",
            b"<model>",
            b"<serial>",
            b"<date_of_manufacture>",
            b"<mode>",
            b"<is_changeable_mode>",
        ):
            assert absent not in raw, f"unexpected element {absent!r} in output"

    def test_is_changeable_mode_true_roundtrips(self):
        original = AAD(is_changeable_mode=True)
        element = aad_to_element(original)
        validate(element)
        restored = element_to_aad(element)
        assert restored.is_changeable_mode is True

    def test_is_changeable_mode_false_roundtrips(self):
        original = AAD(is_changeable_mode=False)
        element = aad_to_element(original)
        validate(element)
        restored = element_to_aad(element)
        assert restored.is_changeable_mode is False

    def test_is_changeable_mode_none_elides_element(self):
        # None means "unknown / not recorded" — distinct from False.
        # Keeping it absent on serialize means a hand-crafted file
        # without the element parses to None, not False.
        original = AAD(is_changeable_mode=None)
        raw = aad_to_bytes(original)
        assert b"<is_changeable_mode>" not in raw
        restored = element_to_aad(parse(raw))
        assert restored.is_changeable_mode is None

    def test_both_counters_always_emit(self):
        # D35: jump_count_initial and fire_count_initial both default
        # to 0 and both are required on the XSD. Always emitting them
        # gives the projection layer a deterministic seed.
        original = AAD()  # both counters at default 0
        raw = aad_to_bytes(original)
        assert b"<jump_count_initial>0</jump_count_initial>" in raw
        assert b"<fire_count_initial>0</fire_count_initial>" in raw

    def test_used_gear_starting_counts_roundtrip(self):
        original = AAD(jump_count_initial=850, fire_count_initial=2)
        element = aad_to_element(original)
        validate(element)
        restored = element_to_aad(element)
        assert restored.jump_count_initial == 850
        assert restored.fire_count_initial == 2

    def test_status_retired_validates(self):
        for s in ComponentStatus:
            a = AAD(status=s)
            element = aad_to_element(a)
            validate(element)


class TestAADXSDContract:
    def test_negative_fire_count_initial_fails_xsd(self):
        from backend.models.common import SCHEMA_NAMESPACE_V1
        from backend.xml.validator import XMLError, parse, validate
        ns = SCHEMA_NAMESPACE_V1
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<aad xmlns="{ns}">
  <id>11111111-1111-4111-8111-111111111111</id>
  <status>active</status>
  <jump_count_initial>0</jump_count_initial>
  <fire_count_initial>-1</fire_count_initial>
</aad>
""".encode()
        element = parse(xml)
        try:
            validate(element)
        except XMLError:
            return
        raise AssertionError("XSD did not reject negative fire_count_initial")

    def test_invalid_is_changeable_mode_fails_xsd(self):
        from backend.models.common import SCHEMA_NAMESPACE_V1
        from backend.xml.validator import XMLError, parse, validate
        ns = SCHEMA_NAMESPACE_V1
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<aad xmlns="{ns}">
  <id>11111111-1111-4111-8111-111111111111</id>
  <status>active</status>
  <is_changeable_mode>maybe</is_changeable_mode>
  <jump_count_initial>0</jump_count_initial>
  <fire_count_initial>0</fire_count_initial>
</aad>
""".encode()
        element = parse(xml)
        try:
            validate(element)
        except XMLError:
            return
        raise AssertionError("XSD did not reject non-boolean is_changeable_mode")
