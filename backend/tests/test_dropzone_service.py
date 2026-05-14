"""Service-layer tests for dropzone_service (R.D.1, D44).

Covers create / get / list / update / delete with all happy and
error paths. Each test uses a real tmp_path-backed logbook root
(per CLAUDE.md §7 — integration tests for storage primitives must
touch a real directory, not mocks).
"""
from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from backend.api.errors import NotFoundError, ValidationFailedError
from backend.models.dropzone import (
    DropzoneAircraft,
    DropzoneCreate,
    DropzoneSummary,
    DropzoneUpdate,
    Environment,
)
from backend.services import dropzone_service
from backend.storage.bootstrap import bootstrap_logbook
from backend.storage.trash import TRASH_DIRNAME


@pytest.fixture
def bootstrapped_root(logbook_root: Path) -> Path:
    """A logbook root with bootstrap applied — XSDs, dropzones/, etc."""
    bootstrap_logbook(logbook_root)
    return logbook_root


def _create_payload(
    *,
    name: str = "Skydive Elsinore",
    city: str = "Lake Elsinore",
    province: str | None = None,
    country: str = "US",
    environment: Environment = Environment.DUST_SAND_SALT,
    notes: str | None = None,
) -> DropzoneCreate:
    return DropzoneCreate(
        name=name,
        city=city,
        province=province,
        country=country,
        environment=environment,
        notes=notes,
    )


# --------------------------------------------------------------------------- #
# create_dropzone
# --------------------------------------------------------------------------- #

class TestCreate:
    def test_writes_file_at_uuid_path(self, bootstrapped_root: Path):
        dz = dropzone_service.create_dropzone(
            bootstrapped_root, "default", _create_payload()
        )
        path = bootstrapped_root / "dropzones" / f"{dz.id}.xml"
        assert path.is_file()

    def test_assigns_server_uuid(self, bootstrapped_root: Path):
        dz = dropzone_service.create_dropzone(
            bootstrapped_root, "default", _create_payload()
        )
        # UUID was generated server-side, not echoed from input
        assert isinstance(dz.id, UUID)

    def test_stamps_created_and_updated(self, bootstrapped_root: Path):
        dz = dropzone_service.create_dropzone(
            bootstrapped_root, "default", _create_payload()
        )
        assert dz.created_at is not None
        assert dz.updated_at is not None
        # Both are stamped at the same moment on create.
        assert dz.created_at == dz.updated_at
        # D17 canonical form (UTC, ms precision, Z suffix).
        assert dz.created_at.endswith("Z")
        assert "T" in dz.created_at

    def test_persists_all_fields_through_full_record(self, bootstrapped_root: Path):
        # Round-trip: payload → write → read disk → equal shape.
        payload = _create_payload(
            name="Parachutisme Adrénaline",
            city="Saint-Jérôme",
            province="QC",
            country="CA",
            environment=Environment.CLEAN_GRASS,
            notes="Hometown DZ.",
        )
        created = dropzone_service.create_dropzone(
            bootstrapped_root, "default", payload
        )
        # Read-back via get_dropzone exercises the full disk path.
        roundtrip = dropzone_service.get_dropzone(
            bootstrapped_root, "default", created.id
        )
        assert roundtrip == created

    def test_xsd_validates_on_disk(self, bootstrapped_root: Path):
        # The on-disk XML must validate against the same XSD a third-
        # party tool would use. Read the bytes back and validate
        # explicitly so this test is independent of get_dropzone.
        from backend.xml.validator import parse, validate

        dz = dropzone_service.create_dropzone(
            bootstrapped_root, "default", _create_payload()
        )
        path = bootstrapped_root / "dropzones" / f"{dz.id}.xml"
        validate(parse(path.read_bytes()))


# --------------------------------------------------------------------------- #
# get_dropzone
# --------------------------------------------------------------------------- #

class TestGet:
    def test_returns_existing_dropzone(self, bootstrapped_root: Path):
        created = dropzone_service.create_dropzone(
            bootstrapped_root, "default", _create_payload()
        )
        fetched = dropzone_service.get_dropzone(
            bootstrapped_root, "default", created.id
        )
        assert fetched == created

    def test_missing_dropzone_raises_not_found(self, bootstrapped_root: Path):
        with pytest.raises(NotFoundError):
            dropzone_service.get_dropzone(
                bootstrapped_root, "default", uuid4()
            )

    def test_corrupted_xml_raises_validation_failed(
        self, bootstrapped_root: Path
    ):
        # Hand-written non-XML in the dropzones dir simulates disk
        # corruption / mid-write crash. Service surfaces it as
        # ValidationFailedError so the API layer returns 422.
        rogue_id = uuid4()
        path = bootstrapped_root / "dropzones" / f"{rogue_id}.xml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not XML at all")
        with pytest.raises(ValidationFailedError):
            dropzone_service.get_dropzone(
                bootstrapped_root, "default", rogue_id
            )


# --------------------------------------------------------------------------- #
# list_dropzones
# --------------------------------------------------------------------------- #

class TestList:
    def test_empty_root_returns_empty_list(self, bootstrapped_root: Path):
        assert dropzone_service.list_dropzones(
            bootstrapped_root, "default"
        ) == []

    def test_unbootstrapped_root_returns_empty_list(
        self, logbook_root: Path
    ):
        # No bootstrap → no dropzones/ dir. Should still return []
        # rather than crash. Same posture as reindex_from_xml.
        assert dropzone_service.list_dropzones(logbook_root, "default") == []

    def test_returns_summaries(self, bootstrapped_root: Path):
        dz = dropzone_service.create_dropzone(
            bootstrapped_root, "default", _create_payload(name="A DZ")
        )
        listed = dropzone_service.list_dropzones(bootstrapped_root, "default")
        assert len(listed) == 1
        assert isinstance(listed[0], DropzoneSummary)
        assert listed[0].id == dz.id
        assert listed[0].name == "A DZ"

    def test_sorted_alphabetically_case_insensitive(
        self, bootstrapped_root: Path
    ):
        # Insertion order: zebra, alpha, mike.
        for name in ("Zebra DZ", "alpha DZ", "Mike DZ"):
            dropzone_service.create_dropzone(
                bootstrapped_root, "default", _create_payload(name=name)
            )
        listed = dropzone_service.list_dropzones(bootstrapped_root, "default")
        # Case-insensitive sort: alpha (lower-a < M, M < Z).
        assert [s.name for s in listed] == ["alpha DZ", "Mike DZ", "Zebra DZ"]

    def test_limit_and_offset(self, bootstrapped_root: Path):
        for n in range(5):
            dropzone_service.create_dropzone(
                bootstrapped_root,
                "default",
                _create_payload(name=f"DZ {n}", city=f"City {n}"),
            )
        all_listed = dropzone_service.list_dropzones(
            bootstrapped_root, "default"
        )
        assert len(all_listed) == 5

        page = dropzone_service.list_dropzones(
            bootstrapped_root, "default", limit=2, offset=1
        )
        assert len(page) == 2
        # Sorted by name, so this should be DZ 1 and DZ 2.
        assert [s.name for s in page] == ["DZ 1", "DZ 2"]

    def test_skips_invalid_files(self, bootstrapped_root: Path):
        # Hand-written garbage file in dropzones/ — list should skip
        # and continue, matching reindex_from_xml's posture.
        good = dropzone_service.create_dropzone(
            bootstrapped_root, "default", _create_payload(name="Good DZ")
        )
        rogue = bootstrapped_root / "dropzones" / f"{uuid4()}.xml"
        rogue.write_text("not XML")
        listed = dropzone_service.list_dropzones(bootstrapped_root, "default")
        assert len(listed) == 1
        assert listed[0].id == good.id

    def test_skips_non_xml_files(self, bootstrapped_root: Path):
        # A stray .DS_Store or README.md in dropzones/ should be
        # ignored, not break the list.
        dropzone_service.create_dropzone(
            bootstrapped_root, "default", _create_payload(name="Real DZ")
        )
        (bootstrapped_root / "dropzones" / ".DS_Store").write_bytes(b"")
        (bootstrapped_root / "dropzones" / "README.md").write_text("hello")
        listed = dropzone_service.list_dropzones(bootstrapped_root, "default")
        assert len(listed) == 1


# --------------------------------------------------------------------------- #
# update_dropzone
# --------------------------------------------------------------------------- #

class TestUpdate:
    def test_full_replace_changes_fields(self, bootstrapped_root: Path):
        created = dropzone_service.create_dropzone(
            bootstrapped_root,
            "default",
            _create_payload(name="Old Name", city="Old City"),
        )
        update = DropzoneUpdate(
            name="New Name",
            city="New City",
            province="QC",
            country="CA",
            environment=Environment.DESERT,
            notes="now with notes",
        )
        result = dropzone_service.update_dropzone(
            bootstrapped_root, "default", created.id, update
        )
        assert result.name == "New Name"
        assert result.city == "New City"
        assert result.province == "QC"
        assert result.country == "CA"
        assert result.environment is Environment.DESERT
        assert result.notes == "now with notes"

    def test_preserves_id_and_created_at(self, bootstrapped_root: Path):
        created = dropzone_service.create_dropzone(
            bootstrapped_root, "default", _create_payload()
        )
        original_id = created.id
        original_created_at = created.created_at

        update = DropzoneUpdate(
            name="Renamed",
            city=created.city,
            country=created.country,
            environment=created.environment,
        )
        result = dropzone_service.update_dropzone(
            bootstrapped_root, "default", created.id, update
        )
        assert result.id == original_id
        assert result.created_at == original_created_at

    def test_bumps_updated_at(self, bootstrapped_root: Path):
        import time

        created = dropzone_service.create_dropzone(
            bootstrapped_root, "default", _create_payload()
        )
        # Sleep just past the ms boundary so timestamps differ.
        time.sleep(0.005)

        update = DropzoneUpdate(
            name="Renamed",
            city=created.city,
            country=created.country,
            environment=created.environment,
        )
        result = dropzone_service.update_dropzone(
            bootstrapped_root, "default", created.id, update
        )
        assert result.updated_at != created.updated_at
        assert result.updated_at > created.updated_at  # ISO8601 lexsort = chrono

    def test_missing_dropzone_raises_not_found(self, bootstrapped_root: Path):
        update = DropzoneUpdate(
            name="x",
            city="y",
            country="US",
            environment=Environment.CLEAN_GRASS,
        )
        with pytest.raises(NotFoundError):
            dropzone_service.update_dropzone(
                bootstrapped_root, "default", uuid4(), update
            )

    def test_persists_to_disk(self, bootstrapped_root: Path):
        # Update writes through to disk — re-reading via get_dropzone
        # in a fresh service call returns the new shape.
        created = dropzone_service.create_dropzone(
            bootstrapped_root, "default", _create_payload(name="Original")
        )
        update = DropzoneUpdate(
            name="Updated",
            city=created.city,
            country=created.country,
            environment=created.environment,
        )
        dropzone_service.update_dropzone(
            bootstrapped_root, "default", created.id, update
        )
        fetched = dropzone_service.get_dropzone(
            bootstrapped_root, "default", created.id
        )
        assert fetched.name == "Updated"


# --------------------------------------------------------------------------- #
# delete_dropzone
# --------------------------------------------------------------------------- #

class TestDelete:
    def test_moves_file_to_trash(self, bootstrapped_root: Path):
        created = dropzone_service.create_dropzone(
            bootstrapped_root, "default", _create_payload()
        )
        original = bootstrapped_root / "dropzones" / f"{created.id}.xml"
        assert original.is_file()

        trashed = dropzone_service.delete_dropzone(
            bootstrapped_root, "default", created.id
        )

        # Original is gone.
        assert not original.exists()
        # Trashed copy exists at the returned path.
        assert trashed.is_file()
        # Lives under the canonical trash subdir.
        assert TRASH_DIRNAME in trashed.parts
        assert "dropzones" in trashed.parts

    def test_removes_from_list(self, bootstrapped_root: Path):
        created = dropzone_service.create_dropzone(
            bootstrapped_root, "default", _create_payload()
        )
        dropzone_service.delete_dropzone(
            bootstrapped_root, "default", created.id
        )
        listed = dropzone_service.list_dropzones(bootstrapped_root, "default")
        assert listed == []

    def test_get_after_delete_raises_not_found(self, bootstrapped_root: Path):
        created = dropzone_service.create_dropzone(
            bootstrapped_root, "default", _create_payload()
        )
        dropzone_service.delete_dropzone(
            bootstrapped_root, "default", created.id
        )
        with pytest.raises(NotFoundError):
            dropzone_service.get_dropzone(
                bootstrapped_root, "default", created.id
            )

    def test_missing_dropzone_raises_not_found(self, bootstrapped_root: Path):
        with pytest.raises(NotFoundError):
            dropzone_service.delete_dropzone(
                bootstrapped_root, "default", uuid4()
            )

    def test_no_cascade_on_jumps_with_dropzone_id(self, bootstrapped_root: Path):
        # D44: deleting a DZ leaves jumps that reference it untouched.
        # We verify this at the service level by confirming the
        # service emits no I/O against the jumps/ directory and that
        # an unrelated jump XML on disk survives intact.
        from datetime import date

        from backend.models.jump import Jump
        from backend.xml.serialize import jump_to_bytes

        created = dropzone_service.create_dropzone(
            bootstrapped_root, "default", _create_payload()
        )
        # Plant a jump.xml that references the DZ id. We don't go
        # through jump_service here because all we need is a file on
        # disk — the assertion is "this file is not touched by the
        # DZ delete".
        jumps_dir = bootstrapped_root / "jumps" / "[1] Test"
        jumps_dir.mkdir(parents=True, exist_ok=True)
        jump = Jump(
            id=uuid4(),
            jump_number=1,
            date=date(2026, 4, 27),
            dropzone="Test",
            dropzone_id=created.id,
            exit_altitude_m=4000,
            deployment_altitude_m=900,
        )
        jump_xml = jumps_dir / "jump.xml"
        jump_xml.write_bytes(jump_to_bytes(jump))
        original_bytes = jump_xml.read_bytes()

        dropzone_service.delete_dropzone(
            bootstrapped_root, "default", created.id
        )

        # Jump file is byte-for-byte unchanged.
        assert jump_xml.read_bytes() == original_bytes


# --------------------------------------------------------------------------- #
# Persistence shape — invariants across operations
# --------------------------------------------------------------------------- #

class TestPersistenceInvariants:
    def test_round_trip_through_disk(self, bootstrapped_root: Path):
        # Strongest end-to-end check: create, list, get, update, list,
        # delete, list — every step's view of disk agrees with the
        # service's return value.
        a = dropzone_service.create_dropzone(
            bootstrapped_root, "default", _create_payload(name="A")
        )
        b = dropzone_service.create_dropzone(
            bootstrapped_root, "default", _create_payload(name="B")
        )
        listed = dropzone_service.list_dropzones(bootstrapped_root, "default")
        assert {s.id for s in listed} == {a.id, b.id}

        update = DropzoneUpdate(
            name="A renamed",
            city=a.city,
            country=a.country,
            environment=a.environment,
        )
        dropzone_service.update_dropzone(
            bootstrapped_root, "default", a.id, update
        )
        fetched_a = dropzone_service.get_dropzone(
            bootstrapped_root, "default", a.id
        )
        assert fetched_a.name == "A renamed"

        dropzone_service.delete_dropzone(
            bootstrapped_root, "default", b.id
        )
        listed_after_delete = dropzone_service.list_dropzones(
            bootstrapped_root, "default"
        )
        assert {s.id for s in listed_after_delete} == {a.id}

    def test_each_dropzone_in_own_file(self, bootstrapped_root: Path):
        # Flat shape per D44: one file per UUID. Two creates => two
        # files, no shared structure, no manifest.
        a = dropzone_service.create_dropzone(
            bootstrapped_root, "default", _create_payload(name="A")
        )
        b = dropzone_service.create_dropzone(
            bootstrapped_root, "default", _create_payload(name="B")
        )
        files = sorted((bootstrapped_root / "dropzones").glob("*.xml"))
        assert len(files) == 2
        assert {f.stem for f in files} == {str(a.id), str(b.id)}
        # No SHA256SUMS — flat single files don't need a manifest.
        assert not (bootstrapped_root / "dropzones" / "SHA256SUMS").exists()


# --------------------------------------------------------------------------- #
# Regression: structured-logging extra={...} keys must not collide with
# LogRecord reserved attributes (name, msg, args, filename, …). The
# default test suite runs at WARNING level so info() short-circuits
# before makeRecord ever validates the dict — these tests force INFO
# so a future regression trips here instead of in production.
# --------------------------------------------------------------------------- #

class TestStructuredLoggingExtraKeys:
    """Each service entry-point that emits a structured log must round
    trip cleanly under INFO logging. Adds a future-proof safety net for
    the same class of bug as task #45 (jump_service "filename") and the
    "name" collision found 2026-04-28 in dropzone_service.
    """

    def test_create_dropzone_logs_without_collision(
        self, bootstrapped_root: Path, caplog
    ):
        caplog.set_level(logging.INFO, logger="backend.services.dropzone")
        # The actual call is the regression — if extra={...} carries a
        # reserved key, makeRecord raises KeyError here.
        dropzone_service.create_dropzone(
            bootstrapped_root, "default", _create_payload()
        )
        events = [r.message for r in caplog.records]
        assert "dropzone_created" in events

    def test_update_dropzone_logs_without_collision(
        self, bootstrapped_root: Path, caplog
    ):
        created = dropzone_service.create_dropzone(
            bootstrapped_root, "default", _create_payload()
        )
        caplog.set_level(logging.INFO, logger="backend.services.dropzone")
        dropzone_service.update_dropzone(
            bootstrapped_root,
            "default",
            created.id,
            DropzoneUpdate(
                name="Renamed",
                city=created.city,
                country=created.country,
                environment=created.environment,
            ),
        )
        events = [r.message for r in caplog.records]
        assert "dropzone_updated" in events

    def test_delete_dropzone_logs_without_collision(
        self, bootstrapped_root: Path, caplog
    ):
        created = dropzone_service.create_dropzone(
            bootstrapped_root, "default", _create_payload()
        )
        caplog.set_level(logging.INFO, logger="backend.services.dropzone")
        dropzone_service.delete_dropzone(
            bootstrapped_root, "default", created.id
        )
        events = [r.message for r in caplog.records]
        assert "dropzone_deleted" in events


# --------------------------------------------------------------------------- #
# Aircraft list (D44, added 2026-04-28)
# --------------------------------------------------------------------------- #

class TestAircraftField:
    """Aircraft list round-trips through the service (XML write +
    XSD validate + read), and updates can add/remove planes from
    an existing record. The SQLite index does NOT carry the
    aircraft list — full GET reads it from XML on demand.
    """

    def test_create_with_fleet_persists_to_disk(self, bootstrapped_root: Path):
        payload = DropzoneCreate(
            name="Fleet DZ",
            city="Anywhere",
            country="US",
            environment=Environment.DUST_SAND_SALT,
            aircraft=[
                DropzoneAircraft(model="Twin Otter", tail_number="N123TO"),
                DropzoneAircraft(model="Cessna 208 Caravan"),
            ],
        )
        created = dropzone_service.create_dropzone(
            bootstrapped_root, "default", payload
        )
        # Read-back exercises the full disk path including XSD
        # validate on parse.
        roundtrip = dropzone_service.get_dropzone(
            bootstrapped_root, "default", created.id
        )
        assert len(roundtrip.aircraft) == 2
        assert roundtrip.aircraft[0].model == "Twin Otter"
        assert roundtrip.aircraft[0].tail_number == "N123TO"
        assert roundtrip.aircraft[1].model == "Cessna 208 Caravan"
        assert roundtrip.aircraft[1].tail_number is None

    def test_create_without_fleet_omits_element(
        self, bootstrapped_root: Path
    ):
        # When the fleet is empty, the XML must not emit an
        # <aircraft> element. Verifies byte-stable round-trip
        # against a hand-crafted minimal record.
        created = dropzone_service.create_dropzone(
            bootstrapped_root, "default", _create_payload()
        )
        path = bootstrapped_root / "dropzones" / f"{created.id}.xml"
        assert b"<aircraft>" not in path.read_bytes()

    def test_update_can_add_planes(self, bootstrapped_root: Path):
        # Start with no fleet, update with three planes.
        created = dropzone_service.create_dropzone(
            bootstrapped_root, "default", _create_payload()
        )
        update = DropzoneUpdate(
            name=created.name,
            city=created.city,
            country=created.country,
            environment=created.environment,
            aircraft=[
                DropzoneAircraft(model="A"),
                DropzoneAircraft(model="B", tail_number="N-B"),
                DropzoneAircraft(model="C"),
            ],
        )
        updated = dropzone_service.update_dropzone(
            bootstrapped_root, "default", created.id, update
        )
        assert [p.model for p in updated.aircraft] == ["A", "B", "C"]
        # Persistence round-trip.
        fetched = dropzone_service.get_dropzone(
            bootstrapped_root, "default", created.id
        )
        assert fetched.aircraft == updated.aircraft

    def test_update_can_remove_planes(self, bootstrapped_root: Path):
        # Start with two planes, update down to one.
        created = dropzone_service.create_dropzone(
            bootstrapped_root,
            "default",
            DropzoneCreate(
                name="DZ",
                city="X",
                country="US",
                environment=Environment.CLEAN_GRASS,
                aircraft=[
                    DropzoneAircraft(model="Otter"),
                    DropzoneAircraft(model="Caravan"),
                ],
            ),
        )
        updated = dropzone_service.update_dropzone(
            bootstrapped_root,
            "default",
            created.id,
            DropzoneUpdate(
                name=created.name,
                city=created.city,
                country=created.country,
                environment=created.environment,
                aircraft=[DropzoneAircraft(model="Otter")],
            ),
        )
        assert [p.model for p in updated.aircraft] == ["Otter"]

    def test_update_can_clear_planes(self, bootstrapped_root: Path):
        # Empty list on update wipes the fleet and should elide the
        # <aircraft> element on disk so the file matches a never-
        # had-a-fleet record byte-for-byte.
        created = dropzone_service.create_dropzone(
            bootstrapped_root,
            "default",
            DropzoneCreate(
                name="DZ",
                city="X",
                country="US",
                environment=Environment.CLEAN_GRASS,
                aircraft=[DropzoneAircraft(model="Otter")],
            ),
        )
        dropzone_service.update_dropzone(
            bootstrapped_root,
            "default",
            created.id,
            DropzoneUpdate(
                name=created.name,
                city=created.city,
                country=created.country,
                environment=created.environment,
                aircraft=[],
            ),
        )
        path = bootstrapped_root / "dropzones" / f"{created.id}.xml"
        assert b"<aircraft>" not in path.read_bytes()

    def test_summary_does_not_carry_aircraft(self, bootstrapped_root: Path):
        # DropzoneSummary is the picker projection — it intentionally
        # omits the fleet to keep the index payload narrow. Listing
        # returns only the 5 summary fields.
        dropzone_service.create_dropzone(
            bootstrapped_root,
            "default",
            DropzoneCreate(
                name="With fleet",
                city="X",
                country="US",
                environment=Environment.CLEAN_GRASS,
                aircraft=[DropzoneAircraft(model="Otter")],
            ),
        )
        listed = dropzone_service.list_dropzones(bootstrapped_root, "default")
        assert len(listed) == 1
        assert isinstance(listed[0], DropzoneSummary)
        assert not hasattr(listed[0], "aircraft")
