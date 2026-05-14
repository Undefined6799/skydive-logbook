"""End-to-end XML round-trip + XSD validation for Jumper (R.2.0a).

Pipeline mirrors the per-component round-trips from R.0.2:
    Jumper → serialize → parse → XSD-validate → Jumper  ==  original

Pin jumper-specific shape:
  * exit_weight_lb is required, strictly > 0.
  * exit_weight_updated_at is xs:date (not dateTime).
  * name is optional with 1..120 char bounds when set.
  * D32 timestamps preserved through round-trip.
"""
from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.models.common import SCHEMA_NAMESPACE_V1
from backend.models.jumper import Jumper, JumperCreate, JumperUpdate
from backend.xml.serialize import (
    element_to_jumper,
    jumper_to_bytes,
    jumper_to_element,
)
from backend.xml.validator import XMLError, parse, validate

_J = "55555555-5555-4555-8555-555555555555"


class TestJumperRoundTrip:
    def test_minimal_jumper_roundtrips(self):
        original = Jumper(exit_weight_lb=200.0)
        raw = jumper_to_bytes(original)
        element = parse(raw)
        validate(element)
        assert element_to_jumper(element) == original

    def test_full_field_set_roundtrips(self):
        original = Jumper(
            id=uuid4(),
            name="Alex Tester",
            exit_weight_lb=205.5,
            exit_weight_updated_at=date(2026, 4, 28),
            created_at="2026-04-28T14:30:00.000Z",
            updated_at="2026-04-28T14:30:00.000Z",
        )
        element = jumper_to_element(original)
        validate(element)
        assert element_to_jumper(element) == original

    def test_optional_name_elides_when_none(self):
        original = Jumper(exit_weight_lb=180.0)
        raw = jumper_to_bytes(original)
        assert b"<name>" not in raw

    def test_optional_exit_weight_updated_at_elides_when_none(self):
        original = Jumper(exit_weight_lb=180.0)
        raw = jumper_to_bytes(original)
        assert b"<exit_weight_updated_at>" not in raw

    def test_integer_exit_weight_emits_without_decimal(self):
        # ``:g`` strips trailing .0 so a whole-pound value emits as
        # an integer, matching the posture on Jump.exit_altitude_m.
        original = Jumper(exit_weight_lb=200.0)
        raw = jumper_to_bytes(original)
        assert b"<exit_weight_lb>200</exit_weight_lb>" in raw
        assert b"<exit_weight_lb>200.0</exit_weight_lb>" not in raw

    def test_fractional_exit_weight_preserved(self):
        original = Jumper(exit_weight_lb=205.5)
        raw = jumper_to_bytes(original)
        assert b"<exit_weight_lb>205.5</exit_weight_lb>" in raw
        restored = element_to_jumper(parse(raw))
        assert restored.exit_weight_lb == 205.5

    def test_unicode_name_roundtrips(self):
        original = Jumper(name="François Béland", exit_weight_lb=200.0)
        element = jumper_to_element(original)
        validate(element)
        assert element_to_jumper(element) == original

    def test_d32_timestamps_preserved(self):
        original = Jumper(
            exit_weight_lb=200.0,
            created_at="2026-04-28T10:00:00.123Z",
            updated_at="2026-04-28T14:30:00.456Z",
        )
        element = jumper_to_element(original)
        validate(element)
        restored = element_to_jumper(element)
        assert restored.created_at == "2026-04-28T10:00:00.123Z"
        assert restored.updated_at == "2026-04-28T14:30:00.456Z"

    def test_exit_weight_updated_at_is_date_not_datetime(self):
        # The XSD declares xs:date for this field. A datetime string
        # in the wire format would not round-trip; confirm the
        # serializer emits a plain date.
        original = Jumper(
            exit_weight_lb=200.0,
            exit_weight_updated_at=date(2026, 4, 28),
        )
        raw = jumper_to_bytes(original)
        assert b"<exit_weight_updated_at>2026-04-28</exit_weight_updated_at>" in raw
        assert b"T00:00:00" not in raw


class TestJumperPydanticContract:
    def test_jumper_rejects_unknown_field(self):
        with pytest.raises(ValidationError):
            Jumper(exit_weight_lb=200.0, height_in=72)  # type: ignore[call-arg]

    def test_jumper_requires_exit_weight(self):
        with pytest.raises(ValidationError):
            Jumper()  # type: ignore[call-arg]

    def test_jumper_rejects_zero_exit_weight(self):
        # gt=0 — zero would divide-by-zero in wingloading.
        with pytest.raises(ValidationError):
            Jumper(exit_weight_lb=0)

    def test_jumper_rejects_negative_exit_weight(self):
        with pytest.raises(ValidationError):
            Jumper(exit_weight_lb=-1)

    def test_jumper_name_max_length(self):
        # 120 OK, 121 rejected.
        Jumper(name="a" * 120, exit_weight_lb=200.0)
        with pytest.raises(ValidationError):
            Jumper(name="a" * 121, exit_weight_lb=200.0)

    def test_jumper_name_empty_string_rejected(self):
        # Optional, but if set it must be 1..120 chars.
        with pytest.raises(ValidationError):
            Jumper(name="", exit_weight_lb=200.0)

    def test_jumper_create_shape_matches(self):
        # JumperCreate accepts the same fields as Jumper minus the
        # server-assigned id and the audit timestamps.
        c = JumperCreate(name="A", exit_weight_lb=200.0)
        assert c.exit_weight_lb == 200.0
        assert c.exit_weight_updated_at is None

    def test_jumper_update_requires_exit_weight(self):
        # Full-replace shape — exit_weight is mandatory on update too.
        with pytest.raises(ValidationError):
            JumperUpdate()  # type: ignore[call-arg]


class TestJumperXSDContract:
    def test_zero_exit_weight_fails_xsd(self):
        # XSD: xs:minExclusive=0 — zero is not a valid value.
        ns = SCHEMA_NAMESPACE_V1
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<jumper xmlns="{ns}">
  <id>{_J}</id>
  <exit_weight_lb>0</exit_weight_lb>
</jumper>
""".encode()
        element = parse(xml)
        with pytest.raises(XMLError):
            validate(element)

    def test_negative_exit_weight_fails_xsd(self):
        ns = SCHEMA_NAMESPACE_V1
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<jumper xmlns="{ns}">
  <id>{_J}</id>
  <exit_weight_lb>-200</exit_weight_lb>
</jumper>
""".encode()
        element = parse(xml)
        with pytest.raises(XMLError):
            validate(element)

    def test_missing_exit_weight_fails_xsd(self):
        ns = SCHEMA_NAMESPACE_V1
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<jumper xmlns="{ns}">
  <id>{_J}</id>
</jumper>
""".encode()
        element = parse(xml)
        with pytest.raises(XMLError):
            validate(element)

    def test_name_too_long_fails_xsd(self):
        # 121 chars exceeds the JumperName simpleType's max.
        ns = SCHEMA_NAMESPACE_V1
        long_name = "a" * 121
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<jumper xmlns="{ns}">
  <id>{_J}</id>
  <name>{long_name}</name>
  <exit_weight_lb>200</exit_weight_lb>
</jumper>
""".encode()
        element = parse(xml)
        with pytest.raises(XMLError):
            validate(element)

    def test_invalid_uuid_fails_xsd(self):
        # The UUID simpleType pattern enforces v4. A v1 (timestamp-
        # based) UUID has '1' as the version nibble and should fail.
        ns = SCHEMA_NAMESPACE_V1
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<jumper xmlns="{ns}">
  <id>00000000-0000-1000-8000-000000000000</id>
  <exit_weight_lb>200</exit_weight_lb>
</jumper>
""".encode()
        element = parse(xml)
        with pytest.raises(XMLError):
            validate(element)
