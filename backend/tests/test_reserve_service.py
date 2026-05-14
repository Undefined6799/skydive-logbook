"""Service-layer tests for reserve_service (R.0.3d + R.1c, D33, D34)."""
from __future__ import annotations

import logging
import time
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from backend.api.errors import NotFoundError, ValidationFailedError
from backend.models._component_base import ComponentStatus
from backend.models.reserve import (
    ReserveCreate,
    ReserveRecertExtension,
    ReserveUpdate,
)
from backend.services import reserve_service
from backend.storage.bootstrap import bootstrap_logbook


@pytest.fixture
def bootstrapped_root(logbook_root: Path) -> Path:
    bootstrap_logbook(logbook_root)
    return logbook_root


def _create_payload(**overrides) -> ReserveCreate:
    base = {
        "manufacturer": "Performance Designs",
        "model": "Optimum",
        "serial": "OP-987654",
        "size_sqft": 143.0,
        "date_of_manufacture": date(2019, 8, 1),
        "repack_limit": 40,
        "ride_limit": 25,
    }
    base.update(overrides)
    return ReserveCreate(**base)


class TestCreate:
    def test_writes_file_at_uuid_path(self, bootstrapped_root: Path):
        r = reserve_service.create_reserve(
            bootstrapped_root, "default", _create_payload()
        )
        path = bootstrapped_root / "inventory" / "reserves" / f"{r.id}.xml"
        assert path.is_file()

    def test_assigns_server_uuid(self, bootstrapped_root: Path):
        r = reserve_service.create_reserve(
            bootstrapped_root, "default", _create_payload()
        )
        assert isinstance(r.id, UUID)

    def test_stamps_created_and_updated_at_same_moment(
        self, bootstrapped_root: Path
    ):
        r = reserve_service.create_reserve(
            bootstrapped_root, "default", _create_payload()
        )
        assert r.created_at is not None
        assert r.updated_at is not None
        assert r.created_at == r.updated_at

    def test_default_status_is_active(self, bootstrapped_root: Path):
        r = reserve_service.create_reserve(
            bootstrapped_root, "default", _create_payload()
        )
        assert r.status == ComponentStatus.ACTIVE

    def test_used_gear_repack_count_persists(self, bootstrapped_root: Path):
        # 14 prior repacks recorded on the paper card.
        r = reserve_service.create_reserve(
            bootstrapped_root,
            "default",
            _create_payload(repack_count_initial=14),
        )
        assert r.repack_count_initial == 14

    def test_recert_extensions_persist_and_round_trip(
        self, bootstrapped_root: Path
    ):
        ext = ReserveRecertExtension(
            granted_at="2025-06-01T09:00:00.000Z",
            extends_until=date(2030, 6, 1),
            granted_by="Master Rigger A. Smith",
            reason="Annual factory recert",
        )
        created = reserve_service.create_reserve(
            bootstrapped_root,
            "default",
            _create_payload(recert_extensions=[ext]),
        )
        assert created.recert_extensions == [ext]
        # Round-trip through disk via get.
        fetched = reserve_service.get_reserve(
            bootstrapped_root, "default", created.id
        )
        assert fetched.recert_extensions == [ext]


class TestGet:
    def test_round_trips_full_field_set(self, bootstrapped_root: Path):
        created = reserve_service.create_reserve(
            bootstrapped_root,
            "default",
            _create_payload(
                repack_count_initial=14,
                ride_count_initial=2,
            ),
        )
        # R.2.0c.iii.b: pre-assign via the rig_service-owned seam.
        rig_id = uuid4()
        reserve_service.set_assigned_rig_id(
            bootstrapped_root, created.id, rig_id
        )
        fetched = reserve_service.get_reserve(
            bootstrapped_root, "default", created.id
        )
        assert fetched.assigned_rig_id == rig_id
        assert fetched.repack_count_initial == 14
        assert fetched.ride_count_initial == 2

    def test_missing_id_raises_not_found(self, bootstrapped_root: Path):
        with pytest.raises(NotFoundError):
            reserve_service.get_reserve(bootstrapped_root, "default", uuid4())

    def test_corrupt_xml_raises_validation_failed(
        self, bootstrapped_root: Path
    ):
        bad_id = uuid4()
        path = bootstrapped_root / "inventory" / "reserves" / f"{bad_id}.xml"
        path.write_bytes(b"<broken>")
        with pytest.raises(ValidationFailedError):
            reserve_service.get_reserve(bootstrapped_root, "default", bad_id)


class TestStructuredLoggingExtraKeys:
    def test_create_reserve_log_has_no_collision(
        self, bootstrapped_root: Path, caplog
    ):
        caplog.set_level(logging.INFO, logger="backend.services.reserve")
        reserve_service.create_reserve(
            bootstrapped_root, "default", _create_payload()
        )
        assert any(r.message == "reserve_created" for r in caplog.records)

    def test_update_reserve_log_has_no_collision(
        self, bootstrapped_root: Path, caplog
    ):
        created = reserve_service.create_reserve(
            bootstrapped_root, "default", _create_payload()
        )
        caplog.set_level(logging.INFO, logger="backend.services.reserve")
        reserve_service.update_reserve(
            bootstrapped_root, "default", created.id, _update_payload()
        )
        assert any(r.message == "reserve_updated" for r in caplog.records)

    def test_delete_reserve_log_has_no_collision(
        self, bootstrapped_root: Path, caplog
    ):
        created = reserve_service.create_reserve(
            bootstrapped_root, "default", _create_payload()
        )
        caplog.set_level(logging.INFO, logger="backend.services.reserve")
        reserve_service.delete_reserve(
            bootstrapped_root, "default", created.id
        )
        assert any(r.message == "reserve_deleted" for r in caplog.records)


# --------------------------------------------------------------------------- #
# list_reserves / update_reserve / delete_reserve (R.1c)
# --------------------------------------------------------------------------- #

def _update_payload(**overrides) -> ReserveUpdate:
    base = {
        "status": ComponentStatus.RETIRED,
        "manufacturer": "Performance Designs",
        "model": "Optimum",
        "serial": "OP-987654",
        "size_sqft": 143.0,
        "date_of_manufacture": date(2019, 8, 1),
        "repack_limit": 40,
        "ride_limit": 25,
    }
    base.update(overrides)
    return ReserveUpdate(**base)


class TestList:
    def test_empty_returns_empty(self, bootstrapped_root: Path):
        assert reserve_service.list_reserves(
            bootstrapped_root, "default"
        ) == []

    def test_lists_every_reserve(self, bootstrapped_root: Path):
        ids = set()
        for n in range(3):
            r = reserve_service.create_reserve(
                bootstrapped_root,
                "default",
                _create_payload(serial=f"OP-{n}"),
            )
            ids.add(r.id)
        listed = reserve_service.list_reserves(bootstrapped_root, "default")
        assert {r.id for r in listed} == ids

    def test_orders_by_created_at_descending(self, bootstrapped_root: Path):
        first = reserve_service.create_reserve(
            bootstrapped_root, "default", _create_payload(serial="A")
        )
        time.sleep(0.005)
        second = reserve_service.create_reserve(
            bootstrapped_root, "default", _create_payload(serial="B")
        )
        time.sleep(0.005)
        third = reserve_service.create_reserve(
            bootstrapped_root, "default", _create_payload(serial="C")
        )
        listed = reserve_service.list_reserves(bootstrapped_root, "default")
        assert [r.id for r in listed] == [third.id, second.id, first.id]


class TestUpdate:
    def test_full_replace(self, bootstrapped_root: Path):
        created = reserve_service.create_reserve(
            bootstrapped_root, "default", _create_payload()
        )
        updated = reserve_service.update_reserve(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload(model="PD Reserve"),
        )
        assert updated.model == "PD Reserve"
        assert updated.status == ComponentStatus.RETIRED

    def test_append_recert_extension_via_update(
        self, bootstrapped_root: Path
    ):
        # The natural use of recert_extensions: client reads the
        # current reserve, appends a new ReserveRecertExtension to
        # the list, and PUTs the whole record back. This pins that
        # the round-trip preserves the existing entries plus the new
        # one in order.
        created = reserve_service.create_reserve(
            bootstrapped_root,
            "default",
            _create_payload(
                recert_extensions=[
                    ReserveRecertExtension(
                        granted_at="2025-06-01T09:00:00.000Z",
                        extends_until=date(2030, 6, 1),
                        granted_by="Master Rigger A. Smith",
                    )
                ]
            ),
        )
        new_entry = ReserveRecertExtension(
            granted_at="2026-04-28T14:30:00.000Z",
            extends_until=date(2031, 4, 28),
            granted_by="Master Rigger B. Jones",
            reason="Annual factory recert",
        )
        # Build the update payload with both entries.
        all_entries = [*created.recert_extensions, new_entry]
        updated = reserve_service.update_reserve(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload(recert_extensions=all_entries),
        )
        assert updated.recert_extensions == all_entries

    def test_missing_id_raises_not_found(self, bootstrapped_root: Path):
        with pytest.raises(NotFoundError):
            reserve_service.update_reserve(
                bootstrapped_root, "default", uuid4(), _update_payload()
            )


class TestDelete:
    def test_moves_file_to_trash(self, bootstrapped_root: Path):
        created = reserve_service.create_reserve(
            bootstrapped_root, "default", _create_payload()
        )
        original = (
            bootstrapped_root / "inventory" / "reserves" / f"{created.id}.xml"
        )
        assert original.is_file()
        trashed = reserve_service.delete_reserve(
            bootstrapped_root, "default", created.id
        )
        assert not original.exists()
        assert trashed.parent.parent == (
            bootstrapped_root / ".trash" / "inventory" / "reserves"
        )

    def test_subsequent_get_raises_not_found(self, bootstrapped_root: Path):
        created = reserve_service.create_reserve(
            bootstrapped_root, "default", _create_payload()
        )
        reserve_service.delete_reserve(
            bootstrapped_root, "default", created.id
        )
        with pytest.raises(NotFoundError):
            reserve_service.get_reserve(
                bootstrapped_root, "default", created.id
            )

    def test_missing_id_raises_not_found(self, bootstrapped_root: Path):
        with pytest.raises(NotFoundError):
            reserve_service.delete_reserve(
                bootstrapped_root, "default", uuid4()
            )
