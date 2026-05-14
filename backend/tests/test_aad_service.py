"""Service-layer tests for aad_service (R.0.3c + R.1b, D33, D34)."""
from __future__ import annotations

import logging
import time
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from backend.api.errors import NotFoundError, ValidationFailedError
from backend.models._component_base import ComponentStatus, NotesLogEntry
from backend.models.aad import AADCreate, AADUpdate
from backend.services import aad_service
from backend.storage.bootstrap import bootstrap_logbook


@pytest.fixture
def bootstrapped_root(logbook_root: Path) -> Path:
    bootstrap_logbook(logbook_root)
    return logbook_root


def _create_payload(**overrides) -> AADCreate:
    base = {
        "manufacturer": "Airtec",
        "model": "Cypres 2",
        "serial": "C2-987654",
        "date_of_manufacture": date(2017, 3, 12),
        "mode": "Pro",
        "is_changeable_mode": True,
        "jump_count_initial": 420,
    }
    base.update(overrides)
    return AADCreate(**base)


# --------------------------------------------------------------------------- #
# create_aad
# --------------------------------------------------------------------------- #

class TestCreate:
    def test_writes_file_at_uuid_path(self, bootstrapped_root: Path):
        a = aad_service.create_aad(
            bootstrapped_root, "default", _create_payload()
        )
        path = bootstrapped_root / "inventory" / "aads" / f"{a.id}.xml"
        assert path.is_file()

    def test_assigns_server_uuid(self, bootstrapped_root: Path):
        a = aad_service.create_aad(
            bootstrapped_root, "default", _create_payload()
        )
        assert isinstance(a.id, UUID)

    def test_two_creates_get_distinct_ids(self, bootstrapped_root: Path):
        a = aad_service.create_aad(
            bootstrapped_root, "default", _create_payload()
        )
        b = aad_service.create_aad(
            bootstrapped_root, "default", _create_payload()
        )
        assert a.id != b.id

    def test_stamps_created_and_updated_at_same_moment(
        self, bootstrapped_root: Path
    ):
        a = aad_service.create_aad(
            bootstrapped_root, "default", _create_payload()
        )
        assert a.created_at is not None
        assert a.updated_at is not None
        assert a.created_at == a.updated_at

    def test_default_status_is_active(self, bootstrapped_root: Path):
        a = aad_service.create_aad(
            bootstrapped_root, "default", _create_payload()
        )
        assert a.status == ComponentStatus.ACTIVE

    def test_minimal_payload_creates_clean(self, bootstrapped_root: Path):
        # Bare AAD — D34 lets every identification field be None;
        # only the D35 counter seeds default to 0.
        a = aad_service.create_aad(
            bootstrapped_root, "default", AADCreate()
        )
        assert a.manufacturer is None
        assert a.is_changeable_mode is None  # distinct from False
        assert a.jump_count_initial == 0
        assert a.fire_count_initial == 0

    def test_used_gear_counts_persist(self, bootstrapped_root: Path):
        a = aad_service.create_aad(
            bootstrapped_root,
            "default",
            _create_payload(jump_count_initial=850, fire_count_initial=2),
        )
        assert a.jump_count_initial == 850
        assert a.fire_count_initial == 2


# --------------------------------------------------------------------------- #
# get_aad
# --------------------------------------------------------------------------- #

class TestGet:
    def test_round_trips_full_field_set(self, bootstrapped_root: Path):
        notes = [
            NotesLogEntry(
                at="2026-04-28T14:30:00.000Z",
                text="4-year service current",
            )
        ]
        created = aad_service.create_aad(
            bootstrapped_root,
            "default",
            _create_payload(
                notes_log=notes,
                fire_count_initial=0,
            ),
        )
        # R.2.0c.iii.b: pre-assign via the rig_service-owned seam.
        rig_id = uuid4()
        aad_service.set_assigned_rig_id(bootstrapped_root, created.id, rig_id)
        fetched = aad_service.get_aad(
            bootstrapped_root, "default", created.id
        )
        assert fetched.assigned_rig_id == rig_id
        assert fetched.notes_log == notes

    def test_missing_id_raises_not_found(self, bootstrapped_root: Path):
        with pytest.raises(NotFoundError):
            aad_service.get_aad(bootstrapped_root, "default", uuid4())

    def test_corrupt_xml_raises_validation_failed(
        self, bootstrapped_root: Path
    ):
        bad_id = uuid4()
        path = bootstrapped_root / "inventory" / "aads" / f"{bad_id}.xml"
        path.write_bytes(b"<broken>")
        with pytest.raises(ValidationFailedError):
            aad_service.get_aad(bootstrapped_root, "default", bad_id)


class TestStructuredLoggingExtraKeys:
    """Same regression class as container_service / dropzone hotfix."""

    def test_create_aad_log_has_no_collision(
        self, bootstrapped_root: Path, caplog
    ):
        caplog.set_level(logging.INFO, logger="backend.services.aad")
        aad_service.create_aad(
            bootstrapped_root, "default", _create_payload()
        )
        assert any(r.message == "aad_created" for r in caplog.records)

    def test_update_aad_log_has_no_collision(
        self, bootstrapped_root: Path, caplog
    ):
        created = aad_service.create_aad(
            bootstrapped_root, "default", _create_payload()
        )
        caplog.set_level(logging.INFO, logger="backend.services.aad")
        aad_service.update_aad(
            bootstrapped_root, "default", created.id, _update_payload()
        )
        assert any(r.message == "aad_updated" for r in caplog.records)

    def test_delete_aad_log_has_no_collision(
        self, bootstrapped_root: Path, caplog
    ):
        created = aad_service.create_aad(
            bootstrapped_root, "default", _create_payload()
        )
        caplog.set_level(logging.INFO, logger="backend.services.aad")
        aad_service.delete_aad(bootstrapped_root, "default", created.id)
        assert any(r.message == "aad_deleted" for r in caplog.records)


# --------------------------------------------------------------------------- #
# list_aads (R.1b)
# --------------------------------------------------------------------------- #

def _update_payload(**overrides) -> AADUpdate:
    base = {
        "status": ComponentStatus.RETIRED,
        "manufacturer": "Airtec",
        "model": "Cypres 2",
        "serial": "C2-987654",
        "date_of_manufacture": date(2017, 3, 12),
        "mode": "Pro",
        "is_changeable_mode": True,
        "jump_count_initial": 420,
    }
    base.update(overrides)
    return AADUpdate(**base)


class TestList:
    def test_empty_returns_empty_list(self, bootstrapped_root: Path):
        assert aad_service.list_aads(bootstrapped_root, "default") == []

    def test_lists_every_aad(self, bootstrapped_root: Path):
        ids = set()
        for n in range(3):
            a = aad_service.create_aad(
                bootstrapped_root,
                "default",
                _create_payload(serial=f"C2-{n}"),
            )
            ids.add(a.id)
        listed = aad_service.list_aads(bootstrapped_root, "default")
        assert {a.id for a in listed} == ids

    def test_orders_by_created_at_descending(self, bootstrapped_root: Path):
        first = aad_service.create_aad(
            bootstrapped_root, "default", _create_payload(serial="A")
        )
        time.sleep(0.005)
        second = aad_service.create_aad(
            bootstrapped_root, "default", _create_payload(serial="B")
        )
        time.sleep(0.005)
        third = aad_service.create_aad(
            bootstrapped_root, "default", _create_payload(serial="C")
        )
        listed = aad_service.list_aads(bootstrapped_root, "default")
        assert [a.id for a in listed] == [third.id, second.id, first.id]

    def test_skips_invalid_xml_files(self, bootstrapped_root: Path):
        good = aad_service.create_aad(
            bootstrapped_root, "default", _create_payload()
        )
        bad_path = (
            bootstrapped_root / "inventory" / "aads" / f"{uuid4()}.xml"
        )
        bad_path.write_bytes(b"<broken>")
        listed = aad_service.list_aads(bootstrapped_root, "default")
        assert {a.id for a in listed} == {good.id}


class TestUpdate:
    def test_full_replace_persists(self, bootstrapped_root: Path):
        created = aad_service.create_aad(
            bootstrapped_root, "default", _create_payload()
        )
        updated = aad_service.update_aad(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload(mode="Expert"),
        )
        assert updated.mode == "Expert"
        fetched = aad_service.get_aad(
            bootstrapped_root, "default", created.id
        )
        assert fetched == updated

    def test_preserves_id_and_created_at(self, bootstrapped_root: Path):
        created = aad_service.create_aad(
            bootstrapped_root, "default", _create_payload()
        )
        updated = aad_service.update_aad(
            bootstrapped_root, "default", created.id, _update_payload()
        )
        assert updated.id == created.id
        assert updated.created_at == created.created_at

    def test_bumps_updated_at(self, bootstrapped_root: Path):
        created = aad_service.create_aad(
            bootstrapped_root, "default", _create_payload()
        )
        time.sleep(0.005)
        updated = aad_service.update_aad(
            bootstrapped_root, "default", created.id, _update_payload()
        )
        assert updated.updated_at > created.updated_at

    def test_is_changeable_mode_round_trip(self, bootstrapped_root: Path):
        # AAD-specific: bool|None field. None means "unknown" — distinct
        # from False (locked) and True (changeable).
        created = aad_service.create_aad(
            bootstrapped_root,
            "default",
            _create_payload(is_changeable_mode=None),
        )
        updated = aad_service.update_aad(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload(is_changeable_mode=False),
        )
        assert updated.is_changeable_mode is False

    def test_missing_id_raises_not_found(self, bootstrapped_root: Path):
        with pytest.raises(NotFoundError):
            aad_service.update_aad(
                bootstrapped_root, "default", uuid4(), _update_payload()
            )


class TestDelete:
    def test_moves_file_to_trash(self, bootstrapped_root: Path):
        created = aad_service.create_aad(
            bootstrapped_root, "default", _create_payload()
        )
        original = (
            bootstrapped_root / "inventory" / "aads" / f"{created.id}.xml"
        )
        assert original.is_file()
        trashed = aad_service.delete_aad(
            bootstrapped_root, "default", created.id
        )
        assert not original.exists()
        assert trashed.parent.parent == (
            bootstrapped_root / ".trash" / "inventory" / "aads"
        )

    def test_subsequent_get_raises_not_found(self, bootstrapped_root: Path):
        created = aad_service.create_aad(
            bootstrapped_root, "default", _create_payload()
        )
        aad_service.delete_aad(bootstrapped_root, "default", created.id)
        with pytest.raises(NotFoundError):
            aad_service.get_aad(bootstrapped_root, "default", created.id)

    def test_missing_id_raises_not_found(self, bootstrapped_root: Path):
        with pytest.raises(NotFoundError):
            aad_service.delete_aad(bootstrapped_root, "default", uuid4())
