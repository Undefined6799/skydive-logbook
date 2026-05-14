"""Service-layer tests for main_service (R.0.3e + R.1d, D33, D34)."""
from __future__ import annotations

import logging
import time
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from backend.api.errors import NotFoundError, ValidationFailedError
from backend.models._component_base import ComponentStatus
from backend.models.dropzone import Environment
from backend.models.main import Lineset, MainCreate, MainUpdate
from backend.services import main_service
from backend.storage.bootstrap import bootstrap_logbook


@pytest.fixture
def bootstrapped_root(logbook_root: Path) -> Path:
    bootstrap_logbook(logbook_root)
    return logbook_root


def _create_payload(**overrides) -> MainCreate:
    base = {
        "manufacturer": "Performance Designs",
        "model": "Sabre 2",
        "serial": "S2-987654",
        "size_sqft": 170.0,
        "date_of_manufacture": date(2018, 6, 15),
        "default_environment": Environment.DUST_SAND_SALT,
    }
    base.update(overrides)
    return MainCreate(**base)


def _sample_lineset(**overrides) -> Lineset:
    base = {
        "line_type": "Vectran V750",
        "breaking_strength_lb": 750.0,
        "install_date": date(2025, 1, 15),
        "installed_by": "Master Rigger A. Smith",
        # D46: was consumed_lb_initial: float (lb).
        "jumps_on_lineset_initial": 0,
    }
    base.update(overrides)
    return Lineset(**base)


class TestCreate:
    def test_writes_file_at_uuid_path(self, bootstrapped_root: Path):
        m = main_service.create_main(
            bootstrapped_root, "default", _create_payload()
        )
        path = bootstrapped_root / "inventory" / "mains" / f"{m.id}.xml"
        assert path.is_file()

    def test_assigns_server_uuid(self, bootstrapped_root: Path):
        m = main_service.create_main(
            bootstrapped_root, "default", _create_payload()
        )
        assert isinstance(m.id, UUID)

    def test_stamps_created_and_updated_at_same_moment(
        self, bootstrapped_root: Path
    ):
        m = main_service.create_main(
            bootstrapped_root, "default", _create_payload()
        )
        assert m.created_at is not None
        assert m.updated_at is not None
        assert m.created_at == m.updated_at

    def test_default_status_is_active(self, bootstrapped_root: Path):
        m = main_service.create_main(
            bootstrapped_root, "default", _create_payload()
        )
        assert m.status == ComponentStatus.ACTIVE

    def test_minimal_payload_creates_clean(self, bootstrapped_root: Path):
        m = main_service.create_main(
            bootstrapped_root, "default", MainCreate()
        )
        assert m.manufacturer is None
        assert m.current_lineset is None
        assert m.lineset_history == []
        assert m.jump_count_initial == 0

    def test_used_gear_starting_count_persists(self, bootstrapped_root: Path):
        m = main_service.create_main(
            bootstrapped_root,
            "default",
            _create_payload(jump_count_initial=850),
        )
        assert m.jump_count_initial == 850

    def test_default_environment_persists(self, bootstrapped_root: Path):
        m = main_service.create_main(
            bootstrapped_root,
            "default",
            _create_payload(default_environment=Environment.DESERT),
        )
        assert m.default_environment == Environment.DESERT


class TestLineset:
    def test_current_lineset_persists(self, bootstrapped_root: Path):
        ls = _sample_lineset()
        created = main_service.create_main(
            bootstrapped_root,
            "default",
            _create_payload(current_lineset=ls),
        )
        assert created.current_lineset == ls

    def test_lineset_id_default_factory_assigns_fresh_uuid(
        self, bootstrapped_root: Path
    ):
        # Lineset.id has default_factory=uuid4 so two linesets built
        # without an explicit id get distinct UUIDs. Pin this so a
        # future refactor (e.g. moving the default off the model)
        # surfaces as a test failure rather than silent collision in
        # rig-snapshot.xml later (D36).
        a = _sample_lineset()
        b = _sample_lineset()
        assert a.id != b.id

    def test_lineset_history_preserves_order(self, bootstrapped_root: Path):
        # History is append-only; reordering would silently rewrite
        # which lineset was on the main in which time period.
        ids = [uuid4() for _ in range(3)]
        history = [
            _sample_lineset(id=ids[0], install_date=date(2020, 1, 1)),
            _sample_lineset(id=ids[1], install_date=date(2022, 1, 1)),
            _sample_lineset(id=ids[2], install_date=date(2024, 1, 1)),
        ]
        created = main_service.create_main(
            bootstrapped_root,
            "default",
            _create_payload(lineset_history=history),
        )
        assert [ls.id for ls in created.lineset_history] == ids

    def test_current_and_history_coexist_round_trip(
        self, bootstrapped_root: Path
    ):
        history = [
            _sample_lineset(
                id=uuid4(),
                line_type="Vectran V750",
                jumps_on_lineset_initial=500,
                install_date=date(2020, 1, 1),
            ),
        ]
        current = _sample_lineset(
            id=uuid4(),
            line_type="HMA 500",
            breaking_strength_lb=500.0,
            jumps_on_lineset_initial=0,
            install_date=date(2024, 6, 1),
        )
        created = main_service.create_main(
            bootstrapped_root,
            "default",
            _create_payload(
                current_lineset=current,
                lineset_history=history,
            ),
        )
        # Round-trip through disk via get.
        fetched = main_service.get_main(
            bootstrapped_root, "default", created.id
        )
        assert fetched.current_lineset == current
        assert fetched.lineset_history == history


class TestGet:
    def test_round_trips_full_field_set(self, bootstrapped_root: Path):
        created = main_service.create_main(
            bootstrapped_root,
            "default",
            _create_payload(
                jump_count_initial=420,
                current_lineset=_sample_lineset(),
            ),
        )
        # R.2.0c.iii.b: pre-assign via the rig_service-owned seam.
        rig_id = uuid4()
        main_service.set_assigned_rig_id(
            bootstrapped_root, created.id, rig_id
        )
        fetched = main_service.get_main(
            bootstrapped_root, "default", created.id
        )
        assert fetched.assigned_rig_id == rig_id
        assert fetched.jump_count_initial == 420
        assert fetched.current_lineset == created.current_lineset

    def test_missing_id_raises_not_found(self, bootstrapped_root: Path):
        with pytest.raises(NotFoundError):
            main_service.get_main(bootstrapped_root, "default", uuid4())

    def test_corrupt_xml_raises_validation_failed(
        self, bootstrapped_root: Path
    ):
        bad_id = uuid4()
        path = bootstrapped_root / "inventory" / "mains" / f"{bad_id}.xml"
        path.write_bytes(b"<broken>")
        with pytest.raises(ValidationFailedError):
            main_service.get_main(bootstrapped_root, "default", bad_id)


class TestStructuredLoggingExtraKeys:
    def test_create_main_log_has_no_collision(
        self, bootstrapped_root: Path, caplog
    ):
        caplog.set_level(logging.INFO, logger="backend.services.main")
        main_service.create_main(
            bootstrapped_root, "default", _create_payload()
        )
        assert any(r.message == "main_created" for r in caplog.records)

    def test_update_main_log_has_no_collision(
        self, bootstrapped_root: Path, caplog
    ):
        created = main_service.create_main(
            bootstrapped_root, "default", _create_payload()
        )
        caplog.set_level(logging.INFO, logger="backend.services.main")
        main_service.update_main(
            bootstrapped_root, "default", created.id, _update_payload()
        )
        assert any(r.message == "main_updated" for r in caplog.records)

    def test_delete_main_log_has_no_collision(
        self, bootstrapped_root: Path, caplog
    ):
        created = main_service.create_main(
            bootstrapped_root, "default", _create_payload()
        )
        caplog.set_level(logging.INFO, logger="backend.services.main")
        main_service.delete_main(
            bootstrapped_root, "default", created.id
        )
        assert any(r.message == "main_deleted" for r in caplog.records)


# --------------------------------------------------------------------------- #
# list_mains / update_main / delete_main (R.1d)
# --------------------------------------------------------------------------- #

def _update_payload(**overrides) -> MainUpdate:
    base = {
        "status": ComponentStatus.RETIRED,
        "manufacturer": "Performance Designs",
        "model": "Sabre 2",
        "serial": "S2-987654",
        "size_sqft": 170.0,
        "date_of_manufacture": date(2018, 6, 15),
        "default_environment": Environment.DUST_SAND_SALT,
    }
    base.update(overrides)
    return MainUpdate(**base)


class TestList:
    def test_empty_returns_empty(self, bootstrapped_root: Path):
        assert main_service.list_mains(bootstrapped_root, "default") == []

    def test_lists_every_main(self, bootstrapped_root: Path):
        ids = set()
        for n in range(3):
            m = main_service.create_main(
                bootstrapped_root,
                "default",
                _create_payload(serial=f"S2-{n}"),
            )
            ids.add(m.id)
        listed = main_service.list_mains(bootstrapped_root, "default")
        assert {m.id for m in listed} == ids

    def test_orders_by_created_at_descending(self, bootstrapped_root: Path):
        first = main_service.create_main(
            bootstrapped_root, "default", _create_payload(serial="A")
        )
        time.sleep(0.005)
        second = main_service.create_main(
            bootstrapped_root, "default", _create_payload(serial="B")
        )
        time.sleep(0.005)
        third = main_service.create_main(
            bootstrapped_root, "default", _create_payload(serial="C")
        )
        listed = main_service.list_mains(bootstrapped_root, "default")
        assert [m.id for m in listed] == [third.id, second.id, first.id]


class TestUpdate:
    def test_full_replace(self, bootstrapped_root: Path):
        created = main_service.create_main(
            bootstrapped_root, "default", _create_payload()
        )
        updated = main_service.update_main(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload(model="Sabre 3"),
        )
        assert updated.model == "Sabre 3"
        assert updated.status == ComponentStatus.RETIRED

    def test_lineset_state_round_trips_through_update(
        self, bootstrapped_root: Path
    ):
        # The "manual reline via PUT" flow: send a Main with the
        # previous current_lineset moved into lineset_history and a
        # new current_lineset installed. The service writes whatever
        # the client sends; the dedicated reline workflow lands later.
        previous_ls = _sample_lineset(install_date=date(2020, 1, 1))
        created = main_service.create_main(
            bootstrapped_root,
            "default",
            _create_payload(current_lineset=previous_ls),
        )
        new_ls = _sample_lineset(
            line_type="HMA-500",
            breaking_strength_lb=500.0,
            install_date=date(2026, 4, 28),
        )
        updated = main_service.update_main(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload(
                current_lineset=new_ls,
                lineset_history=[previous_ls],
            ),
        )
        assert updated.current_lineset == new_ls
        assert updated.lineset_history == [previous_ls]

    def test_lineset_id_must_be_echoed_to_preserve(
        self, bootstrapped_root: Path
    ):
        # Pin the docstring's contract: the service does not magically
        # merge the on-disk lineset id. If the client wants to keep
        # the same UUID through an update, they have to echo it.
        original_ls = _sample_lineset()
        original_ls_id = original_ls.id
        created = main_service.create_main(
            bootstrapped_root,
            "default",
            _create_payload(current_lineset=original_ls),
        )
        # Sending a Lineset without an explicit id gets a fresh uuid4
        # from the model's default_factory. The update WILL succeed but
        # the new lineset has a different id.
        replacement_ls = _sample_lineset()
        updated = main_service.update_main(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload(current_lineset=replacement_ls),
        )
        assert updated.current_lineset is not None
        assert updated.current_lineset.id != original_ls_id

    def test_missing_id_raises_not_found(self, bootstrapped_root: Path):
        with pytest.raises(NotFoundError):
            main_service.update_main(
                bootstrapped_root, "default", uuid4(), _update_payload()
            )


class TestDelete:
    def test_moves_file_to_trash(self, bootstrapped_root: Path):
        created = main_service.create_main(
            bootstrapped_root, "default", _create_payload()
        )
        original = (
            bootstrapped_root / "inventory" / "mains" / f"{created.id}.xml"
        )
        assert original.is_file()
        trashed = main_service.delete_main(
            bootstrapped_root, "default", created.id
        )
        assert not original.exists()
        assert trashed.parent.parent == (
            bootstrapped_root / ".trash" / "inventory" / "mains"
        )

    def test_subsequent_get_raises_not_found(self, bootstrapped_root: Path):
        created = main_service.create_main(
            bootstrapped_root, "default", _create_payload()
        )
        main_service.delete_main(bootstrapped_root, "default", created.id)
        with pytest.raises(NotFoundError):
            main_service.get_main(
                bootstrapped_root, "default", created.id
            )

    def test_missing_id_raises_not_found(self, bootstrapped_root: Path):
        with pytest.raises(NotFoundError):
            main_service.delete_main(
                bootstrapped_root, "default", uuid4()
            )
