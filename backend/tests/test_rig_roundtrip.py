"""End-to-end XML round-trip + XSD validation for Rig (R.2.0a).

Pipeline mirrors the per-component round-trips from R.0.2:
    Rig → serialize → parse → XSD-validate → Rig  ==  original

Pin rig-specific shape:
  * Four current_*_id refs are required at create time per D37.
  * repack_history empty-list elides the wrapper element.
  * notes_log empty-list elides the wrapper element.
  * jurisdiction is a closed enum (USPA | CSPA | both) at the XSD.
  * Rig is NOT a component — has no status, no assigned_rig_id.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from backend.models._component_base import NotesLogEntry
from backend.models.common import SCHEMA_NAMESPACE_V1
from backend.models.rig import (
    Jurisdiction,
    RepackEntry,
    Rig,
    RigCreate,
    RigUpdate,
)
from backend.xml.serialize import (
    element_to_rig,
    rig_to_bytes,
    rig_to_element,
)
from backend.xml.validator import XMLError, parse, validate

# A fixed, valid set of UUIDv4 strings reused throughout the tests so
# the literal-XML negatives in TestRigXSDContract can declare them.
_M = "11111111-1111-4111-8111-111111111111"
_R = "22222222-2222-4222-8222-222222222222"
_A = "33333333-3333-4333-8333-333333333333"
_C = "44444444-4444-4444-8444-444444444444"


def _minimal_rig(**overrides) -> Rig:
    """Build a minimal Rig with the four required component refs.

    Tests can override individual fields; what stays constant is the
    "smallest valid Rig" shape so the reader sees the irreducible
    requirements at the call site.
    """
    base = dict(
        nickname="Black Cobra",
        jurisdiction=Jurisdiction.USPA,
        current_main_id=UUID(_M),
        current_reserve_id=UUID(_R),
        current_aad_id=UUID(_A),
        current_container_id=UUID(_C),
    )
    base.update(overrides)
    return Rig(**base)


class TestRigRoundTrip:
    def test_minimal_rig_roundtrips(self):
        original = _minimal_rig()
        raw = rig_to_bytes(original)
        element = parse(raw)
        validate(element)
        assert element_to_rig(element) == original

    def test_full_field_set_roundtrips(self):
        original = _minimal_rig(
            id=uuid4(),
            jurisdiction=Jurisdiction.BOTH,
            repack_history=[
                RepackEntry(
                    date=date(2025, 11, 1),
                    rigger="Jean Dupont",
                    jurisdiction_seal=Jurisdiction.USPA,
                    notes="Annual inspection, new closing loop",
                ),
                RepackEntry(
                    date=date(2026, 4, 28),
                    rigger="Jean Dupont",
                    jurisdiction_seal=Jurisdiction.BOTH,
                ),
            ],
            notes_log=[
                NotesLogEntry(
                    at="2026-04-28T14:30:00.000Z",
                    text="Used purchase, prior owner had it 4 years",
                ),
            ],
            created_at="2026-04-28T14:30:00.000Z",
            updated_at="2026-04-28T14:30:00.000Z",
        )
        element = rig_to_element(original)
        validate(element)
        assert element_to_rig(element) == original

    def test_empty_repack_history_elides_wrapper(self):
        original = _minimal_rig(repack_history=[])
        raw = rig_to_bytes(original)
        assert b"<repack_history>" not in raw

    def test_empty_notes_log_elides_wrapper(self):
        original = _minimal_rig(notes_log=[])
        raw = rig_to_bytes(original)
        assert b"<notes_log>" not in raw

    def test_repack_history_preserves_order(self):
        # Order matters for the "latest entry drives the clock" rule
        # (D38). A round-trip that reordered them would silently
        # break the next-repack-due math.
        entries = [
            RepackEntry(
                date=date(2024, m, 1),
                rigger=f"Rigger {m}",
                jurisdiction_seal=Jurisdiction.USPA,
            )
            for m in (3, 6, 9, 12)
        ]
        original = _minimal_rig(repack_history=entries)
        element = rig_to_element(original)
        validate(element)
        restored = element_to_rig(element)
        assert restored.repack_history == entries

    def test_repack_entry_with_minimal_fields_roundtrips(self):
        # notes is optional; absence must round-trip as None.
        entry = RepackEntry(
            date=date(2025, 6, 15),
            rigger="J. Smith",
            jurisdiction_seal=Jurisdiction.CSPA,
        )
        original = _minimal_rig(repack_history=[entry])
        raw = rig_to_bytes(original)
        # The optional <notes> child elides on emit.
        assert b"<notes>" not in raw
        restored = element_to_rig(parse(raw))
        assert restored.repack_history == [entry]

    def test_unicode_in_nickname_roundtrips(self):
        # D4 dropped the ASCII-only rule pre-launch; nickname is free
        # Unicode bounded only by length.
        original = _minimal_rig(nickname="Élise — vol 1 ✈")
        element = rig_to_element(original)
        validate(element)
        assert element_to_rig(element) == original

    def test_unicode_in_rigger_name_roundtrips(self):
        entry = RepackEntry(
            date=date(2025, 6, 15),
            rigger="François Béland",
            jurisdiction_seal=Jurisdiction.CSPA,
        )
        original = _minimal_rig(repack_history=[entry])
        element = rig_to_element(original)
        validate(element)
        assert element_to_rig(element) == original

    def test_each_jurisdiction_value_validates(self):
        for j in Jurisdiction:
            r = _minimal_rig(jurisdiction=j)
            element = rig_to_element(r)
            validate(element)

    def test_each_jurisdiction_seal_value_validates(self):
        # Repack entry's jurisdiction_seal reuses the same enum;
        # confirm every value passes XSD validation.
        for seal in Jurisdiction:
            entry = RepackEntry(
                date=date(2025, 6, 15),
                rigger="Test",
                jurisdiction_seal=seal,
            )
            r = _minimal_rig(repack_history=[entry])
            element = rig_to_element(r)
            validate(element)

    def test_d32_timestamps_preserved_through_roundtrip(self):
        original = _minimal_rig(
            created_at="2026-04-28T10:00:00.123Z",
            updated_at="2026-04-28T14:30:00.456Z",
        )
        element = rig_to_element(original)
        validate(element)
        restored = element_to_rig(element)
        assert restored.created_at == "2026-04-28T10:00:00.123Z"
        assert restored.updated_at == "2026-04-28T14:30:00.456Z"

    def test_all_four_component_refs_present_in_xml(self):
        original = _minimal_rig()
        raw = rig_to_bytes(original)
        for tag in (
            b"<current_main_id>",
            b"<current_reserve_id>",
            b"<current_aad_id>",
            b"<current_container_id>",
        ):
            assert tag in raw, f"missing {tag!r} in rig.xml output"


class TestRigPydanticContract:
    """Pin the shape at the Pydantic layer.

    XSD enforces the on-disk shape; Pydantic enforces the Python-API
    shape. Both layers must agree, but Pydantic's ``extra="forbid"``
    is the firewall against typos in the application code.
    """

    def test_rig_rejects_unknown_field(self):
        with pytest.raises(ValidationError):
            Rig(
                nickname="X",
                jurisdiction=Jurisdiction.USPA,
                current_main_id=UUID(_M),
                current_reserve_id=UUID(_R),
                current_aad_id=UUID(_A),
                current_container_id=UUID(_C),
                status="active",  # type: ignore[call-arg]
            )

    def test_rig_rejects_assigned_rig_id_field(self):
        # D34 is explicit: rigs are NOT components. They do not carry
        # assigned_rig_id; a future drift that adds it would surface
        # here as a model construction failure.
        with pytest.raises(ValidationError):
            Rig(
                nickname="X",
                jurisdiction=Jurisdiction.USPA,
                current_main_id=UUID(_M),
                current_reserve_id=UUID(_R),
                current_aad_id=UUID(_A),
                current_container_id=UUID(_C),
                assigned_rig_id=UUID(_M),  # type: ignore[call-arg]
            )

    def test_rig_requires_all_four_refs(self):
        with pytest.raises(ValidationError):
            Rig(
                nickname="X",
                jurisdiction=Jurisdiction.USPA,
                current_main_id=UUID(_M),
                # missing reserve, aad, container
            )  # type: ignore[call-arg]

    def test_rig_nickname_max_length(self):
        # 120 chars OK, 121 rejected.
        ok = "a" * 120
        too_long = "a" * 121
        _minimal_rig(nickname=ok)
        with pytest.raises(ValidationError):
            _minimal_rig(nickname=too_long)

    def test_rig_nickname_empty_rejected(self):
        with pytest.raises(ValidationError):
            _minimal_rig(nickname="")

    def test_rig_create_accepts_initial_repack_history(self):
        # D38: RigCreate accepts an initial list (used-gear setup).
        c = RigCreate(
            nickname="X",
            jurisdiction=Jurisdiction.USPA,
            current_main_id=UUID(_M),
            current_reserve_id=UUID(_R),
            current_aad_id=UUID(_A),
            current_container_id=UUID(_C),
            repack_history=[
                RepackEntry(
                    date=date(2025, 11, 1),
                    rigger="J. Dupont",
                    jurisdiction_seal=Jurisdiction.USPA,
                ),
            ],
        )
        assert len(c.repack_history) == 1

    def test_rig_update_accepts_repack_history_field(self):
        # D66 narrowed D38's deferral: RigUpdate accepts a
        # ``repack_history`` field so jumpers can correct their
        # repack record via the regular edit surface without
        # hand-editing rig.xml. R.5's append + cross-component
        # side-effects flow remains deferred; this contract is
        # the metadata-only path.
        from datetime import date as _date
        u = RigUpdate(
            nickname="X",
            jurisdiction=Jurisdiction.USPA,
            current_main_id=UUID(_M),
            current_reserve_id=UUID(_R),
            current_aad_id=UUID(_A),
            current_container_id=UUID(_C),
            repack_history=[
                RepackEntry(
                    date=_date(2025, 6, 15),
                    rigger="Rigger",
                    jurisdiction_seal=Jurisdiction.USPA,
                ),
            ],
        )
        assert len(u.repack_history) == 1
        assert u.repack_history[0].date == _date(2025, 6, 15)

    def test_repack_entry_rigger_required(self):
        with pytest.raises(ValidationError):
            RepackEntry(
                date=date(2025, 6, 15),
                jurisdiction_seal=Jurisdiction.USPA,
            )  # type: ignore[call-arg]

    def test_repack_entry_rigger_max_length(self):
        with pytest.raises(ValidationError):
            RepackEntry(
                date=date(2025, 6, 15),
                rigger="x" * 121,
                jurisdiction_seal=Jurisdiction.USPA,
            )


class TestRigXSDContract:
    """Pin XSD-layer rejections that Pydantic doesn't enforce.

    These verify that the on-disk file format genuinely closes the
    enums and rejects shape errors regardless of the Python writer
    — a third-party tool emitting bad XML is rejected at parse-and-
    validate time.
    """

    def test_unknown_jurisdiction_value_fails_xsd(self):
        ns = SCHEMA_NAMESPACE_V1
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rig xmlns="{ns}">
  <id>{_M}</id>
  <nickname>X</nickname>
  <jurisdiction>FAA</jurisdiction>
  <current_main_id>{_M}</current_main_id>
  <current_reserve_id>{_R}</current_reserve_id>
  <current_aad_id>{_A}</current_aad_id>
  <current_container_id>{_C}</current_container_id>
</rig>
""".encode()
        element = parse(xml)
        with pytest.raises(XMLError):
            validate(element)

    def test_unknown_jurisdiction_seal_fails_xsd(self):
        ns = SCHEMA_NAMESPACE_V1
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rig xmlns="{ns}">
  <id>{_M}</id>
  <nickname>X</nickname>
  <jurisdiction>USPA</jurisdiction>
  <current_main_id>{_M}</current_main_id>
  <current_reserve_id>{_R}</current_reserve_id>
  <current_aad_id>{_A}</current_aad_id>
  <current_container_id>{_C}</current_container_id>
  <repack_history>
    <repack>
      <date>2025-06-15</date>
      <rigger>J. Smith</rigger>
      <jurisdiction_seal>FAA</jurisdiction_seal>
    </repack>
  </repack_history>
</rig>
""".encode()
        element = parse(xml)
        with pytest.raises(XMLError):
            validate(element)

    def test_missing_required_component_ref_fails_xsd(self):
        # All four current_*_id refs are required by the XSD; missing
        # current_container_id specifically should fail.
        ns = SCHEMA_NAMESPACE_V1
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rig xmlns="{ns}">
  <id>{_M}</id>
  <nickname>X</nickname>
  <jurisdiction>USPA</jurisdiction>
  <current_main_id>{_M}</current_main_id>
  <current_reserve_id>{_R}</current_reserve_id>
  <current_aad_id>{_A}</current_aad_id>
</rig>
""".encode()
        element = parse(xml)
        with pytest.raises(XMLError):
            validate(element)

    def test_repack_missing_rigger_fails_xsd(self):
        ns = SCHEMA_NAMESPACE_V1
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rig xmlns="{ns}">
  <id>{_M}</id>
  <nickname>X</nickname>
  <jurisdiction>USPA</jurisdiction>
  <current_main_id>{_M}</current_main_id>
  <current_reserve_id>{_R}</current_reserve_id>
  <current_aad_id>{_A}</current_aad_id>
  <current_container_id>{_C}</current_container_id>
  <repack_history>
    <repack>
      <date>2025-06-15</date>
      <jurisdiction_seal>USPA</jurisdiction_seal>
    </repack>
  </repack_history>
</rig>
""".encode()
        element = parse(xml)
        with pytest.raises(XMLError):
            validate(element)

    def test_nickname_empty_fails_xsd(self):
        ns = SCHEMA_NAMESPACE_V1
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rig xmlns="{ns}">
  <id>{_M}</id>
  <nickname></nickname>
  <jurisdiction>USPA</jurisdiction>
  <current_main_id>{_M}</current_main_id>
  <current_reserve_id>{_R}</current_reserve_id>
  <current_aad_id>{_A}</current_aad_id>
  <current_container_id>{_C}</current_container_id>
</rig>
""".encode()
        element = parse(xml)
        with pytest.raises(XMLError):
            validate(element)
