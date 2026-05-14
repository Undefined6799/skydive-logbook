"""End-to-end XML round-trip + XSD validation for the Dropzone model (D44).

Mirrors the structure of test_xml_roundtrip.py for jumps:
  Dropzone → serialize → parse → XSD-validate → Dropzone == original
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from backend.models.common import SCHEMA_NAMESPACE_V1
from backend.models.dropzone import Dropzone, DropzoneAircraft, Environment
from backend.xml.serialize import (
    dropzone_to_bytes,
    dropzone_to_element,
    element_to_dropzone,
)
from backend.xml.validator import parse, validate


class TestDropzoneRoundTrip:
    def test_minimal_dropzone_roundtrips(self):
        # The smallest legal dropzone — no province, no notes, no
        # timestamps. Mirrors a quick-add from the LogJumpModal
        # "Quick-add new DZ" flow (D44 §UI integration).
        original = Dropzone(
            name="Skydive Elsinore",
            city="Lake Elsinore",
            country="US",
            environment=Environment.DUST_SAND_SALT,
        )
        raw = dropzone_to_bytes(original)
        element = parse(raw)
        validate(element)  # picks schema by namespace
        restored = element_to_dropzone(element)
        assert restored == original

    def test_full_dropzone_roundtrips(self):
        # Every optional field set. D44's full record shape.
        original = Dropzone(
            id=uuid4(),
            name="Parachutisme Adrénaline",
            city="Saint-Jérôme",
            province="QC",
            country="CA",
            environment=Environment.CLEAN_GRASS,
            notes="Hometown DZ. Great cafeteria.",
            created_at="2026-04-27T10:00:00.000Z",
            updated_at="2026-04-27T10:00:00.000Z",
        )
        element = dropzone_to_element(original)
        validate(element)
        restored = element_to_dropzone(element)
        assert restored == original

    def test_unicode_name_and_city_roundtrip(self):
        # NFC-normalized unicode in name and city must round-trip
        # byte-for-byte through serialize → XSD validate → parse.
        # Mirrors test_title_unicode_roundtrips for jumps.
        original = Dropzone(
            name="Saut en parachute — Québec",
            city="Saint-Jérôme",
            country="CA",
            environment=Environment.CLEAN_GRASS,
        )
        raw = dropzone_to_bytes(original)
        validate(parse(raw))
        restored = element_to_dropzone(parse(raw))
        assert restored.name == "Saut en parachute — Québec"
        assert restored.city == "Saint-Jérôme"

    def test_each_environment_value_roundtrips(self):
        # All three closed-enum values must serialize and validate.
        # Adding a fourth value to the table is a contract change
        # per D45 — this test will need updating with that change.
        for env in (
            Environment.CLEAN_GRASS,
            Environment.DUST_SAND_SALT,
            Environment.DESERT,
        ):
            original = Dropzone(
                name=f"Test DZ {env.value}",
                city="Anywhere",
                country="US",
                environment=env,
            )
            element = dropzone_to_element(original)
            validate(element)
            assert element_to_dropzone(element).environment is env

    def test_province_absent_when_none(self):
        # Optional field — must not emit when None so a hand-crafted
        # DZ without a province round-trips byte-stable.
        original = Dropzone(
            name="Lithuania DZ",
            city="Vilnius",
            country="LT",
            environment=Environment.CLEAN_GRASS,
        )
        raw = dropzone_to_bytes(original)
        assert b"<province>" not in raw
        restored = element_to_dropzone(parse(raw))
        assert restored.province is None


class TestDropzoneXSDContract:
    """Invariants the XSD is supposed to enforce on dropzones."""

    def test_invalid_country_code_lowercase_fails_xsd(self):
        # CountryCode pattern is [A-Z]{2} — lowercase must fail.
        ns = SCHEMA_NAMESPACE_V1
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<dropzone xmlns="{ns}">
  <id>11111111-1111-4111-8111-111111111111</id>
  <name>Test</name>
  <city>Anywhere</city>
  <country>ca</country>
  <environment>clean_grass</environment>
</dropzone>
'''.encode()
        element = parse(xml)
        try:
            validate(element)
        except Exception:
            pass
        else:
            raise AssertionError("expected XSD to reject lowercase country code")

    def test_invalid_country_code_three_letters_fails_xsd(self):
        # Alpha-3 codes (CAN, USA) are wrong — alpha-2 only.
        ns = SCHEMA_NAMESPACE_V1
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<dropzone xmlns="{ns}">
  <id>11111111-1111-4111-8111-111111111111</id>
  <name>Test</name>
  <city>Anywhere</city>
  <country>CAN</country>
  <environment>clean_grass</environment>
</dropzone>
'''.encode()
        element = parse(xml)
        try:
            validate(element)
        except Exception:
            pass
        else:
            raise AssertionError("expected XSD to reject alpha-3 country code")

    def test_unknown_environment_fails_xsd(self):
        ns = SCHEMA_NAMESPACE_V1
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<dropzone xmlns="{ns}">
  <id>11111111-1111-4111-8111-111111111111</id>
  <name>Test</name>
  <city>Anywhere</city>
  <country>US</country>
  <environment>tropical</environment>
</dropzone>
'''.encode()
        element = parse(xml)
        try:
            validate(element)
        except Exception:
            pass
        else:
            raise AssertionError("expected XSD to reject unknown environment value")

    def test_missing_required_field_fails_xsd(self):
        # Missing <country> — all of name/city/country/environment
        # are required by the schema.
        ns = SCHEMA_NAMESPACE_V1
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<dropzone xmlns="{ns}">
  <id>11111111-1111-4111-8111-111111111111</id>
  <name>Test</name>
  <city>Anywhere</city>
  <environment>clean_grass</environment>
</dropzone>
'''.encode()
        element = parse(xml)
        try:
            validate(element)
        except Exception:
            pass
        else:
            raise AssertionError("expected XSD to reject missing country")


class TestDropzonePydantic:
    """Pydantic-side validation of the Dropzone model."""

    def test_lowercase_country_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Dropzone(
                name="Test",
                city="Anywhere",
                country="ca",  # lowercase — pattern requires [A-Z]{2}
                environment=Environment.CLEAN_GRASS,
            )

    def test_unknown_environment_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Dropzone(
                name="Test",
                city="Anywhere",
                country="US",
                environment="tropical",  # not a member of Environment
            )

    def test_extra_field_forbidden(self):
        # extra="forbid" on the model — sending an unknown field is
        # a 422 at the API layer.
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Dropzone(
                name="Test",
                city="Anywhere",
                country="US",
                environment=Environment.CLEAN_GRASS,
                bogus_field="anything",
            )


# --------------------------------------------------------------------------- #
# D44 aircraft list (added 2026-04-28)
# --------------------------------------------------------------------------- #

class TestDropzoneAircraft:
    """Free-text fleet list — model required, tail_number optional,
    0..n entries. The element is omitted entirely when the list is
    empty so a hand-crafted XML without the addition round-trips
    byte-stable.
    """

    def test_empty_list_omits_element(self):
        # Default empty list ↔ no <aircraft> element on the wire.
        dz = Dropzone(
            name="Bare",
            city="Anywhere",
            country="US",
            environment=Environment.CLEAN_GRASS,
        )
        raw = dropzone_to_bytes(dz)
        assert b"<aircraft>" not in raw
        validate(parse(raw))
        restored = element_to_dropzone(parse(raw))
        assert restored.aircraft == []

    def test_single_plane_with_tail_number_roundtrips(self):
        dz = Dropzone(
            name="Hometown DZ",
            city="Anywhere",
            country="CA",
            environment=Environment.CLEAN_GRASS,
            aircraft=[
                DropzoneAircraft(model="Twin Otter", tail_number="C-FXYZ"),
            ],
        )
        element = dropzone_to_element(dz)
        validate(element)
        restored = element_to_dropzone(element)
        assert restored == dz

    def test_plane_without_tail_number_roundtrips(self):
        # tail_number is optional — must round-trip as None when
        # absent and not be emitted on serialize.
        dz = Dropzone(
            name="DZ",
            city="Anywhere",
            country="US",
            environment=Environment.CLEAN_GRASS,
            aircraft=[DropzoneAircraft(model="Cessna 208 Caravan")],
        )
        raw = dropzone_to_bytes(dz)
        assert b"<tail_number>" not in raw
        validate(parse(raw))
        restored = element_to_dropzone(parse(raw))
        assert len(restored.aircraft) == 1
        assert restored.aircraft[0].model == "Cessna 208 Caravan"
        assert restored.aircraft[0].tail_number is None

    def test_mixed_fleet_preserves_order(self):
        # Multiple planes: order is meaningful (UI lists them in
        # insertion order). Mix tail-number and bare entries.
        dz = Dropzone(
            name="Big DZ",
            city="Eloy",
            country="US",
            environment=Environment.DESERT,
            aircraft=[
                DropzoneAircraft(model="Skyvan", tail_number="N57VAN"),
                DropzoneAircraft(model="Twin Otter"),
                DropzoneAircraft(model="Pilatus PC-12", tail_number="N123PC"),
            ],
        )
        element = dropzone_to_element(dz)
        validate(element)
        restored = element_to_dropzone(element)
        assert [p.model for p in restored.aircraft] == [
            "Skyvan",
            "Twin Otter",
            "Pilatus PC-12",
        ]
        assert restored.aircraft[1].tail_number is None
        assert restored.aircraft[0].tail_number == "N57VAN"
        assert restored == dz

    def test_unicode_in_model_and_tail(self):
        # Tail number registries are ASCII-only in practice, but
        # model could carry a localized name. Both must survive NFC.
        dz = Dropzone(
            name="DZ",
            city="Anywhere",
            country="FR",
            environment=Environment.CLEAN_GRASS,
            aircraft=[
                DropzoneAircraft(model="Pilatus Porter — PC-6", tail_number="F-GABC"),
            ],
        )
        validate(parse(dropzone_to_bytes(dz)))
        restored = element_to_dropzone(parse(dropzone_to_bytes(dz)))
        assert restored.aircraft[0].model == "Pilatus Porter — PC-6"

    def test_xsd_rejects_empty_model(self):
        # XSD min=1 on <model> — a hand-crafted file with an empty
        # value must fail validation.
        ns = SCHEMA_NAMESPACE_V1
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<dropzone xmlns="{ns}">
  <id>11111111-1111-4111-8111-111111111111</id>
  <name>x</name>
  <city>y</city>
  <country>US</country>
  <environment>clean_grass</environment>
  <aircraft><plane><model></model></plane></aircraft>
</dropzone>
'''.encode()
        element = parse(xml)
        try:
            validate(element)
        except Exception:
            pass
        else:
            raise AssertionError("expected XSD to reject empty <model>")

    def test_pydantic_rejects_unknown_aircraft_field(self):
        # extra="forbid" on DropzoneAircraft.
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            DropzoneAircraft(model="Twin Otter", capacity=22)
