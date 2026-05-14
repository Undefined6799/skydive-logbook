"""End-to-end XML round-trip + XSD validation for Container (R.0.2b).

Pipeline mirrors the Jump and Dropzone round-trip tests:
  Container → serialize → parse → XSD-validate → Container  ==  original

Also pins a few invariants:
  * Optional identification fields elide cleanly when None.
  * The shared ComponentBaseFields group emits in the right order
    relative to the kind-specific fields (XSD validation is the
    enforcer; the test exercises every branch of the emit).
  * jump_count_initial is always emitted (even when zero) because
    D35's projection layer needs a deterministic seed.
"""
from __future__ import annotations

from datetime import date
from uuid import uuid4

from backend.models._component_base import ComponentStatus, NotesLogEntry
from backend.models.container import Container
from backend.xml.serialize import (
    container_to_bytes,
    container_to_element,
    element_to_container,
)
from backend.xml.validator import parse, validate


class TestContainerRoundTrip:
    def test_minimal_container_roundtrips(self):
        # Bare-minimum onboarding: just the universal id + status +
        # a zero jump_count_initial. Everything else is None / empty.
        original = Container(jump_count_initial=0)
        raw = container_to_bytes(original)
        element = parse(raw)
        validate(element)
        restored = element_to_container(element)
        assert restored == original

    def test_full_field_set_roundtrips(self):
        original = Container(
            id=uuid4(),
            status=ComponentStatus.ACTIVE,
            assigned_rig_id=uuid4(),
            notes_log=[
                NotesLogEntry(
                    at="2026-04-28T14:30:00.000Z",
                    text="Onboarded — bought used from Bob",
                ),
                NotesLogEntry(
                    at="2026-04-29T08:15:00.000Z",
                    text="Inspection clean",
                ),
            ],
            manufacturer="Sun Path",
            model="Javelin Odyssey",
            serial="OD-12345",
            size="M22",
            date_of_manufacture=date(2018, 6, 15),
            jump_count_initial=750,
            created_at="2026-04-28T14:30:00.000Z",
            updated_at="2026-04-29T08:15:00.000Z",
        )
        element = container_to_element(original)
        validate(element)
        assert element_to_container(element) == original

    def test_optional_identification_fields_elide_when_none(self):
        # A used-gear container with unknown manufacturer / DOM still
        # has to round-trip. The serializer elides the optional
        # elements; the parser reads them as None.
        original = Container(jump_count_initial=200)
        raw = container_to_bytes(original)
        # No optional kind-specific elements emitted.
        for absent in (
            b"<manufacturer>",
            b"<model>",
            b"<serial>",
            b"<size>",
            b"<date_of_manufacture>",
        ):
            assert absent not in raw, f"unexpected element {absent!r} in output"
        # Round-trip still equal.
        restored = element_to_container(parse(raw))
        assert restored == original

    def test_assigned_rig_id_optional(self):
        # None on the model means the wrapper element is absent in
        # the output.
        original = Container(jump_count_initial=0)
        raw = container_to_bytes(original)
        assert b"<assigned_rig_id>" not in raw

    def test_assigned_rig_id_present_when_set(self):
        rig_id = uuid4()
        original = Container(jump_count_initial=0, assigned_rig_id=rig_id)
        raw = container_to_bytes(original)
        assert f"<assigned_rig_id>{rig_id}</assigned_rig_id>".encode() in raw
        restored = element_to_container(parse(raw))
        assert restored.assigned_rig_id == rig_id

    def test_empty_notes_log_elides_wrapper(self):
        # Same posture as DropzoneAircraft (R.D.6) — empty list
        # means the wrapper element is absent. Hand-crafted files
        # that do not write the element round-trip byte-stable.
        original = Container(jump_count_initial=0, notes_log=[])
        raw = container_to_bytes(original)
        assert b"<notes_log>" not in raw

    def test_notes_log_preserves_order(self):
        entries = [
            NotesLogEntry(at=f"2026-04-{day:02d}T10:00:00.000Z", text=f"day {day}")
            for day in (1, 2, 3, 4, 5)
        ]
        original = Container(jump_count_initial=0, notes_log=entries)
        element = container_to_element(original)
        validate(element)
        restored = element_to_container(element)
        # Order is load-bearing: the log is append-only, so reordering
        # would silently rewrite history.
        assert restored.notes_log == entries

    def test_jump_count_initial_zero_still_emits(self):
        # D35: the projection layer uses jump_count_initial as a
        # deterministic seed. A missing element would force "is it
        # zero or unknown?" guesswork on every reindex.
        original = Container(jump_count_initial=0)
        raw = container_to_bytes(original)
        assert b"<jump_count_initial>0</jump_count_initial>" in raw

    def test_used_gear_starting_count_roundtrips(self):
        original = Container(jump_count_initial=1234)
        element = container_to_element(original)
        validate(element)
        restored = element_to_container(element)
        assert restored.jump_count_initial == 1234

    def test_status_retired_validates(self):
        # Pin every ComponentStatus value through XSD validation.
        for s in ComponentStatus:
            c = Container(jump_count_initial=0, status=s)
            element = container_to_element(c)
            validate(element)  # raises XMLError on bad enum value


class TestContainerXSDContract:
    """XSD-side invariants this slice introduces."""

    def test_negative_jump_count_initial_fails_xsd(self):
        # Build the XML by hand to bypass Pydantic — we want to
        # exercise the XSD constraint specifically.
        from backend.models.common import SCHEMA_NAMESPACE_V1
        from backend.xml.validator import XMLError, parse, validate
        ns = SCHEMA_NAMESPACE_V1
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<container xmlns="{ns}">
  <id>11111111-1111-4111-8111-111111111111</id>
  <status>active</status>
  <jump_count_initial>-5</jump_count_initial>
</container>
""".encode()
        element = parse(xml)
        try:
            validate(element)
        except XMLError:
            return
        raise AssertionError("XSD did not reject negative jump_count_initial")

    def test_unknown_status_fails_xsd(self):
        from backend.models.common import SCHEMA_NAMESPACE_V1
        from backend.xml.validator import XMLError, parse, validate
        ns = SCHEMA_NAMESPACE_V1
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<container xmlns="{ns}">
  <id>11111111-1111-4111-8111-111111111111</id>
  <status>halfway_retired</status>
  <jump_count_initial>0</jump_count_initial>
</container>
""".encode()
        element = parse(xml)
        try:
            validate(element)
        except XMLError:
            return
        raise AssertionError("XSD did not reject unknown status enum value")
