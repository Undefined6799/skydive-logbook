"""End-to-end XML round-trip + XSD validation for RigSnapshot (R.2.1.b).

Pipeline mirrors the per-entity round-trips:
    RigSnapshot → serialize → parse → XSD-validate → RigSnapshot  ==  original

Pin snapshot-specific shape:
  * snapshot_at, rig (denormalized id/nickname/jurisdiction +
    optional last_repack_date), main, reserve, aad, container,
    jumper, generator — in that XSD order.
  * The denormalized rig does NOT carry repack_history (D36 keeps
    snapshots compact).
  * Per D36 the snapshot writer leaves the main's lineset_history
    empty; pin the byte-stable round-trip in that case.
  * The MainContent / ReserveContent / AADContent /
    ContainerContent / JumperContent reusable types (R.2.1.a)
    keep the field set in lockstep with the live entities.
"""
from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.models._component_base import ComponentStatus, NotesLogEntry
from backend.models.aad import AAD
from backend.models.common import SCHEMA_NAMESPACE_V1
from backend.models.container import Container
from backend.models.jumper import Jumper
from backend.models.main import Lineset, Main
from backend.models.reserve import Reserve
from backend.models.rig import Jurisdiction
from backend.models.rig_snapshot import RigSnapshot, RigSnapshotRig
from backend.xml.serialize import (
    element_to_rig_snapshot,
    rig_snapshot_to_bytes,
    rig_snapshot_to_element,
)
from backend.xml.validator import XMLError, parse, validate


def _minimal_snapshot(**rig_overrides) -> RigSnapshot:
    """Build a minimal but XSD-valid RigSnapshot for round-trip tests."""
    rig_base: dict = {
        "id": uuid4(),
        "nickname": "Black Cobra",
        "jurisdiction": Jurisdiction.USPA,
    }
    rig_base.update(rig_overrides)
    return RigSnapshot(
        snapshot_at="2026-04-28T14:30:00.000Z",
        rig=RigSnapshotRig(**rig_base),
        main=Main(),
        reserve=Reserve(),
        aad=AAD(),
        container=Container(),
        jumper=Jumper(exit_weight_lb=200.0),
    )


class TestRigSnapshotRoundTrip:
    def test_minimal_snapshot_roundtrips(self):
        original = _minimal_snapshot()
        raw = rig_snapshot_to_bytes(original)
        element = parse(raw)
        validate(element)
        assert element_to_rig_snapshot(element) == original

    def test_full_field_set_roundtrips(self):
        # Every nested entity gets a defensible set of fields,
        # including identifiers and counters, plus notes_log /
        # current_lineset where the model supports them.
        rig_id = uuid4()
        main = Main(
            id=uuid4(),
            status=ComponentStatus.ACTIVE,
            assigned_rig_id=rig_id,
            notes_log=[
                NotesLogEntry(
                    at="2026-04-28T10:00:00.000Z",
                    text="Pre-jump inspection clean",
                ),
            ],
            manufacturer="PD",
            model="Sabre 3",
            serial="S3-12345",
            size_sqft=170.0,
            date_of_manufacture=date(2024, 6, 1),
            jump_count_initial=420,
            current_lineset=Lineset(
                line_type="Vectran V750",
                breaking_strength_lb=750.0,
                install_date=date(2025, 1, 15),
                installed_by="Master Rigger A. Smith",
                jumps_on_lineset_initial=12,
            ),
            created_at="2026-04-28T10:00:00.000Z",
            updated_at="2026-04-28T10:00:00.000Z",
        )
        reserve = Reserve(
            id=uuid4(),
            assigned_rig_id=rig_id,
            manufacturer="PD",
            model="Optimum",
            serial="OP-789",
            size_sqft=143.0,
            date_of_manufacture=date(2019, 8, 1),
            repack_limit=40,
            ride_limit=25,
            repack_count_initial=8,
            ride_count_initial=0,
        )
        aad = AAD(
            id=uuid4(),
            assigned_rig_id=rig_id,
            manufacturer="Cypres",
            model="Cypres 2",
            serial="C2-XYZ",
            date_of_manufacture=date(2018, 5, 1),
            mode="Pro",
            is_changeable_mode=True,
            jump_count_initial=300,
            fire_count_initial=0,
        )
        container = Container(
            id=uuid4(),
            assigned_rig_id=rig_id,
            manufacturer="UPT",
            model="Vector V3",
            serial="V3-001",
            size="M",
            date_of_manufacture=date(2024, 1, 1),
            jump_count_initial=420,
        )
        jumper = Jumper(
            id=uuid4(),
            name="Alex Tester",
            exit_weight_lb=205.0,
            exit_weight_updated_at=date(2026, 1, 15),
        )
        original = RigSnapshot(
            snapshot_at="2026-04-28T14:30:00.000Z",
            rig=RigSnapshotRig(
                id=rig_id,
                nickname="Black Cobra",
                jurisdiction=Jurisdiction.BOTH,
                last_repack_date=date(2025, 11, 1),
            ),
            main=main,
            reserve=reserve,
            aad=aad,
            container=container,
            jumper=jumper,
        )
        element = rig_snapshot_to_element(original)
        validate(element)
        assert element_to_rig_snapshot(element) == original

    def test_optional_last_repack_date_elides_when_none(self):
        original = _minimal_snapshot(last_repack_date=None)
        raw = rig_snapshot_to_bytes(original)
        assert b"<last_repack_date>" not in raw
        # And the round-trip preserves None.
        restored = element_to_rig_snapshot(parse(raw))
        assert restored.rig.last_repack_date is None

    def test_last_repack_date_round_trips(self):
        original = _minimal_snapshot(last_repack_date=date(2026, 4, 1))
        element = rig_snapshot_to_element(original)
        validate(element)
        restored = element_to_rig_snapshot(element)
        assert restored.rig.last_repack_date == date(2026, 4, 1)

    def test_each_jurisdiction_value_validates(self):
        for j in Jurisdiction:
            s = _minimal_snapshot(jurisdiction=j)
            element = rig_snapshot_to_element(s)
            validate(element)

    def test_main_with_empty_lineset_history_byte_stable(self):
        # D36: the writer leaves lineset_history empty on the
        # snapshot. The XSD tolerates either shape (MainContent is
        # shared); the elision is what keeps the file compact.
        original = _minimal_snapshot()
        # Main.lineset_history defaults to [] — no need to override.
        raw = rig_snapshot_to_bytes(original)
        # Confirm there's no <lineset_history> wrapper inside the
        # snapshot's <main>. That's what D36 wants the writer to
        # produce; this test pins the shape.
        assert b"<lineset_history>" not in raw

    def test_main_with_lineset_history_still_round_trips(self):
        # Documenting that the model and serializer DO tolerate
        # history if a hand-crafted file has one — the empty-history
        # invariant lives in the writer, not the data shape. A
        # future drift that flipped the writer would still produce
        # parseable XML.
        archived = Lineset(
            line_type="Vectran V750",
            breaking_strength_lb=750.0,
            install_date=date(2024, 1, 1),
            jumps_on_lineset_initial=350,
        )
        main_with_history = Main(lineset_history=[archived])
        original = RigSnapshot(
            snapshot_at="2026-04-28T14:30:00.000Z",
            rig=RigSnapshotRig(
                id=uuid4(),
                nickname="Black Cobra",
                jurisdiction=Jurisdiction.USPA,
            ),
            main=main_with_history,
            reserve=Reserve(),
            aad=AAD(),
            container=Container(),
            jumper=Jumper(exit_weight_lb=200.0),
        )
        element = rig_snapshot_to_element(original)
        validate(element)
        restored = element_to_rig_snapshot(element)
        assert restored.main.lineset_history == [archived]

    def test_unicode_in_rig_nickname_roundtrips(self):
        original = _minimal_snapshot(nickname="Élise — vol 1 ✈")
        element = rig_snapshot_to_element(original)
        validate(element)
        restored = element_to_rig_snapshot(element)
        assert restored.rig.nickname == "Élise — vol 1 ✈"

    def test_snapshot_at_preserved_through_roundtrip(self):
        original = _minimal_snapshot()
        original_at = original.snapshot_at
        element = rig_snapshot_to_element(original)
        validate(element)
        restored = element_to_rig_snapshot(element)
        assert restored.snapshot_at == original_at

    def test_all_five_nested_entities_present_in_xml(self):
        # Sanity: the serialized form has each of the five nested
        # entity tags. Guards against a future drift that drops one.
        original = _minimal_snapshot()
        raw = rig_snapshot_to_bytes(original)
        for tag in (
            b"<main>",
            b"<reserve>",
            b"<aad>",
            b"<container>",
            b"<jumper>",
        ):
            assert tag in raw, f"missing {tag!r} in snapshot output"

    def test_rig_does_not_carry_repack_history(self):
        # D36: the snapshot's denormalized <rig> is intentionally
        # narrow — id, nickname, jurisdiction, last_repack_date.
        # NOT repack_history (which lives only on the canonical
        # rig.xml). Pin the absence at the shape level so a future
        # drift that adds repack_history to RigSnapshotRig surfaces
        # here.
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError):
            RigSnapshotRig(
                id=uuid4(),
                nickname="X",
                jurisdiction=Jurisdiction.USPA,
                repack_history=[],  # type: ignore[call-arg]
            )


class TestRigSnapshotPydanticContract:
    """Pin the shape at the Pydantic layer."""

    def test_snapshot_rejects_unknown_field(self):
        with pytest.raises(ValidationError):
            RigSnapshot(
                snapshot_at="2026-04-28T14:30:00.000Z",
                rig=RigSnapshotRig(
                    id=uuid4(),
                    nickname="X",
                    jurisdiction=Jurisdiction.USPA,
                ),
                main=Main(),
                reserve=Reserve(),
                aad=AAD(),
                container=Container(),
                jumper=Jumper(exit_weight_lb=200.0),
                rig_id=uuid4(),  # type: ignore[call-arg]
            )

    def test_snapshot_requires_all_five_entities(self):
        # Drop one entity; should raise.
        with pytest.raises(ValidationError):
            RigSnapshot(
                snapshot_at="2026-04-28T14:30:00.000Z",
                rig=RigSnapshotRig(
                    id=uuid4(),
                    nickname="X",
                    jurisdiction=Jurisdiction.USPA,
                ),
                main=Main(),
                reserve=Reserve(),
                aad=AAD(),
                container=Container(),
                # missing jumper
            )  # type: ignore[call-arg]

    def test_rig_nickname_max_length(self):
        # 120 OK, 121 rejected.
        RigSnapshotRig(
            id=uuid4(),
            nickname="a" * 120,
            jurisdiction=Jurisdiction.USPA,
        )
        with pytest.raises(ValidationError):
            RigSnapshotRig(
                id=uuid4(),
                nickname="a" * 121,
                jurisdiction=Jurisdiction.USPA,
            )


class TestRigSnapshotXSDContract:
    """Pin XSD-layer rejections that Pydantic doesn't enforce."""

    def test_unknown_jurisdiction_fails_xsd(self):
        # Build a snapshot via the serializer and corrupt the
        # jurisdiction value in the resulting XML — avoids 50 lines
        # of literal XML. Only one <jurisdiction> element exists
        # (inside <rig>), so the single replace is unambiguous.
        ok = _minimal_snapshot()
        raw = rig_snapshot_to_bytes(ok).decode()
        bad = raw.replace(
            "<jurisdiction>USPA</jurisdiction>",
            "<jurisdiction>FAA</jurisdiction>",
            1,
        )
        element = parse(bad.encode())
        with pytest.raises(XMLError):
            validate(element)

    def test_missing_main_fails_xsd(self):
        # Build a snapshot, then strip the <main> element from the
        # serialized XML — should fail XSD validation since main is
        # required.
        ok = _minimal_snapshot()
        raw = rig_snapshot_to_bytes(ok).decode()
        # Strip the <main>...</main> block. Since serializer pretty-
        # prints, find and excise the wrapper.
        start = raw.index("<main>")
        end = raw.index("</main>") + len("</main>")
        stripped = raw[:start] + raw[end:]
        element = parse(stripped.encode())
        with pytest.raises(XMLError):
            validate(element)

    def test_missing_jumper_fails_xsd(self):
        ok = _minimal_snapshot()
        raw = rig_snapshot_to_bytes(ok).decode()
        start = raw.index("<jumper>")
        end = raw.index("</jumper>") + len("</jumper>")
        stripped = raw[:start] + raw[end:]
        element = parse(stripped.encode())
        with pytest.raises(XMLError):
            validate(element)

    def test_invalid_uuid_in_rig_fails_xsd(self):
        ns = SCHEMA_NAMESPACE_V1
        # Build literal XML with a non-v4 UUID in <rig>/<id>.
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rig_snapshot xmlns="{ns}">
  <snapshot_at>2026-04-28T14:30:00.000Z</snapshot_at>
  <rig>
    <id>00000000-0000-1000-8000-000000000000</id>
    <nickname>X</nickname>
    <jurisdiction>USPA</jurisdiction>
  </rig>
  <main>
    <id>11111111-1111-4111-8111-111111111111</id>
    <status>active</status>
    <jump_count_initial>0</jump_count_initial>
  </main>
  <reserve>
    <id>22222222-2222-4222-8222-222222222222</id>
    <status>active</status>
    <repack_count_initial>0</repack_count_initial>
    <ride_count_initial>0</ride_count_initial>
  </reserve>
  <aad>
    <id>33333333-3333-4333-8333-333333333333</id>
    <status>active</status>
    <jump_count_initial>0</jump_count_initial>
    <fire_count_initial>0</fire_count_initial>
  </aad>
  <container>
    <id>44444444-4444-4444-8444-444444444444</id>
    <status>active</status>
    <jump_count_initial>0</jump_count_initial>
  </container>
  <jumper>
    <id>55555555-5555-4555-8555-555555555555</id>
    <exit_weight_lb>200</exit_weight_lb>
  </jumper>
</rig_snapshot>
""".encode()
        element = parse(xml)
        with pytest.raises(XMLError):
            validate(element)
