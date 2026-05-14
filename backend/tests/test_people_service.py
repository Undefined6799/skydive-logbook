"""Service-layer tests for people_service (D54, Phase 2b).

Mirrors test_dropzone_service.py for the same flat-entity shape.
Covers create / get / list / update / delete with happy and error
paths, plus the NFC normalization invariant unique to Person (D4 /
D54: names are normalized at the storage layer on every write).

Each test uses a real tmp_path-backed logbook root (per CLAUDE.md
§7 — integration tests for storage primitives must touch a real
directory, not mocks).
"""
from __future__ import annotations

import logging
import unicodedata
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from backend.api.errors import NotFoundError, ValidationFailedError
from backend.models.person import PersonCreate, PersonSummary, PersonUpdate
from backend.services import people_service
from backend.storage.bootstrap import bootstrap_logbook
from backend.storage.index import open_index
from backend.storage.trash import TRASH_DIRNAME


@pytest.fixture
def bootstrapped_root(logbook_root: Path) -> Path:
    """A logbook root with bootstrap applied — XSDs, people/, etc."""
    bootstrap_logbook(logbook_root)
    # Prime the index so the open_index inside service calls sees
    # the current schema on first call.
    result = open_index(logbook_root)
    result.conn.close()
    return logbook_root


def _create_payload(
    *,
    name: str = "Alice Anderson",
    notes: str | None = None,
) -> PersonCreate:
    return PersonCreate(name=name, notes=notes)


# --------------------------------------------------------------------------- #
# create_person
# --------------------------------------------------------------------------- #

class TestCreate:
    def test_writes_file_at_uuid_path(self, bootstrapped_root: Path):
        p = people_service.create_person(
            bootstrapped_root, "default", _create_payload()
        )
        path = bootstrapped_root / "people" / f"{p.id}.xml"
        assert path.is_file()

    def test_assigns_server_uuid(self, bootstrapped_root: Path):
        p = people_service.create_person(
            bootstrapped_root, "default", _create_payload()
        )
        assert isinstance(p.id, UUID)

    def test_two_creates_get_different_uuids(self, bootstrapped_root: Path):
        p1 = people_service.create_person(
            bootstrapped_root, "default", _create_payload(name="Alice")
        )
        p2 = people_service.create_person(
            bootstrapped_root, "default", _create_payload(name="Bob")
        )
        assert p1.id != p2.id

    def test_stamps_created_and_updated(self, bootstrapped_root: Path):
        p = people_service.create_person(
            bootstrapped_root, "default", _create_payload()
        )
        assert p.created_at is not None
        assert p.updated_at is not None
        # Both stamped at the same moment on create.
        assert p.created_at == p.updated_at
        # D17 canonical form (UTC, ms precision, Z suffix).
        assert p.created_at.endswith("Z")
        assert "T" in p.created_at

    def test_persists_notes_through_full_record(self, bootstrapped_root: Path):
        # Round-trip: payload → write → read disk → equal shape.
        payload = _create_payload(
            name="Bob Builder",
            notes="Packs at Skydive City weekends.",
        )
        created = people_service.create_person(
            bootstrapped_root, "default", payload
        )
        roundtrip = people_service.get_person(
            bootstrapped_root, "default", created.id
        )
        assert roundtrip.name == "Bob Builder"
        assert roundtrip.notes == "Packs at Skydive City weekends."

    def test_index_row_populated(self, bootstrapped_root: Path):
        p = people_service.create_person(
            bootstrapped_root, "default", _create_payload(name="Charlie")
        )
        result = open_index(bootstrapped_root)
        try:
            row = result.conn.execute(
                "SELECT id, name, schema_ns, created_at, updated_at "
                "FROM people WHERE id = ?",
                (str(p.id),),
            ).fetchone()
        finally:
            result.conn.close()
        assert row is not None
        assert row["id"] == str(p.id)
        assert row["name"] == "Charlie"
        assert row["schema_ns"] == "https://skydive-logbook.org/schema/v1"
        assert row["created_at"].endswith("Z")
        assert row["created_at"] == row["updated_at"]

    def test_nfc_normalization_on_write(self, bootstrapped_root: Path):
        # D4 invariant: names land on disk as NFC regardless of input
        # form. NFD ("Café", e + combining acute) must become
        # NFC ("Café", precomposed é) before the write.
        nfd_name = "Caf" + "é"  # 5 codepoints
        nfc_name = "Café"  # 4 codepoints
        # Sanity preconditions on the test inputs themselves.
        assert unicodedata.normalize("NFD", nfc_name) == nfd_name
        assert unicodedata.normalize("NFC", nfd_name) == nfc_name
        assert nfd_name != nfc_name

        p = people_service.create_person(
            bootstrapped_root, "default", _create_payload(name=nfd_name)
        )
        # Returned model carries the NFC form.
        assert p.name == nfc_name
        # Disk file (parsed back) carries the NFC form.
        roundtrip = people_service.get_person(
            bootstrapped_root, "default", p.id
        )
        assert roundtrip.name == nfc_name
        # Belt-and-braces: SQLite index also has the NFC form.
        result = open_index(bootstrapped_root)
        try:
            row = result.conn.execute(
                "SELECT name FROM people WHERE id = ?", (str(p.id),)
            ).fetchone()
        finally:
            result.conn.close()
        assert row["name"] == nfc_name

    def test_unicode_name_round_trips(self, bootstrapped_root: Path):
        p = people_service.create_person(
            bootstrapped_root,
            "default",
            _create_payload(name="Émile Côté"),
        )
        roundtrip = people_service.get_person(
            bootstrapped_root, "default", p.id
        )
        assert roundtrip.name == "Émile Côté"

    def test_emits_person_created_log(
        self, bootstrapped_root: Path, caplog
    ):
        caplog.set_level(logging.INFO, logger="backend.services.people")
        p = people_service.create_person(
            bootstrapped_root, "default", _create_payload(name="Diane")
        )
        records = [r for r in caplog.records if r.message == "person_created"]
        assert len(records) == 1
        assert records[0].person_id == str(p.id)
        assert records[0].person_name == "Diane"

    def test_no_tmp_files_leaked(self, bootstrapped_root: Path):
        people_service.create_person(
            bootstrapped_root, "default", _create_payload()
        )
        tmps = list((bootstrapped_root / "people").rglob("*.tmp"))
        assert tmps == []


# --------------------------------------------------------------------------- #
# get_person
# --------------------------------------------------------------------------- #

class TestGet:
    def test_returns_full_record(self, bootstrapped_root: Path):
        created = people_service.create_person(
            bootstrapped_root,
            "default",
            _create_payload(name="Eve", notes="rigger"),
        )
        fetched = people_service.get_person(
            bootstrapped_root, "default", created.id
        )
        assert fetched == created

    def test_unknown_uuid_raises_not_found(self, bootstrapped_root: Path):
        with pytest.raises(NotFoundError):
            people_service.get_person(
                bootstrapped_root, "default", uuid4()
            )

    def test_corrupted_xml_raises_validation_failed(
        self, bootstrapped_root: Path
    ):
        # Hand-corrupt a person.xml on disk; get_person must surface
        # a 422 (ValidationFailedError) rather than a 500.
        p = people_service.create_person(
            bootstrapped_root, "default", _create_payload()
        )
        path = bootstrapped_root / "people" / f"{p.id}.xml"
        path.write_bytes(b"<not-valid-xml>")
        with pytest.raises(ValidationFailedError):
            people_service.get_person(
                bootstrapped_root, "default", p.id
            )


# --------------------------------------------------------------------------- #
# list_people
# --------------------------------------------------------------------------- #

class TestList:
    def test_empty_logbook_returns_empty_list(self, bootstrapped_root: Path):
        result = people_service.list_people(bootstrapped_root, "default")
        assert result == []

    def test_returns_summaries_in_name_order_case_insensitive(
        self, bootstrapped_root: Path
    ):
        # NOCASE collation: "alice" sorts with "Alice", not after
        # capital "Z".
        for name in ("bob", "Alice", "charlie"):
            people_service.create_person(
                bootstrapped_root, "default", _create_payload(name=name)
            )
        listing = people_service.list_people(bootstrapped_root, "default")
        names = [s.name for s in listing]
        # Sorted by NOCASE — Alice, bob, charlie.
        assert names == ["Alice", "bob", "charlie"]

    def test_returns_summary_shape(self, bootstrapped_root: Path):
        p = people_service.create_person(
            bootstrapped_root,
            "default",
            _create_payload(name="Frank", notes="should not appear"),
        )
        listing = people_service.list_people(bootstrapped_root, "default")
        assert len(listing) == 1
        s = listing[0]
        assert isinstance(s, PersonSummary)
        assert s.id == p.id
        assert s.name == "Frank"
        # Summary is compact — no notes attribute by design.
        assert not hasattr(s, "notes")

    def test_limit_truncates(self, bootstrapped_root: Path):
        for name in ("Alice", "Bob", "Charlie", "Diane"):
            people_service.create_person(
                bootstrapped_root, "default", _create_payload(name=name)
            )
        listing = people_service.list_people(
            bootstrapped_root, "default", limit=2
        )
        assert len(listing) == 2
        assert [s.name for s in listing] == ["Alice", "Bob"]

    def test_offset_skips(self, bootstrapped_root: Path):
        for name in ("Alice", "Bob", "Charlie", "Diane"):
            people_service.create_person(
                bootstrapped_root, "default", _create_payload(name=name)
            )
        listing = people_service.list_people(
            bootstrapped_root, "default", offset=2
        )
        assert [s.name for s in listing] == ["Charlie", "Diane"]


# --------------------------------------------------------------------------- #
# update_person
# --------------------------------------------------------------------------- #

class TestUpdate:
    def test_full_replace_updates_name_and_notes(
        self, bootstrapped_root: Path
    ):
        created = people_service.create_person(
            bootstrapped_root,
            "default",
            _create_payload(name="Gabe", notes="old"),
        )
        updated = people_service.update_person(
            bootstrapped_root,
            "default",
            created.id,
            PersonUpdate(name="Gabriel", notes="new"),
        )
        assert updated.name == "Gabriel"
        assert updated.notes == "new"

    def test_preserves_id_and_created_at(self, bootstrapped_root: Path):
        created = people_service.create_person(
            bootstrapped_root, "default", _create_payload(name="Hank")
        )
        updated = people_service.update_person(
            bootstrapped_root,
            "default",
            created.id,
            PersonUpdate(name="Hank Updated"),
        )
        assert updated.id == created.id
        assert updated.created_at == created.created_at

    def test_bumps_updated_at(self, bootstrapped_root: Path):
        import time

        created = people_service.create_person(
            bootstrapped_root, "default", _create_payload()
        )
        # Force a measurable time delta so the new ISO timestamp can
        # differ from the original at ms granularity.
        time.sleep(0.01)
        updated = people_service.update_person(
            bootstrapped_root,
            "default",
            created.id,
            PersonUpdate(name="Updated"),
        )
        assert updated.updated_at != created.updated_at

    def test_unknown_uuid_raises_not_found(self, bootstrapped_root: Path):
        with pytest.raises(NotFoundError):
            people_service.update_person(
                bootstrapped_root,
                "default",
                uuid4(),
                PersonUpdate(name="ghost"),
            )

    def test_clearing_notes_persists_none(self, bootstrapped_root: Path):
        # Full-replace semantics: passing notes=None drops any
        # previous notes.
        created = people_service.create_person(
            bootstrapped_root,
            "default",
            _create_payload(name="Iris", notes="present"),
        )
        updated = people_service.update_person(
            bootstrapped_root,
            "default",
            created.id,
            PersonUpdate(name="Iris", notes=None),
        )
        assert updated.notes is None
        roundtrip = people_service.get_person(
            bootstrapped_root, "default", created.id
        )
        assert roundtrip.notes is None

    def test_nfc_normalization_on_update(self, bootstrapped_root: Path):
        nfd_name = "Caf" + "é"
        nfc_name = "Café"
        created = people_service.create_person(
            bootstrapped_root, "default", _create_payload(name="Original")
        )
        updated = people_service.update_person(
            bootstrapped_root,
            "default",
            created.id,
            PersonUpdate(name=nfd_name),
        )
        assert updated.name == nfc_name


# --------------------------------------------------------------------------- #
# delete_person
# --------------------------------------------------------------------------- #

class TestDelete:
    def test_moves_file_to_trash(self, bootstrapped_root: Path):
        p = people_service.create_person(
            bootstrapped_root, "default", _create_payload()
        )
        path = bootstrapped_root / "people" / f"{p.id}.xml"
        assert path.is_file()  # precondition

        trashed = people_service.delete_person(
            bootstrapped_root, "default", p.id
        )

        assert not path.exists()  # file moved out of active set
        assert trashed.exists()
        # Trashed under .trash/people/, not the active people/.
        assert TRASH_DIRNAME in trashed.parts
        assert "people" in trashed.parts

    def test_removes_index_row(self, bootstrapped_root: Path):
        p = people_service.create_person(
            bootstrapped_root, "default", _create_payload()
        )
        people_service.delete_person(bootstrapped_root, "default", p.id)
        result = open_index(bootstrapped_root)
        try:
            row = result.conn.execute(
                "SELECT id FROM people WHERE id = ?", (str(p.id),)
            ).fetchone()
        finally:
            result.conn.close()
        assert row is None

    def test_unknown_uuid_raises_not_found(self, bootstrapped_root: Path):
        with pytest.raises(NotFoundError):
            people_service.delete_person(
                bootstrapped_root, "default", uuid4()
            )

    def test_subsequent_get_raises_not_found(self, bootstrapped_root: Path):
        p = people_service.create_person(
            bootstrapped_root, "default", _create_payload()
        )
        people_service.delete_person(bootstrapped_root, "default", p.id)
        with pytest.raises(NotFoundError):
            people_service.get_person(
                bootstrapped_root, "default", p.id
            )


# --------------------------------------------------------------------------- #
# Reindex round-trip — the index is rebuildable from XML (D3)
# --------------------------------------------------------------------------- #

class TestReindexRoundTrip:
    def test_reindex_rebuilds_people_table_from_xml(
        self, bootstrapped_root: Path
    ):
        from backend.services.reindex_service import reindex_from_xml

        people_service.create_person(
            bootstrapped_root, "default", _create_payload(name="Alice")
        )
        people_service.create_person(
            bootstrapped_root, "default", _create_payload(name="Bob")
        )

        # Wipe the people index table to simulate index rot / a fresh
        # DB. The on-disk XML is the source of truth (D3); reindex
        # must rebuild the rows from it.
        result = open_index(bootstrapped_root)
        try:
            result.conn.execute("DELETE FROM people")
            result.conn.commit()
        finally:
            result.conn.close()

        # Sanity: table is empty before reindex.
        listing = people_service.list_people(bootstrapped_root, "default")
        assert listing == []

        report = reindex_from_xml(bootstrapped_root)
        assert report.people_scanned == 2
        assert report.people_indexed == 2
        assert report.people_skipped == []

        # Listing now reflects the rebuilt rows.
        listing_after = people_service.list_people(
            bootstrapped_root, "default"
        )
        assert {s.name for s in listing_after} == {"Alice", "Bob"}

    def test_reindex_skips_invalid_xml(self, bootstrapped_root: Path):
        from backend.services.reindex_service import reindex_from_xml

        good = people_service.create_person(
            bootstrapped_root, "default", _create_payload(name="Good")
        )
        # Drop a corrupt person.xml in the active folder so reindex
        # walks past it.
        bad_uuid = uuid4()
        (bootstrapped_root / "people" / f"{bad_uuid}.xml").write_bytes(
            b"<not-valid-xml>"
        )

        # Wipe so reindex repopulates from scratch.
        result = open_index(bootstrapped_root)
        try:
            result.conn.execute("DELETE FROM people")
            result.conn.commit()
        finally:
            result.conn.close()

        report = reindex_from_xml(bootstrapped_root)
        # 2 entries scanned (good + bad), only 1 indexed.
        assert report.people_scanned == 2
        assert report.people_indexed == 1
        assert len(report.people_skipped) == 1
        # Index has the good record; bad is absent.
        result = open_index(bootstrapped_root)
        try:
            ids = {
                row["id"]
                for row in result.conn.execute("SELECT id FROM people")
            }
        finally:
            result.conn.close()
        assert str(good.id) in ids
        assert str(bad_uuid) not in ids
        # And ``clean`` is False because of the skip.
        assert not report.clean


# --------------------------------------------------------------------------- #
# Bootstrap creates the people/ directory
# --------------------------------------------------------------------------- #

class TestBootstrap:
    def test_people_dir_exists_after_bootstrap(self, bootstrapped_root: Path):
        assert (bootstrapped_root / "people").is_dir()
