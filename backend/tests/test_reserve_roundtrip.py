"""End-to-end XML round-trip + XSD validation for Reserve (R.0.2d).

Pipeline mirrors the Container and AAD round-trips:
  Reserve → serialize → parse → XSD-validate → Reserve  ==  original

Pin reserve-specific shape:
  * No jump counter (D35 §2553 — reserves are packed, not jumped).
  * Both D35 counter seeds always emitted (deterministic projection
    seed).
  * size_sqft round-trips as decimal without spurious trailing zero.
  * recert_extensions is an empty-list-elides-wrapper structured log.
"""
from __future__ import annotations

from datetime import date
from uuid import uuid4

from backend.models._component_base import ComponentStatus, NotesLogEntry
from backend.models.reserve import Reserve, ReserveRecertExtension
from backend.xml.serialize import (
    element_to_reserve,
    reserve_to_bytes,
    reserve_to_element,
)
from backend.xml.validator import parse, validate


class TestReserveRoundTrip:
    def test_minimal_reserve_roundtrips(self):
        original = Reserve()
        raw = reserve_to_bytes(original)
        element = parse(raw)
        validate(element)
        restored = element_to_reserve(element)
        assert restored == original

    def test_full_field_set_roundtrips(self):
        original = Reserve(
            id=uuid4(),
            status=ComponentStatus.ACTIVE,
            assigned_rig_id=uuid4(),
            notes_log=[
                NotesLogEntry(
                    at="2026-04-28T14:30:00.000Z",
                    text="Bought used; 14 prior repacks recorded",
                ),
            ],
            manufacturer="Performance Designs",
            model="Optimum",
            serial="OP-987654",
            size_sqft=143.0,
            date_of_manufacture=date(2019, 8, 1),
            repack_limit=40,
            ride_limit=25,
            repack_count_initial=14,
            ride_count_initial=0,
            recert_extensions=[
                ReserveRecertExtension(
                    granted_at="2025-06-01T09:00:00.000Z",
                    extends_until=date(2030, 6, 1),
                    granted_by="Master Rigger A. Smith",
                    reason="Annual factory recert",
                ),
            ],
            created_at="2026-04-28T14:30:00.000Z",
            updated_at="2026-04-28T14:30:00.000Z",
        )
        element = reserve_to_element(original)
        validate(element)
        assert element_to_reserve(element) == original

    def test_optional_identification_fields_elide_when_none(self):
        original = Reserve()
        raw = reserve_to_bytes(original)
        for absent in (
            b"<manufacturer>",
            b"<model>",
            b"<serial>",
            b"<size_sqft>",
            b"<date_of_manufacture>",
            b"<repack_limit>",
            b"<ride_limit>",
        ):
            assert absent not in raw, f"unexpected element {absent!r} in output"

    def test_size_sqft_integer_value_emits_without_decimal(self):
        # ``:g`` strips trailing .0 — same posture as Jump.exit_altitude_m.
        original = Reserve(size_sqft=143.0)
        raw = reserve_to_bytes(original)
        assert b"<size_sqft>143</size_sqft>" in raw
        assert b"<size_sqft>143.0</size_sqft>" not in raw

    def test_size_sqft_fractional_preserved(self):
        # Don't lose precision for non-integer areas.
        original = Reserve(size_sqft=143.5)
        raw = reserve_to_bytes(original)
        assert b"<size_sqft>143.5</size_sqft>" in raw
        restored = element_to_reserve(parse(raw))
        assert restored.size_sqft == 143.5

    def test_no_jump_counter_field(self):
        # D35 §2553: reserves deliberately have NO jump counter. The
        # XSD does not declare ``jump_count_initial`` for reserves;
        # the model has no such field. A future drift would surface
        # as a ValidationError on construct.
        # Pydantic v2 with extra="forbid" rejects unknown fields.
        from pydantic import ValidationError
        try:
            Reserve(jump_count_initial=10)  # type: ignore[call-arg]
        except ValidationError:
            return
        raise AssertionError("Reserve unexpectedly accepts jump_count_initial")

    def test_both_counters_always_emit(self):
        # D35: repack_count_initial + ride_count_initial both default
        # to 0 and both are required on the XSD.
        original = Reserve()
        raw = reserve_to_bytes(original)
        assert b"<repack_count_initial>0</repack_count_initial>" in raw
        assert b"<ride_count_initial>0</ride_count_initial>" in raw

    def test_used_gear_starting_counts_roundtrip(self):
        original = Reserve(repack_count_initial=14, ride_count_initial=2)
        element = reserve_to_element(original)
        validate(element)
        restored = element_to_reserve(element)
        assert restored.repack_count_initial == 14
        assert restored.ride_count_initial == 2

    def test_empty_recert_extensions_elides_wrapper(self):
        original = Reserve(recert_extensions=[])
        raw = reserve_to_bytes(original)
        assert b"<recert_extensions>" not in raw

    def test_recert_extension_minimal_fields(self):
        # Only the two required fields set; granted_by + reason elide.
        ext = ReserveRecertExtension(
            granted_at="2025-06-01T09:00:00.000Z",
            extends_until=date(2030, 6, 1),
        )
        original = Reserve(recert_extensions=[ext])
        element = reserve_to_element(original)
        validate(element)
        raw = reserve_to_bytes(original)
        assert b"<granted_by>" not in raw
        assert b"<reason>" not in raw
        restored = element_to_reserve(parse(raw))
        assert restored.recert_extensions == [ext]

    def test_recert_extensions_preserves_order(self):
        exts = [
            ReserveRecertExtension(
                granted_at=f"2025-{m:02d}-01T09:00:00.000Z",
                extends_until=date(2030, m, 1),
                granted_by=f"Rigger {m}",
            )
            for m in (1, 6, 12)
        ]
        original = Reserve(recert_extensions=exts)
        element = reserve_to_element(original)
        validate(element)
        restored = element_to_reserve(element)
        assert restored.recert_extensions == exts

    def test_status_retired_validates(self):
        for s in ComponentStatus:
            r = Reserve(status=s)
            element = reserve_to_element(r)
            validate(element)


class TestReserveXSDContract:
    def test_negative_repack_count_initial_fails_xsd(self):
        from backend.models.common import SCHEMA_NAMESPACE_V1
        from backend.xml.validator import XMLError, parse, validate
        ns = SCHEMA_NAMESPACE_V1
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<reserve xmlns="{ns}">
  <id>11111111-1111-4111-8111-111111111111</id>
  <status>active</status>
  <repack_count_initial>-1</repack_count_initial>
  <ride_count_initial>0</ride_count_initial>
</reserve>
""".encode()
        element = parse(xml)
        try:
            validate(element)
        except XMLError:
            return
        raise AssertionError("XSD did not reject negative repack_count_initial")

    def test_recert_extension_missing_extends_until_fails_xsd(self):
        # XSD-required field on the nested ReserveRecertExtension type.
        from backend.models.common import SCHEMA_NAMESPACE_V1
        from backend.xml.validator import XMLError, parse, validate
        ns = SCHEMA_NAMESPACE_V1
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<reserve xmlns="{ns}">
  <id>11111111-1111-4111-8111-111111111111</id>
  <status>active</status>
  <repack_count_initial>0</repack_count_initial>
  <ride_count_initial>0</ride_count_initial>
  <recert_extensions>
    <extension>
      <granted_at>2025-06-01T09:00:00.000Z</granted_at>
    </extension>
  </recert_extensions>
</reserve>
""".encode()
        element = parse(xml)
        try:
            validate(element)
        except XMLError:
            return
        raise AssertionError("XSD did not reject missing extends_until")
