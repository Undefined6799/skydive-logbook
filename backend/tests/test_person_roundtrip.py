"""End-to-end XML round-trip + XSD validation for the Person model (D54).

Mirrors test_dropzone_roundtrip.py for the same shape:
    Person → person_to_bytes → parse → XSD-validate → Person == original

Pinned contracts (D54 §Decision):
- Minimal Person (id + name) round-trips byte-stable.
- ``notes`` is optional; absent ≡ None on the model and elides in
  XML.
- Audit timestamps elide when None and round-trip when set.
- ``<generator>`` appears on every emit (Q4 provenance) but is not
  read back onto the model — it does not affect equality.
- Names accept Unicode and round-trip byte-stable (D4 NFC).
- Empty / oversized names are rejected at the Pydantic layer.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.models.common import GENERATOR_STRING, SCHEMA_NAMESPACE_V1
from backend.models.person import Person, PersonCreate, PersonSummary, PersonUpdate
from backend.xml.serialize import (
    element_to_person,
    person_to_bytes,
    person_to_element,
)
from backend.xml.validator import parse, validate


class TestPersonRoundTrip:
    def test_minimal_person_roundtrips(self):
        # The smallest legal Person — no notes, no audit timestamps.
        # Models a quick-add from the LogJumpModal "+ new person"
        # flow that lands in Phase 2c.
        original = Person(name="Alice")
        raw = person_to_bytes(original)
        element = parse(raw)
        validate(element)  # picks schema by namespace
        restored = element_to_person(element)
        assert restored == original

    def test_full_person_roundtrips(self):
        # Every optional field set.
        original = Person(
            id=uuid4(),
            name="Bob",
            notes="Packs at Skydive City weekends.",
            created_at="2026-04-30T10:00:00.000Z",
            updated_at="2026-04-30T10:00:00.000Z",
        )
        element = person_to_element(original)
        validate(element)
        restored = element_to_person(element)
        assert restored == original

    def test_id_round_trips_exactly(self):
        # The UUID is the file-system identity; any drift would mean
        # "renaming a person creates a new on-disk file" — broken
        # rename invariant per D54 §Why.
        pid = uuid4()
        original = Person(id=pid, name="Charlie")
        restored = element_to_person(parse(person_to_bytes(original)))
        assert restored.id == pid

    def test_unicode_name_round_trips(self):
        # NFC-normalized Unicode names must survive the full pipeline
        # byte-for-byte. Mirrors test_title_unicode_roundtrips for
        # jumps and the dropzone Unicode test.
        original = Person(name="Émile Côté — rigger")
        raw = person_to_bytes(original)
        validate(parse(raw))
        restored = element_to_person(parse(raw))
        assert restored.name == "Émile Côté — rigger"

    def test_max_length_name_round_trips(self):
        # 120 chars is the Pydantic + XSD ceiling; landing exactly on
        # the cap must succeed (off-by-one regression guard).
        name_120 = "A" * 120
        original = Person(name=name_120)
        restored = element_to_person(parse(person_to_bytes(original)))
        assert restored.name == name_120
        assert len(restored.name) == 120

    def test_min_length_name_round_trips(self):
        # 1 char is the floor. Single-character names aren't typical
        # but the schema accepts them and the parser must too.
        original = Person(name="X")
        restored = element_to_person(parse(person_to_bytes(original)))
        assert restored.name == "X"


class TestPersonOptionalFields:
    def test_notes_omitted_when_none(self):
        # Optional element elides in XML so a Person without notes
        # produces a compact file without a stray empty <notes/>.
        ns = "{" + SCHEMA_NAMESPACE_V1 + "}"
        element = person_to_element(Person(name="Alice"))
        assert element.find(f"{ns}notes") is None

    def test_notes_round_trips_when_present(self):
        original = Person(name="Alice", notes="hometown rigger")
        restored = element_to_person(parse(person_to_bytes(original)))
        assert restored.notes == "hometown rigger"

    def test_timestamps_omitted_when_none(self):
        ns = "{" + SCHEMA_NAMESPACE_V1 + "}"
        element = person_to_element(Person(name="Alice"))
        assert element.find(f"{ns}created_at") is None
        assert element.find(f"{ns}updated_at") is None

    def test_timestamps_round_trip_when_set(self):
        original = Person(
            name="Alice",
            created_at="2026-01-01T00:00:00.000Z",
            updated_at="2026-04-30T12:34:56.789Z",
        )
        restored = element_to_person(parse(person_to_bytes(original)))
        assert restored.created_at == "2026-01-01T00:00:00.000Z"
        assert restored.updated_at == "2026-04-30T12:34:56.789Z"


class TestPersonGeneratorProvenance:
    def test_generator_emitted_on_every_serialize(self):
        # Q4 write-time provenance: <generator> appears on every emit
        # so a hand-investigated XML file always carries its origin.
        ns = "{" + SCHEMA_NAMESPACE_V1 + "}"
        element = person_to_element(Person(name="Alice"))
        gen = element.find(f"{ns}generator")
        assert gen is not None
        assert gen.text == GENERATOR_STRING

    def test_generator_not_part_of_model_equality(self):
        # The model has no generator field; round-trips must equate
        # even though the on-disk XML carries a generator string.
        # Pin so a future "preserve generator on the model" change
        # has to confront this contract.
        original = Person(name="Alice")
        restored = element_to_person(parse(person_to_bytes(original)))
        assert original == restored
        assert not hasattr(original, "generator")


class TestPersonValidation:
    def test_empty_name_rejected_at_pydantic(self):
        with pytest.raises(ValidationError):
            Person(name="")

    def test_oversize_name_rejected_at_pydantic(self):
        with pytest.raises(ValidationError):
            Person(name="A" * 121)

    def test_extra_field_rejected_at_pydantic(self):
        # ConfigDict(extra="forbid") — protects the schema from drift
        # if a client sends a payload with an unexpected key.
        with pytest.raises(ValidationError):
            Person(name="Alice", role="packer")  # type: ignore[call-arg]


class TestPersonRequestBodies:
    def test_person_create_minimal(self):
        body = PersonCreate(name="Alice")
        assert body.name == "Alice"
        assert body.notes is None

    def test_person_create_with_notes(self):
        body = PersonCreate(name="Alice", notes="rigger")
        assert body.notes == "rigger"

    def test_person_create_rejects_id(self):
        # PersonCreate is identity-free — clients cannot supply an id
        # (server-assigned per D54 / Phase 2c).
        with pytest.raises(ValidationError):
            PersonCreate(name="Alice", id=uuid4())  # type: ignore[call-arg]

    def test_person_update_round_trips(self):
        body = PersonUpdate(name="Alice Updated", notes="updated notes")
        assert body.name == "Alice Updated"
        assert body.notes == "updated notes"

    def test_person_summary_compact_shape(self):
        # PersonSummary is the SQLite-cached projection (Phase 2b).
        # No notes, no timestamps — the fields a picker actually needs.
        pid = uuid4()
        s = PersonSummary(id=pid, name="Alice")
        assert s.id == pid
        assert s.name == "Alice"
        # Other Person fields are absent on the summary by design.
        assert not hasattr(s, "notes")


class TestHandCraftedPersonXML:
    def test_hand_crafted_minimal_xml_validates_and_parses(self):
        # Pin that an XML file authored by hand (not by the writer)
        # validates and parses correctly. Acts as the "third-party
        # tool wrote a person.xml" contract test.
        pid = uuid4()
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<person xmlns="{SCHEMA_NAMESPACE_V1}">
  <id>{pid}</id>
  <name>Hand-Crafted</name>
</person>
""".encode()
        element = parse(xml)
        validate(element)
        person = element_to_person(element)
        assert person.id == pid
        assert person.name == "Hand-Crafted"
        assert person.notes is None
        assert person.created_at is None
        assert person.updated_at is None

    def test_xsd_rejects_missing_id(self):
        # <id> is required by the XSD. A hand-crafted file without
        # one must be rejected at validation time.
        from backend.xml.validator import XSDValidationError

        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<person xmlns="{SCHEMA_NAMESPACE_V1}">
  <name>NoId</name>
</person>
""".encode()
        with pytest.raises(XSDValidationError):
            validate(parse(xml))

    def test_xsd_rejects_missing_name(self):
        from backend.xml.validator import XSDValidationError

        pid = uuid4()
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<person xmlns="{SCHEMA_NAMESPACE_V1}">
  <id>{pid}</id>
</person>
""".encode()
        with pytest.raises(XSDValidationError):
            validate(parse(xml))

    def test_xsd_rejects_oversize_name(self):
        from backend.xml.validator import XSDValidationError

        pid = uuid4()
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<person xmlns="{SCHEMA_NAMESPACE_V1}">
  <id>{pid}</id>
  <name>{"A" * 121}</name>
</person>
""".encode()
        with pytest.raises(XSDValidationError):
            validate(parse(xml))
