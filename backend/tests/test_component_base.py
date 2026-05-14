"""Tests for the rig-manager shared base (D33, D34).

Covers ``ComponentStatus``, ``NotesLogEntry``, and ``ComponentBase``
in isolation — concrete-component round-trips land in their per-kind
test files (R.0.2b through R.0.2e).

The XSD-side counterparts (``ComponentStatus`` simpleType,
``NotesLogEntry`` complexType, ``ComponentBaseFields`` xs:group) live
in ``backend/xml/schema/SCHEMA.v1.xsd``; round-trip validation that
exercises both lands when the first concrete component is wired
(R.0.2b — Container).
"""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from backend.models._component_base import (
    ComponentBase,
    ComponentStatus,
    NotesLogEntry,
)


class TestComponentStatus:
    def test_exact_member_set(self):
        # The closed-enum stance from D22 (preserved through D34) means
        # adding a member is an explicit XSD + model + D-entry change.
        # Pin the set so a stray ``ComponentStatus.WHATEVER`` cannot
        # creep in unnoticed.
        assert {s.value for s in ComponentStatus} == {
            "active",
            "retired",
            "sold",
            "out_of_service",
        }

    def test_string_value_round_trips(self):
        # StrEnum compares equal to its string value (Python 3.11+ —
        # see https://docs.python.org/3/library/enum.html#enum.StrEnum).
        # The XSD enumeration values match these strings exactly; if
        # they ever drift, this assertion is the canary.
        assert ComponentStatus.ACTIVE == "active"
        assert ComponentStatus.OUT_OF_SERVICE == "out_of_service"

    def test_unknown_value_rejected(self):
        with pytest.raises(ValueError):
            ComponentStatus("not_a_real_status")


class TestNotesLogEntry:
    def test_minimal_entry_validates(self):
        e = NotesLogEntry(at="2026-04-28T14:30:00.000Z", text="Repacked")
        assert e.text == "Repacked"
        assert e.at == "2026-04-28T14:30:00.000Z"

    def test_text_required_min_length_1(self):
        with pytest.raises(ValidationError):
            NotesLogEntry(at="2026-04-28T14:30:00.000Z", text="")

    def test_text_max_length_2000(self):
        # Pin the cap at the XSD's value so the two layers agree.
        ok = NotesLogEntry(at="2026-04-28T14:30:00.000Z", text="x" * 2000)
        assert len(ok.text) == 2000
        with pytest.raises(ValidationError):
            NotesLogEntry(at="2026-04-28T14:30:00.000Z", text="x" * 2001)

    def test_extra_fields_forbidden(self):
        # ``model_config`` is ``extra="forbid"`` per the project
        # invariant — a typo (``txt=`` instead of ``text=``) must
        # surface at validation time, not silently turn into an
        # extra unknown field.
        with pytest.raises(ValidationError):
            NotesLogEntry(
                at="2026-04-28T14:30:00.000Z",
                text="ok",
                author="someone",  # type: ignore[call-arg]
            )


class TestComponentBase:
    def test_default_id_is_uuid4(self):
        c = ComponentBase()
        assert isinstance(c.id, UUID)
        # Pydantic uses ``default_factory=uuid4`` so every instance
        # gets a fresh ID. Two no-arg constructions must not collide.
        assert ComponentBase().id != c.id

    def test_default_status_is_active(self):
        # Onboarding a component is the common case; defaulting to
        # active keeps the create flow ergonomic. Retiring is an
        # explicit later operation.
        assert ComponentBase().status == ComponentStatus.ACTIVE

    def test_assigned_rig_id_optional(self):
        c = ComponentBase()
        assert c.assigned_rig_id is None

        rig_id = uuid4()
        c2 = ComponentBase(assigned_rig_id=rig_id)
        assert c2.assigned_rig_id == rig_id

    def test_notes_log_defaults_empty(self):
        # Empty list rather than None so concrete code can iterate
        # ``component.notes_log`` without a None-guard.
        assert ComponentBase().notes_log == []

    def test_notes_log_carries_entries(self):
        entry = NotesLogEntry(at="2026-04-28T14:30:00.000Z", text="Repacked")
        c = ComponentBase(notes_log=[entry])
        assert c.notes_log == [entry]

    def test_audit_timestamps_optional(self):
        # D32: optional on the model so a hand-crafted file (or a
        # pre-D32 record) validates. Service layer always stamps
        # both on writes.
        c = ComponentBase()
        assert c.created_at is None
        assert c.updated_at is None

    def test_extra_fields_forbidden(self):
        # The strict-shape posture from D2 (Pydantic models are the
        # single source of truth for runtime + API shape) carries
        # through to the base.
        with pytest.raises(ValidationError):
            ComponentBase(unexpected_field="x")  # type: ignore[call-arg]

    def test_invalid_status_string_rejected(self):
        # Sending an arbitrary string as ``status`` must fail at
        # parse, not produce a ComponentBase with an off-enum value.
        with pytest.raises(ValidationError):
            ComponentBase(status="halfway_retired")  # type: ignore[arg-type]
