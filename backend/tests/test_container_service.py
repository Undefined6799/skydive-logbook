"""Service-layer tests for container_service (R.0.3b + R.1a, D33, D34).

R.0.3b covered create + get; R.1a extends this file with list /
update / delete. Each test uses a real tmp_path-backed logbook root
per CLAUDE.md §7 (integration tests for storage primitives must
touch a real directory, not mocks).
"""
from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from backend.api.errors import ComponentInUse, NotFoundError, ValidationFailedError
from backend.models._component_base import (
    ComponentStatus,
    NotesLogEntry,
)
from backend.models.container import ContainerCreate, ContainerUpdate
from backend.services import container_service
from backend.storage.bootstrap import bootstrap_logbook


@pytest.fixture
def bootstrapped_root(logbook_root: Path) -> Path:
    """A logbook root with bootstrap applied — XSDs, inventory/*, etc."""
    bootstrap_logbook(logbook_root)
    return logbook_root


def _create_payload(**overrides) -> ContainerCreate:
    """Convenience builder. Defaults to a defensible used-gear shape;
    tests override only what they're exercising."""
    base = {
        "manufacturer": "Sun Path",
        "model": "Javelin Odyssey",
        "serial": "OD-12345",
        "size": "M22",
        "jump_count_initial": 750,
    }
    base.update(overrides)
    return ContainerCreate(**base)


# --------------------------------------------------------------------------- #
# create_container
# --------------------------------------------------------------------------- #

class TestCreate:
    def test_writes_file_at_uuid_path(self, bootstrapped_root: Path):
        c = container_service.create_container(
            bootstrapped_root, "default", _create_payload()
        )
        path = bootstrapped_root / "inventory" / "containers" / f"{c.id}.xml"
        assert path.is_file(), f"expected container at {path}"

    def test_assigns_server_uuid(self, bootstrapped_root: Path):
        # UUID is server-assigned. ContainerCreate has no `id` field
        # so the caller can't even try to send one.
        c = container_service.create_container(
            bootstrapped_root, "default", _create_payload()
        )
        assert isinstance(c.id, UUID)

    def test_two_creates_get_distinct_ids(self, bootstrapped_root: Path):
        a = container_service.create_container(
            bootstrapped_root, "default", _create_payload()
        )
        b = container_service.create_container(
            bootstrapped_root, "default", _create_payload()
        )
        assert a.id != b.id

    def test_stamps_created_and_updated_at_same_moment(
        self, bootstrapped_root: Path
    ):
        c = container_service.create_container(
            bootstrapped_root, "default", _create_payload()
        )
        assert c.created_at is not None
        assert c.updated_at is not None
        # On create, both timestamps are stamped together — same value.
        assert c.created_at == c.updated_at

    def test_default_status_is_active(self, bootstrapped_root: Path):
        # ContainerCreate's status defaults to ACTIVE.
        c = container_service.create_container(
            bootstrapped_root, "default", _create_payload()
        )
        assert c.status == ComponentStatus.ACTIVE

    def test_explicit_status_preserved(self, bootstrapped_root: Path):
        c = container_service.create_container(
            bootstrapped_root,
            "default",
            _create_payload(status=ComponentStatus.OUT_OF_SERVICE),
        )
        assert c.status == ComponentStatus.OUT_OF_SERVICE

    def test_minimal_payload_creates_clean(self, bootstrapped_root: Path):
        # Bare-minimum container — no identification fields, no DOM.
        c = container_service.create_container(
            bootstrapped_root, "default", ContainerCreate()
        )
        assert c.manufacturer is None
        assert c.model is None
        assert c.size is None
        assert c.jump_count_initial == 0

    def test_notes_log_entries_persist(self, bootstrapped_root: Path):
        entry = NotesLogEntry(
            at="2026-04-28T14:30:00.000Z",
            text="Onboarded — bought used from Bob",
        )
        c = container_service.create_container(
            bootstrapped_root,
            "default",
            _create_payload(notes_log=[entry]),
        )
        assert c.notes_log == [entry]

    def test_create_rejects_assigned_rig_id_field(
        self, bootstrapped_root: Path
    ):
        # R.2.0c.iii.b: assigned_rig_id is rig_service-owned; the
        # field is not on ContainerCreate, and Pydantic's
        # extra="forbid" rejects any body that includes it.
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError):
            _create_payload(assigned_rig_id=uuid4())  # type: ignore[call-arg]

    def test_assigned_rig_id_via_internal_seam(
        self, bootstrapped_root: Path
    ):
        # R.2.0c.iii.a + R.2.0c.iii.b: the only sanctioned write
        # site for assigned_rig_id is set_assigned_rig_id, called
        # by rig_service. Round-trips correctly through the helper.
        c = container_service.create_container(
            bootstrapped_root, "default", _create_payload()
        )
        assert c.assigned_rig_id is None
        rig_id = uuid4()
        updated = container_service.set_assigned_rig_id(
            bootstrapped_root, c.id, rig_id
        )
        assert updated.assigned_rig_id == rig_id
        # And the round-trip from disk confirms it.
        fetched = container_service.get_container(
            bootstrapped_root, "default", c.id
        )
        assert fetched.assigned_rig_id == rig_id


# --------------------------------------------------------------------------- #
# get_container
# --------------------------------------------------------------------------- #

class TestGet:
    def test_round_trips_full_field_set(self, bootstrapped_root: Path):
        notes = [
            NotesLogEntry(
                at="2026-04-28T14:30:00.000Z",
                text="Onboarded",
            )
        ]
        created = container_service.create_container(
            bootstrapped_root,
            "default",
            _create_payload(
                status=ComponentStatus.ACTIVE,
                notes_log=notes,
                manufacturer="UPT",
                model="Vector V3",
                serial="V3-789",
                size="L",
                jump_count_initial=200,
            ),
        )
        # Pre-assign via the rig_service-owned seam (R.2.0c.iii.a/b).
        rig_id = uuid4()
        container_service.set_assigned_rig_id(
            bootstrapped_root, created.id, rig_id
        )
        fetched = container_service.get_container(
            bootstrapped_root, "default", created.id
        )
        assert fetched.assigned_rig_id == rig_id
        # Other fields untouched.
        assert fetched.notes_log == notes
        assert fetched.manufacturer == "UPT"
        assert fetched.model == "Vector V3"
        assert fetched.size == "L"
        assert fetched.jump_count_initial == 200

    def test_missing_id_raises_not_found(self, bootstrapped_root: Path):
        with pytest.raises(NotFoundError):
            container_service.get_container(
                bootstrapped_root, "default", uuid4()
            )

    def test_corrupt_xml_raises_validation_failed(
        self, bootstrapped_root: Path
    ):
        # Hand-write garbage at a UUID path; the get path should
        # surface ValidationFailedError, not crash. This is the same
        # posture as get_dropzone for files corrupted out-of-band.
        bad_id = uuid4()
        path = (
            bootstrapped_root / "inventory" / "containers" / f"{bad_id}.xml"
        )
        path.write_bytes(b"<not><valid></not></valid>")
        with pytest.raises(ValidationFailedError):
            container_service.get_container(bootstrapped_root, "default", bad_id)

    def test_get_after_create_reads_from_disk(self, bootstrapped_root: Path):
        # Sanity check: get is a fresh read off disk, not a cached
        # echo of the created object. Modifying the on-disk file
        # surfaces in get_container's output.
        created = container_service.create_container(
            bootstrapped_root, "default", _create_payload()
        )
        # Read the file back through get and confirm the path was real.
        path = (
            bootstrapped_root
            / "inventory"
            / "containers"
            / f"{created.id}.xml"
        )
        assert path.is_file()
        fetched = container_service.get_container(
            bootstrapped_root, "default", created.id
        )
        assert fetched.id == created.id


# --------------------------------------------------------------------------- #
# Structured logging — guard against extra={"name": ...} collisions
# --------------------------------------------------------------------------- #

class TestStructuredLoggingExtraKeys:
    """Same regression class as task #45 / dropzone hotfix: any
    structured-log call site must not collide with reserved
    ``LogRecord`` field names. Tests run under INFO logging so
    ``makeRecord`` actually validates the dict (otherwise the
    short-circuit on ``isEnabledFor`` masks the collision)."""

    def test_create_container_log_has_no_collision(
        self, bootstrapped_root: Path, caplog
    ):
        caplog.set_level(logging.INFO, logger="backend.services.container")
        # If the service ever uses ``extra={"name": ...}`` again, this
        # raises ``KeyError`` from makeRecord.
        container_service.create_container(
            bootstrapped_root, "default", _create_payload()
        )
        # At least one container_created event made it through.
        assert any(
            r.message == "container_created" for r in caplog.records
        )

    def test_update_container_log_has_no_collision(
        self, bootstrapped_root: Path, caplog
    ):
        created = container_service.create_container(
            bootstrapped_root, "default", _create_payload()
        )
        caplog.set_level(logging.INFO, logger="backend.services.container")
        container_service.update_container(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload(),
        )
        assert any(
            r.message == "container_updated" for r in caplog.records
        )

    def test_delete_container_log_has_no_collision(
        self, bootstrapped_root: Path, caplog
    ):
        created = container_service.create_container(
            bootstrapped_root, "default", _create_payload()
        )
        caplog.set_level(logging.INFO, logger="backend.services.container")
        container_service.delete_container(
            bootstrapped_root, "default", created.id
        )
        assert any(
            r.message == "container_deleted" for r in caplog.records
        )


# --------------------------------------------------------------------------- #
# list_containers (R.1a)
# --------------------------------------------------------------------------- #

def _update_payload(**overrides) -> ContainerUpdate:
    """Convenience builder for ContainerUpdate. Defaults to a status
    other than ACTIVE so update tests can verify the field flowed
    through (RETIRED is a useful default — it's the most common
    transition the user actually makes)."""
    base = {
        "status": ComponentStatus.RETIRED,
        "manufacturer": "Sun Path",
        "model": "Javelin Odyssey",
        "size": "M22",
        "jump_count_initial": 750,
    }
    base.update(overrides)
    return ContainerUpdate(**base)


class TestList:
    def test_empty_returns_empty_list(self, bootstrapped_root: Path):
        # Fresh logbook, no containers yet.
        assert container_service.list_containers(
            bootstrapped_root, "default"
        ) == []

    def test_lists_every_container(self, bootstrapped_root: Path):
        ids = set()
        for n in range(3):
            c = container_service.create_container(
                bootstrapped_root,
                "default",
                _create_payload(serial=f"OD-{n}"),
            )
            ids.add(c.id)
        listed = container_service.list_containers(
            bootstrapped_root, "default"
        )
        assert {c.id for c in listed} == ids

    def test_orders_by_created_at_descending(self, bootstrapped_root: Path):
        # The service stamps via _now_utc_iso() with ms precision, so
        # back-to-back creates can collide on the same timestamp and
        # leave the sort order unstable. Insert small sleeps between
        # creates so each one gets a strictly-greater created_at.
        import time
        first = container_service.create_container(
            bootstrapped_root, "default", _create_payload(serial="A")
        )
        time.sleep(0.005)
        second = container_service.create_container(
            bootstrapped_root, "default", _create_payload(serial="B")
        )
        time.sleep(0.005)
        third = container_service.create_container(
            bootstrapped_root, "default", _create_payload(serial="C")
        )
        listed = container_service.list_containers(
            bootstrapped_root, "default"
        )
        # Most-recently created first.
        assert [c.id for c in listed] == [third.id, second.id, first.id]

    def test_limit_and_offset(self, bootstrapped_root: Path):
        for n in range(5):
            container_service.create_container(
                bootstrapped_root,
                "default",
                _create_payload(serial=f"S-{n}"),
            )
        page = container_service.list_containers(
            bootstrapped_root, "default", limit=2, offset=1
        )
        assert len(page) == 2

    def test_skips_invalid_xml_files(self, bootstrapped_root: Path):
        # Drop a corrupt XML next to a valid container; list should
        # return only the valid one and emit a warning. The list
        # endpoint stays useful even if one file has been corrupted
        # out-of-band.
        good = container_service.create_container(
            bootstrapped_root, "default", _create_payload()
        )
        bad_path = (
            bootstrapped_root / "inventory" / "containers" / f"{uuid4()}.xml"
        )
        bad_path.write_bytes(b"<not-valid></not-valid>")
        listed = container_service.list_containers(
            bootstrapped_root, "default"
        )
        assert {c.id for c in listed} == {good.id}

    def test_missing_dir_returns_empty(self, logbook_root: Path):
        # No bootstrap, no inventory dir at all. Service should
        # return [] rather than raise — the "fresh logbook root"
        # case is a perfectly normal no-op.
        assert container_service.list_containers(
            logbook_root, "default"
        ) == []


# --------------------------------------------------------------------------- #
# update_container (R.1a)
# --------------------------------------------------------------------------- #

class TestUpdate:
    def test_full_replace_persists_to_disk(self, bootstrapped_root: Path):
        created = container_service.create_container(
            bootstrapped_root, "default", _create_payload()
        )
        updated = container_service.update_container(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload(model="Vector V3", size="L"),
        )
        # Returned object reflects the update.
        assert updated.model == "Vector V3"
        assert updated.size == "L"
        # Read back from disk: the change persisted.
        fetched = container_service.get_container(
            bootstrapped_root, "default", created.id
        )
        assert fetched == updated

    def test_preserves_id_and_created_at(self, bootstrapped_root: Path):
        created = container_service.create_container(
            bootstrapped_root, "default", _create_payload()
        )
        # Sleep is unnecessary — _now_utc_iso() has ms precision but
        # the test just checks identity, not strict-greater ordering.
        updated = container_service.update_container(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload(),
        )
        assert updated.id == created.id
        assert updated.created_at == created.created_at

    def test_bumps_updated_at(self, bootstrapped_root: Path):
        import time
        created = container_service.create_container(
            bootstrapped_root, "default", _create_payload()
        )
        # Sleep to ensure a strictly-greater updated_at since
        # _now_utc_iso has ms precision; back-to-back calls might
        # collide on the same ms.
        time.sleep(0.005)
        updated = container_service.update_container(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload(),
        )
        assert updated.updated_at != created.updated_at
        assert updated.updated_at > created.updated_at

    def test_status_change_persists(self, bootstrapped_root: Path):
        # The most common update — retiring a component.
        created = container_service.create_container(
            bootstrapped_root,
            "default",
            _create_payload(status=ComponentStatus.ACTIVE),
        )
        updated = container_service.update_container(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload(status=ComponentStatus.RETIRED),
        )
        assert updated.status == ComponentStatus.RETIRED

    def test_update_rejects_assigned_rig_id_field(
        self, bootstrapped_root: Path
    ):
        # R.2.0c.iii.b: assigned_rig_id is rig_service-owned. The
        # field is not on ContainerUpdate — Pydantic's
        # extra="forbid" rejects any PUT body that includes it.
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError):
            _update_payload(assigned_rig_id=uuid4())  # type: ignore[call-arg]

    def test_update_preserves_on_disk_assigned_rig_id(
        self, bootstrapped_root: Path
    ):
        # R.2.0c.iii.b: assigned_rig_id is preserved across a PUT
        # — the user can edit metadata without disturbing the rig
        # assignment. Status stays ACTIVE so the D37 in-use check
        # doesn't fire.
        rig_id = uuid4()
        created = container_service.create_container(
            bootstrapped_root, "default", _create_payload()
        )
        container_service.set_assigned_rig_id(
            bootstrapped_root, created.id, rig_id
        )
        updated = container_service.update_container(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload(status=ComponentStatus.ACTIVE, model="Vector V3"),
        )
        assert updated.assigned_rig_id == rig_id
        assert updated.model == "Vector V3"

    def test_status_transition_to_retired_while_assigned_raises_409(
        self, bootstrapped_root: Path
    ):
        # R.2.0c.iii.b D37 enforcement: a container on a rig can't
        # transition to non-active via PUT. The user must detach the
        # rig first (rig delete or R.5 repack flow).
        rig_id = uuid4()
        created = container_service.create_container(
            bootstrapped_root, "default", _create_payload()
        )
        container_service.set_assigned_rig_id(
            bootstrapped_root, created.id, rig_id
        )
        with pytest.raises(ComponentInUse) as info:
            container_service.update_container(
                bootstrapped_root,
                "default",
                created.id,
                _update_payload(status=ComponentStatus.RETIRED),
            )
        assert info.value.code == "component_in_use"
        assert info.value.http_status == 409
        # FieldError points at #/status with the rig id in the detail.
        pointers = [e.pointer for e in info.value.errors or []]
        assert "#/status" in pointers

    def test_status_transition_to_sold_while_assigned_raises_409(
        self, bootstrapped_root: Path
    ):
        rig_id = uuid4()
        created = container_service.create_container(
            bootstrapped_root, "default", _create_payload()
        )
        container_service.set_assigned_rig_id(
            bootstrapped_root, created.id, rig_id
        )
        with pytest.raises(ComponentInUse):
            container_service.update_container(
                bootstrapped_root,
                "default",
                created.id,
                _update_payload(status=ComponentStatus.SOLD),
            )

    def test_status_transition_to_oos_while_assigned_raises_409(
        self, bootstrapped_root: Path
    ):
        rig_id = uuid4()
        created = container_service.create_container(
            bootstrapped_root, "default", _create_payload()
        )
        container_service.set_assigned_rig_id(
            bootstrapped_root, created.id, rig_id
        )
        with pytest.raises(ComponentInUse):
            container_service.update_container(
                bootstrapped_root,
                "default",
                created.id,
                _update_payload(status=ComponentStatus.OUT_OF_SERVICE),
            )

    def test_status_transition_while_unassigned_allowed(
        self, bootstrapped_root: Path
    ):
        # Sanity: when the container ISN'T on a rig, retiring it is
        # a normal allowed update.
        created = container_service.create_container(
            bootstrapped_root, "default", _create_payload()
        )
        assert created.assigned_rig_id is None
        updated = container_service.update_container(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload(status=ComponentStatus.RETIRED),
        )
        assert updated.status == ComponentStatus.RETIRED

    def test_active_status_while_assigned_allowed(
        self, bootstrapped_root: Path
    ):
        # Editing other fields without changing status (which stays
        # active) on an assigned component must work — that's the
        # common case for jumpers updating notes_log entries etc.
        rig_id = uuid4()
        created = container_service.create_container(
            bootstrapped_root, "default", _create_payload()
        )
        container_service.set_assigned_rig_id(
            bootstrapped_root, created.id, rig_id
        )
        updated = container_service.update_container(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload(status=ComponentStatus.ACTIVE, model="Vector V3"),
        )
        assert updated.model == "Vector V3"
        assert updated.status == ComponentStatus.ACTIVE
        assert updated.assigned_rig_id == rig_id

    def test_missing_id_raises_not_found(self, bootstrapped_root: Path):
        with pytest.raises(NotFoundError):
            container_service.update_container(
                bootstrapped_root,
                "default",
                uuid4(),
                _update_payload(),
            )


# --------------------------------------------------------------------------- #
# delete_container (R.1a)
# --------------------------------------------------------------------------- #

class TestDelete:
    def test_moves_file_to_trash(self, bootstrapped_root: Path):
        created = container_service.create_container(
            bootstrapped_root, "default", _create_payload()
        )
        original_path = (
            bootstrapped_root
            / "inventory"
            / "containers"
            / f"{created.id}.xml"
        )
        assert original_path.is_file()
        trashed = container_service.delete_container(
            bootstrapped_root, "default", created.id
        )
        # Original gone.
        assert not original_path.exists()
        # Now under .trash/inventory/containers/<ts>_<uuid>.xml/<uuid>.xml.
        assert trashed.is_file()
        assert trashed.parent.parent == (
            bootstrapped_root / ".trash" / "inventory" / "containers"
        )

    def test_subsequent_get_raises_not_found(self, bootstrapped_root: Path):
        created = container_service.create_container(
            bootstrapped_root, "default", _create_payload()
        )
        container_service.delete_container(
            bootstrapped_root, "default", created.id
        )
        with pytest.raises(NotFoundError):
            container_service.get_container(
                bootstrapped_root, "default", created.id
            )

    def test_subsequent_list_omits_trashed(self, bootstrapped_root: Path):
        a = container_service.create_container(
            bootstrapped_root, "default", _create_payload(serial="A")
        )
        b = container_service.create_container(
            bootstrapped_root, "default", _create_payload(serial="B")
        )
        container_service.delete_container(
            bootstrapped_root, "default", a.id
        )
        listed = container_service.list_containers(
            bootstrapped_root, "default"
        )
        assert {c.id for c in listed} == {b.id}

    def test_missing_id_raises_not_found(self, bootstrapped_root: Path):
        with pytest.raises(NotFoundError):
            container_service.delete_container(
                bootstrapped_root, "default", uuid4()
            )
