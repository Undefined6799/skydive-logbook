"""End-to-end XML round-trip + XSD validation (D2).

These tests exercise the full pipeline without touching disk or services:
  Jump → serialize → parse → XSD-validate → Jump  ==  original
"""
from __future__ import annotations

from datetime import date, time
from uuid import uuid4

from backend.models.jump import Attachment, Jump
from backend.xml.serialize import element_to_jump, jump_to_bytes, jump_to_element
from backend.xml.validator import parse, validate


class TestJumpRoundTrip:
    def test_minimal_jump_roundtrips(self):
        original = Jump(
            id=uuid4(),
            jump_number=1,
            date=date(2026, 4, 22),
            dropzone="Elsinore",
            exit_altitude_m=4000,
            deployment_altitude_m=900,
        )
        raw = jump_to_bytes(original)
        element = parse(raw)
        validate(element)  # picks schema by namespace
        restored = element_to_jump(element)
        assert restored == original

    def test_title_roundtrips_when_present(self):
        # D4 (revised 2026-04-23): <title> is an optional element on
        # <jump> capped at 120 chars. It must survive the full
        # serialize → parse → validate → deserialize loop byte-for-byte.
        original = Jump(
            id=uuid4(),
            jump_number=851,
            title="First 4-way of the season",
            date=date(2026, 4, 22),
            dropzone="Elsinore",
            exit_altitude_m=4000,
            deployment_altitude_m=900,
        )
        raw = jump_to_bytes(original)
        validate(parse(raw))
        restored = element_to_jump(parse(raw))
        assert restored.title == "First 4-way of the season"
        assert restored == original

    def test_title_unicode_roundtrips(self):
        # Unicode is permitted in titles (D4 revised 2026-04-23).
        # NFC-normalized input must round-trip without corruption
        # across serialize → XSD validate → parse.
        original = Jump(
            id=uuid4(),
            jump_number=2,
            title="Première chute 🪂 — accent & emoji",
            date=date(2026, 4, 23),
            dropzone="Elsinore",
            exit_altitude_m=4000,
            deployment_altitude_m=900,
        )
        validate(parse(jump_to_bytes(original)))
        restored = element_to_jump(parse(jump_to_bytes(original)))
        assert restored.title == "Première chute 🪂 — accent & emoji"

    def test_title_absent_by_default(self):
        # Minimal Jump (no title arg) serializes without a <title>
        # element and round-trips with title=None.
        original = Jump(
            id=uuid4(),
            jump_number=3,
            date=date(2026, 4, 22),
            dropzone="Elsinore",
            exit_altitude_m=4000,
            deployment_altitude_m=900,
        )
        raw = jump_to_bytes(original)
        # No <title> element emitted when absent.
        assert b"<title>" not in raw
        restored = element_to_jump(parse(raw))
        assert restored.title is None

    def test_full_jump_roundtrips(self, sample_jump: Jump):
        raw = jump_to_bytes(sample_jump)
        element = parse(raw)
        validate(element)
        restored = element_to_jump(element)
        assert restored == sample_jump

    def test_time_of_day_and_timezone(self):
        original = Jump(
            id=uuid4(),
            jump_number=42,
            date=date(2026, 4, 22),
            time=time(14, 30),
            timezone="America/Los_Angeles",
            dropzone="Elsinore",
            exit_altitude_m=4000,
            deployment_altitude_m=900,
        )
        element = jump_to_element(original)
        validate(element)
        assert element_to_jump(element) == original

    def test_attachments_preserved(self):
        original = Jump(
            id=uuid4(),
            jump_number=42,
            date=date(2026, 4, 22),
            dropzone="Elsinore",
            exit_altitude_m=4000,
            deployment_altitude_m=900,
            attachments=[
                Attachment(filename="flysight.csv", sha256="b" * 64, size=12345, content_type="text/csv"),
                Attachment(filename="video.mp4", sha256="c" * 64, size=999999),
            ],
        )
        element = jump_to_element(original)
        validate(element)
        assert element_to_jump(element) == original


class TestXSDContract:
    """Invariants the XSD is supposed to enforce."""

    def test_negative_altitude_fails_xsd(self):
        # Build an invalid element by hand (Pydantic would reject it first,
        # so we bypass the model).

        from backend.models.common import SCHEMA_NAMESPACE_V1
        ns = SCHEMA_NAMESPACE_V1
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<jump xmlns="{ns}">
  <id>11111111-1111-4111-8111-111111111111</id>
  <jump_number>1</jump_number>
  <date>2026-04-22</date>
  <dropzone>Elsinore</dropzone>
  <exit_altitude_m>-1</exit_altitude_m>
  <deployment_altitude_m>900</deployment_altitude_m>
</jump>
'''.encode()
        element = parse(xml)
        try:
            validate(element)
        except Exception as e:
            assert "validation" in str(e).lower() or "altitude" in str(e).lower() or "negative" in str(e).lower()
        else:
            raise AssertionError("expected XSD validation to reject negative altitude")

    def test_missing_required_field_fails_xsd(self):
        from backend.models.common import SCHEMA_NAMESPACE_V1
        ns = SCHEMA_NAMESPACE_V1
        # Missing <dropzone>
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<jump xmlns="{ns}">
  <id>11111111-1111-4111-8111-111111111111</id>
  <jump_number>1</jump_number>
  <date>2026-04-22</date>
  <exit_altitude_m>4000</exit_altitude_m>
  <deployment_altitude_m>900</deployment_altitude_m>
</jump>
'''.encode()
        element = parse(xml)
        try:
            validate(element)
        except Exception:
            pass
        else:
            raise AssertionError("expected XSD validation to reject missing dropzone")


class TestD32Timestamps:
    """D32: created_at / updated_at round-trip through jump.xml."""

    def test_timestamps_roundtrip(self):
        # Both fields on the Jump → written into XML → parsed back
        # onto the Jump. Round-trip equality proves serialize ↔ parse
        # agree, and validate() proves the XSD accepts the D17 form.
        original = Jump(
            id=uuid4(),
            jump_number=1,
            date=date(2026, 4, 22),
            dropzone="Elsinore",
            exit_altitude_m=4000,
            deployment_altitude_m=900,
            created_at="2026-04-23T18:45:03.127Z",
            updated_at="2026-04-23T19:12:44.002Z",
        )
        element = jump_to_element(original)
        validate(element)
        assert element_to_jump(element) == original

    def test_timestamps_optional(self):
        # D32 landed additively per D18: files without the elements
        # must still validate. A Jump without timestamps serializes
        # to XML without <created_at> / <updated_at>, and that XML
        # passes XSD validation (optional elements).
        original = Jump(
            id=uuid4(),
            jump_number=1,
            date=date(2026, 4, 22),
            dropzone="Elsinore",
            exit_altitude_m=4000,
            deployment_altitude_m=900,
        )
        element = jump_to_element(original)
        validate(element)
        # Neither element emitted.
        ns = "{https://skydive-logbook.org/schema/v1}"
        assert element.find(f"{ns}created_at") is None
        assert element.find(f"{ns}updated_at") is None
        restored = element_to_jump(element)
        assert restored.created_at is None
        assert restored.updated_at is None

    def test_timestamps_only_updated_at_set_validates(self):
        # Pathological-but-legal: only one of the two timestamps is
        # on the Jump. The XSD permits both independently, so this
        # must round-trip cleanly without forcing both-or-neither.
        original = Jump(
            id=uuid4(),
            jump_number=1,
            date=date(2026, 4, 22),
            dropzone="Elsinore",
            exit_altitude_m=4000,
            deployment_altitude_m=900,
            updated_at="2026-04-23T10:00:00.000Z",
        )
        element = jump_to_element(original)
        validate(element)
        restored = element_to_jump(element)
        assert restored.created_at is None
        assert restored.updated_at == "2026-04-23T10:00:00.000Z"


class TestAltitudePrecision:
    """Altitudes are xs:decimal on the wire (lifted from
    xs:nonNegativeInteger 2026-04-28) so unit conversion at the UI
    boundary round-trips cleanly. 13500 ft = 4114.8 m exactly; if
    we stored 4115 m the display would re-render as 13501.
    """

    def test_fractional_meters_roundtrip(self):
        # 13500 ft converts to exactly 4114.8 m. Round-trip must
        # preserve the fraction so the UI renders 13500 again.
        original = Jump(
            id=uuid4(),
            jump_number=1,
            date=date(2026, 4, 22),
            dropzone="Elsinore",
            exit_altitude_m=4114.8,
            deployment_altitude_m=914.4,  # 3000 ft
        )
        element = jump_to_element(original)
        validate(element)
        restored = element_to_jump(element)
        assert restored.exit_altitude_m == 4114.8
        assert restored.deployment_altitude_m == 914.4

    def test_integer_value_serializes_without_trailing_zero(self):
        # A whole-number altitude (4000) writes as "4000" not
        # "4000.0" so existing pre-decimal jumps don't gratuitously
        # change bytes on the next save.
        original = Jump(
            id=uuid4(),
            jump_number=1,
            date=date(2026, 4, 22),
            dropzone="Elsinore",
            exit_altitude_m=4000,
            deployment_altitude_m=900,
        )
        raw = jump_to_bytes(original)
        assert b"<exit_altitude_m>4000</exit_altitude_m>" in raw
        assert b"<deployment_altitude_m>900</deployment_altitude_m>" in raw

    def test_pre_existing_integer_xml_still_validates(self):
        # Existing on-disk jumps (written before the xs:decimal
        # amend) carry integer altitudes. Those must continue to
        # validate against the new schema and parse back as floats.
        from backend.models.common import SCHEMA_NAMESPACE_V1
        ns = SCHEMA_NAMESPACE_V1
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<jump xmlns="{ns}">
  <id>11111111-1111-4111-8111-111111111111</id>
  <jump_number>1</jump_number>
  <date>2026-04-22</date>
  <dropzone>Elsinore</dropzone>
  <exit_altitude_m>4000</exit_altitude_m>
  <deployment_altitude_m>900</deployment_altitude_m>
</jump>
'''.encode()
        element = parse(xml)
        validate(element)
        restored = element_to_jump(element)
        assert restored.exit_altitude_m == 4000.0
        assert restored.deployment_altitude_m == 900.0

    def test_negative_altitude_still_fails_xsd(self):
        # The minInclusive=0 restriction on the xs:decimal type
        # still rejects negative values — the precision lift didn't
        # weaken the non-negative invariant.
        from backend.models.common import SCHEMA_NAMESPACE_V1
        ns = SCHEMA_NAMESPACE_V1
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<jump xmlns="{ns}">
  <id>11111111-1111-4111-8111-111111111111</id>
  <jump_number>1</jump_number>
  <date>2026-04-22</date>
  <dropzone>Elsinore</dropzone>
  <exit_altitude_m>-1.5</exit_altitude_m>
  <deployment_altitude_m>900</deployment_altitude_m>
</jump>
'''.encode()
        element = parse(xml)
        try:
            validate(element)
        except Exception:
            pass
        else:
            raise AssertionError("expected XSD to reject negative altitude")


class TestD44DropzoneReference:
    """D44: <dropzone_id> on jump.xml is the structured DZ reference."""

    def test_dropzone_id_roundtrips(self):
        # When set, the UUID is written into the XML and parsed back
        # onto the model byte-for-byte. The free-text <dropzone>
        # field stays — both coexist.
        dz_id = uuid4()
        original = Jump(
            id=uuid4(),
            jump_number=1,
            date=date(2026, 4, 22),
            dropzone="Saint-Jérôme",  # free-text label
            dropzone_id=dz_id,         # structured reference
            exit_altitude_m=4000,
            deployment_altitude_m=900,
        )
        element = jump_to_element(original)
        validate(element)
        restored = element_to_jump(element)
        assert restored.dropzone_id == dz_id
        assert restored.dropzone == "Saint-Jérôme"
        assert restored == original

    def test_dropzone_id_absent_by_default(self):
        # A jump logged without picking a DZ entity (R.D.0
        # backward-compat) has no <dropzone_id> in the file and
        # restores with dropzone_id=None.
        original = Jump(
            id=uuid4(),
            jump_number=1,
            date=date(2026, 4, 22),
            dropzone="Some DZ",
            exit_altitude_m=4000,
            deployment_altitude_m=900,
        )
        raw = jump_to_bytes(original)
        assert b"<dropzone_id>" not in raw
        validate(parse(raw))
        restored = element_to_jump(parse(raw))
        assert restored.dropzone_id is None


class TestD33RigReference:
    """D33 (R.2.2-light): <rig_id> on jump.xml links to a rig under
    ``rigs/<nickname>/rig.xml``. Same shape and posture as
    ``dropzone_id`` — additive in v1, optional, absent on legacy
    jumps.
    """

    def test_rig_id_roundtrips(self):
        # When set, the UUID is written into the XML and parsed back
        # onto the model byte-for-byte.
        rig_id = uuid4()
        original = Jump(
            id=uuid4(),
            jump_number=1,
            date=date(2026, 4, 22),
            dropzone="Saint-Jérôme",
            rig_id=rig_id,
            exit_altitude_m=4000,
            deployment_altitude_m=900,
        )
        element = jump_to_element(original)
        validate(element)
        restored = element_to_jump(element)
        assert restored.rig_id == rig_id
        assert restored == original

    def test_rig_id_absent_by_default(self):
        # A jump logged without picking a rig (legacy / no-rig path)
        # has no <rig_id> in the file and restores with rig_id=None.
        original = Jump(
            id=uuid4(),
            jump_number=1,
            date=date(2026, 4, 22),
            dropzone="Some DZ",
            exit_altitude_m=4000,
            deployment_altitude_m=900,
        )
        raw = jump_to_bytes(original)
        assert b"<rig_id>" not in raw
        validate(parse(raw))
        restored = element_to_jump(parse(raw))
        assert restored.rig_id is None

    def test_rig_id_emits_after_dropzone_id(self):
        # The XSD sequence puts <rig_id> after <dropzone_id> and
        # before <environment>. A reordered emit would fail XSD
        # validation; this test pins the wire ordering directly.
        original = Jump(
            id=uuid4(),
            jump_number=1,
            date=date(2026, 4, 22),
            dropzone="Some DZ",
            dropzone_id=uuid4(),
            rig_id=uuid4(),
            exit_altitude_m=4000,
            deployment_altitude_m=900,
        )
        raw = jump_to_bytes(original)
        # dropzone_id comes before rig_id in the byte stream.
        dz_pos = raw.index(b"<dropzone_id>")
        rig_pos = raw.index(b"<rig_id>")
        assert dz_pos < rig_pos

    def test_rig_id_with_dropzone_id_both_round_trip(self):
        dz_id = uuid4()
        rig_id = uuid4()
        original = Jump(
            id=uuid4(),
            jump_number=1,
            date=date(2026, 4, 22),
            dropzone="Some DZ",
            dropzone_id=dz_id,
            rig_id=rig_id,
            exit_altitude_m=4000,
            deployment_altitude_m=900,
        )
        element = jump_to_element(original)
        validate(element)
        restored = element_to_jump(element)
        assert restored.dropzone_id == dz_id
        assert restored.rig_id == rig_id


class TestD45JumpEnvironmentOverride:
    """D45 / D57: ``<packed_in_poor_conditions>`` round-trip.

    The per-jump ``<environment>`` override that D45 originally
    pinned was removed by D57; the related tests have been deleted.
    The packing-conditions flag is the surviving D45 contribution
    to the jump model.
    """

    def test_packed_in_poor_conditions_true_roundtrips(self):
        original = Jump(
            id=uuid4(),
            jump_number=1,
            date=date(2026, 4, 22),
            dropzone="Elsinore",
            packed_in_poor_conditions=True,
            exit_altitude_m=4000,
            deployment_altitude_m=900,
        )
        element = jump_to_element(original)
        validate(element)
        restored = element_to_jump(element)
        assert restored.packed_in_poor_conditions is True

    def test_packed_in_poor_conditions_false_roundtrips(self):
        # False is meaningful: the user explicitly said "I packed in
        # clean conditions" and the file should record that — not
        # collapse to "absent" which means "unknown".
        original = Jump(
            id=uuid4(),
            jump_number=1,
            date=date(2026, 4, 22),
            dropzone="Elsinore",
            packed_in_poor_conditions=False,
            exit_altitude_m=4000,
            deployment_altitude_m=900,
        )
        raw = jump_to_bytes(original)
        assert b"<packed_in_poor_conditions>false</packed_in_poor_conditions>" in raw
        validate(parse(raw))
        restored = element_to_jump(parse(raw))
        assert restored.packed_in_poor_conditions is False

    def test_packed_in_poor_conditions_absent_when_none(self):
        # None ≡ "unknown" / "not stated". Must not emit the element
        # so a hand-crafted file without it survives byte-stable.
        original = Jump(
            id=uuid4(),
            jump_number=1,
            date=date(2026, 4, 22),
            dropzone="Elsinore",
            exit_altitude_m=4000,
            deployment_altitude_m=900,
        )
        raw = jump_to_bytes(original)
        assert b"<packed_in_poor_conditions>" not in raw
        restored = element_to_jump(parse(raw))
        assert restored.packed_in_poor_conditions is None

    def test_full_d44_d45_combo_roundtrips(self):
        # The realistic case for a jumper who picks a DZ and flags
        # packing conditions. D57 removed the per-jump environment
        # override; the DZ alone now carries the environment value
        # that drives wear math.
        original = Jump(
            id=uuid4(),
            jump_number=42,
            date=date(2026, 4, 22),
            dropzone="Eloy",
            dropzone_id=uuid4(),
            packed_in_poor_conditions=True,
            exit_altitude_m=4000,
            deployment_altitude_m=900,
        )
        element = jump_to_element(original)
        validate(element)
        restored = element_to_jump(element)
        assert restored == original
