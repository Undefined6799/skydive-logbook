"""Service-layer tests for jumper_service (R.2.0b + R.2.0c.i, D33).

R.2.0b covered create + get; R.2.0c.i extends with list + update +
delete + the auto-bump rule for ``exit_weight_updated_at``. Real
``tmp_path``-backed logbook root per CLAUDE.md §7.
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from backend.api.errors import NotFoundError, ValidationFailedError
from backend.models.jumper import JumperCreate, JumperUpdate
from backend.services import jumper_service
from backend.storage.bootstrap import bootstrap_logbook


@pytest.fixture
def bootstrapped_root(logbook_root: Path) -> Path:
    """A logbook root with bootstrap applied — XSDs + every subdir
    including ``jumpers/``."""
    bootstrap_logbook(logbook_root)
    return logbook_root


def _create_payload(**overrides) -> JumperCreate:
    """Convenience builder. Defaults to a defensible v0.1 jumper."""
    base: dict = {
        "name": "Alex Tester",
        "exit_weight_lb": 200.0,
    }
    base.update(overrides)
    return JumperCreate(**base)


def _update_payload(**overrides) -> JumperUpdate:
    """Convenience builder for JumperUpdate. Defaults match the
    ``_create_payload`` shape so an "edit nothing" PUT round-trips
    cleanly. Tests that exercise the auto-bump rule override
    ``exit_weight_lb`` (and optionally ``exit_weight_updated_at``)."""
    base: dict = {
        "name": "Alex Tester",
        "exit_weight_lb": 200.0,
    }
    base.update(overrides)
    return JumperUpdate(**base)


# --------------------------------------------------------------------------- #
# create_jumper
# --------------------------------------------------------------------------- #


class TestCreate:
    def test_writes_file_at_uuid_path(self, bootstrapped_root: Path):
        # Folder shape per D47 / Phase C.1: every jumper lives under
        # ``jumpers/<uuid>/`` with ``jumper.xml`` + ``SHA256SUMS`` +
        # ``attachments/``.
        j = jumper_service.create_jumper(
            bootstrapped_root, "default", _create_payload()
        )
        folder = bootstrapped_root / "jumpers" / str(j.id)
        assert folder.is_dir(), f"expected jumper folder at {folder}"
        assert (folder / "jumper.xml").is_file()
        assert (folder / "SHA256SUMS").is_file()
        assert (folder / "attachments").is_dir()

    def test_assigns_server_uuid(self, bootstrapped_root: Path):
        j = jumper_service.create_jumper(
            bootstrapped_root, "default", _create_payload()
        )
        assert isinstance(j.id, UUID)

    def test_two_creates_get_distinct_ids(self, bootstrapped_root: Path):
        a = jumper_service.create_jumper(
            bootstrapped_root, "default", _create_payload()
        )
        b = jumper_service.create_jumper(
            bootstrapped_root, "default", _create_payload()
        )
        assert a.id != b.id

    def test_stamps_timestamps_together(self, bootstrapped_root: Path):
        j = jumper_service.create_jumper(
            bootstrapped_root, "default", _create_payload()
        )
        assert j.created_at is not None and j.updated_at is not None
        assert j.created_at == j.updated_at

    def test_auto_stamps_exit_weight_updated_at_to_today_utc(
        self, bootstrapped_root: Path
    ):
        # When the caller doesn't supply the date, the service sets it
        # to today's UTC date — the staleness clock starts now.
        j = jumper_service.create_jumper(
            bootstrapped_root, "default", _create_payload()
        )
        assert j.exit_weight_updated_at == datetime.now(UTC).date()

    def test_explicit_exit_weight_updated_at_preserved(
        self, bootstrapped_root: Path
    ):
        # Used-gear onboarding path: caller knows the original
        # confirmation date and overrides the auto-stamp.
        original = date(2024, 6, 1)
        j = jumper_service.create_jumper(
            bootstrapped_root,
            "default",
            _create_payload(exit_weight_updated_at=original),
        )
        assert j.exit_weight_updated_at == original

    def test_round_trip_full_record(self, bootstrapped_root: Path):
        original = date(2024, 6, 1)
        created = jumper_service.create_jumper(
            bootstrapped_root,
            "default",
            _create_payload(
                name="François Béland",
                exit_weight_lb=205.5,
                exit_weight_updated_at=original,
            ),
        )
        fetched = jumper_service.get_jumper(
            bootstrapped_root, "default", created.id
        )
        assert fetched == created
        assert fetched.name == "François Béland"
        assert fetched.exit_weight_lb == 205.5
        assert fetched.exit_weight_updated_at == original

    def test_optional_name_can_be_omitted(self, bootstrapped_root: Path):
        j = jumper_service.create_jumper(
            bootstrapped_root,
            "default",
            JumperCreate(exit_weight_lb=200.0),
        )
        assert j.name is None
        # Round-trip preserves the absence.
        fetched = jumper_service.get_jumper(
            bootstrapped_root, "default", j.id
        )
        assert fetched.name is None


# --------------------------------------------------------------------------- #
# get_jumper
# --------------------------------------------------------------------------- #


class TestGet:
    def test_unknown_id_raises_not_found(self, bootstrapped_root: Path):
        with pytest.raises(NotFoundError):
            jumper_service.get_jumper(bootstrapped_root, "default", uuid4())

    def test_invalid_xml_raises_validation_failed(
        self, bootstrapped_root: Path
    ):
        # C.1 shape: corrupting jumper.xml inside the folder must
        # surface as ValidationFailedError on get_jumper.
        created = jumper_service.create_jumper(
            bootstrapped_root, "default", _create_payload()
        )
        path = bootstrapped_root / "jumpers" / str(created.id) / "jumper.xml"
        path.write_bytes(b"<jumper>not valid</jumper>")
        with pytest.raises(ValidationFailedError):
            jumper_service.get_jumper(
                bootstrapped_root, "default", created.id
            )


class TestPersistenceInvariants:
    def test_xml_validates_against_xsd_on_disk(
        self, bootstrapped_root: Path
    ):
        # Smoke-test D2: every write XSD-validates. Read it back
        # through the hardened parser + validator.
        from backend.xml.validator import parse, validate

        j = jumper_service.create_jumper(
            bootstrapped_root, "default", _create_payload()
        )
        path = bootstrapped_root / "jumpers" / str(j.id) / "jumper.xml"
        element = parse(path.read_bytes())
        validate(element)


# --------------------------------------------------------------------------- #
# list_jumpers (R.2.0c.i)
# --------------------------------------------------------------------------- #


class TestList:
    def test_empty_list_when_no_jumpers(self, bootstrapped_root: Path):
        result = jumper_service.list_jumpers(bootstrapped_root, "default")
        assert result == []

    def test_no_folder_returns_empty(self, logbook_root: Path):
        # Pre-bootstrap: jumpers/ doesn't exist. List must tolerate.
        result = jumper_service.list_jumpers(logbook_root, "default")
        assert result == []

    def test_lists_every_jumper(self, bootstrapped_root: Path):
        a = jumper_service.create_jumper(
            bootstrapped_root, "default", _create_payload(name="A")
        )
        # filesystem list ordering uses created_at — sleep so the
        # second jumper has strictly-greater ms-precision timestamp.
        time.sleep(0.005)
        b = jumper_service.create_jumper(
            bootstrapped_root, "default", _create_payload(name="B")
        )
        result = jumper_service.list_jumpers(bootstrapped_root, "default")
        assert {j.id for j in result} == {a.id, b.id}

    def test_orders_newest_first_by_created_at(
        self, bootstrapped_root: Path
    ):
        a = jumper_service.create_jumper(
            bootstrapped_root, "default", _create_payload(name="first")
        )
        time.sleep(0.005)
        b = jumper_service.create_jumper(
            bootstrapped_root, "default", _create_payload(name="second")
        )
        result = jumper_service.list_jumpers(bootstrapped_root, "default")
        assert [j.id for j in result] == [b.id, a.id]

    def test_limit_and_offset_apply(self, bootstrapped_root: Path):
        ids = []
        for i in range(3):
            j = jumper_service.create_jumper(
                bootstrapped_root,
                "default",
                _create_payload(name=f"j{i}"),
            )
            ids.append(j.id)
            time.sleep(0.005)
        # newest-first means ids reversed
        expected_order = list(reversed(ids))
        page = jumper_service.list_jumpers(
            bootstrapped_root, "default", limit=1, offset=1
        )
        assert [j.id for j in page] == [expected_order[1]]

    def test_skips_invalid_xml_with_warning(
        self, bootstrapped_root: Path, caplog
    ):
        # C.1 shape: one valid jumper folder + one folder whose
        # jumper.xml is corrupt. ``list_jumpers`` returns only the
        # valid one and logs a WARNING for the bad folder.
        good = jumper_service.create_jumper(
            bootstrapped_root, "default", _create_payload()
        )
        bad_folder = (
            bootstrapped_root / "jumpers" / "deadbeef-dead-4bee-8eef-deadbeefdead"
        )
        bad_folder.mkdir()
        (bad_folder / "jumper.xml").write_bytes(b"<jumper>not valid</jumper>")
        caplog.set_level(logging.WARNING, logger="backend.services.jumper")
        result = jumper_service.list_jumpers(bootstrapped_root, "default")
        assert [j.id for j in result] == [good.id]
        assert any(
            r.message == "jumper_skip_invalid" for r in caplog.records
        )


# --------------------------------------------------------------------------- #
# update_jumper (R.2.0c.i) — full replace + the auto-bump rule
# --------------------------------------------------------------------------- #


class TestUpdate:
    def test_full_replace_changes_fields(self, bootstrapped_root: Path):
        created = jumper_service.create_jumper(
            bootstrapped_root, "default", _create_payload()
        )
        updated = jumper_service.update_jumper(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload(name="Renamed", exit_weight_lb=200.0),
        )
        assert updated.name == "Renamed"
        # Round-trip from disk confirms the write took.
        fetched = jumper_service.get_jumper(
            bootstrapped_root, "default", created.id
        )
        assert fetched.name == "Renamed"

    def test_preserves_id_and_created_at(self, bootstrapped_root: Path):
        created = jumper_service.create_jumper(
            bootstrapped_root, "default", _create_payload()
        )
        updated = jumper_service.update_jumper(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload(name="Changed"),
        )
        assert updated.id == created.id
        assert updated.created_at == created.created_at

    def test_bumps_updated_at(self, bootstrapped_root: Path):
        created = jumper_service.create_jumper(
            bootstrapped_root, "default", _create_payload()
        )
        time.sleep(0.005)
        updated = jumper_service.update_jumper(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload(),
        )
        assert updated.updated_at != created.updated_at
        # updated_at should be strictly later than created_at on every
        # update — the lexical ordering of the ms ISO strings matches
        # chronological ordering for any single timezone (UTC here).
        assert updated.updated_at > created.created_at

    def test_unknown_id_raises_not_found(self, bootstrapped_root: Path):
        with pytest.raises(NotFoundError):
            jumper_service.update_jumper(
                bootstrapped_root, "default", uuid4(), _update_payload()
            )


class TestUpdateAutoBump:
    """D33 staleness clock: ``exit_weight_updated_at`` resets when
    the weight changes and is preserved when it doesn't.
    """

    def test_weight_change_no_explicit_date_bumps_to_today(
        self, bootstrapped_root: Path
    ):
        # Set the original date to a year ago so we can see the bump.
        original_date = date(2025, 5, 1)
        created = jumper_service.create_jumper(
            bootstrapped_root,
            "default",
            _create_payload(exit_weight_updated_at=original_date),
        )
        # Weight change, no explicit date supplied → auto-bump.
        updated = jumper_service.update_jumper(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload(exit_weight_lb=210.0),
        )
        assert updated.exit_weight_updated_at == datetime.now(UTC).date()
        assert updated.exit_weight_updated_at != original_date

    def test_weight_change_with_explicit_date_uses_explicit(
        self, bootstrapped_root: Path
    ):
        # Explicit date on the update — used-gear correction path.
        # The auto-bump must not override what the caller asked for.
        created = jumper_service.create_jumper(
            bootstrapped_root,
            "default",
            _create_payload(exit_weight_updated_at=date(2025, 1, 1)),
        )
        explicit = date(2025, 6, 15)
        updated = jumper_service.update_jumper(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload(
                exit_weight_lb=210.0,
                exit_weight_updated_at=explicit,
            ),
        )
        assert updated.exit_weight_updated_at == explicit

    def test_metadata_only_edit_preserves_stamp(
        self, bootstrapped_root: Path
    ):
        # Weight unchanged, no explicit date supplied → on-disk
        # stamp is preserved. A name edit must not silently reset
        # the staleness clock.
        original_date = date(2025, 5, 1)
        created = jumper_service.create_jumper(
            bootstrapped_root,
            "default",
            _create_payload(
                name="Original",
                exit_weight_lb=200.0,
                exit_weight_updated_at=original_date,
            ),
        )
        updated = jumper_service.update_jumper(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload(
                name="Renamed",
                exit_weight_lb=200.0,
            ),
        )
        assert updated.name == "Renamed"
        assert updated.exit_weight_updated_at == original_date

    def test_explicit_date_with_unchanged_weight_still_wins(
        self, bootstrapped_root: Path
    ):
        # Caller may want to update the date without changing the
        # weight (e.g. "I confirmed my weight today; it's the same").
        # Explicit date wins.
        created = jumper_service.create_jumper(
            bootstrapped_root,
            "default",
            _create_payload(exit_weight_updated_at=date(2025, 1, 1)),
        )
        explicit = date(2026, 4, 28)
        updated = jumper_service.update_jumper(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload(
                exit_weight_lb=200.0,
                exit_weight_updated_at=explicit,
            ),
        )
        assert updated.exit_weight_updated_at == explicit


# --------------------------------------------------------------------------- #
# delete_jumper (R.2.0c.i)
# --------------------------------------------------------------------------- #


class TestDelete:
    def test_returns_trash_path(self, bootstrapped_root: Path):
        # C.1 shape: delete soft-deletes the whole jumper folder
        # (jumper.xml + SHA256SUMS + attachments/) into
        # .trash/jumpers/<ts>_<uuid>/.
        created = jumper_service.create_jumper(
            bootstrapped_root, "default", _create_payload()
        )
        trashed = jumper_service.delete_jumper(
            bootstrapped_root, "default", created.id
        )
        assert trashed.is_dir()
        assert ".trash" in trashed.parts
        assert "jumpers" in trashed.parts
        # Trash folder name is "<timestamp>_<uuid>".
        assert trashed.name.endswith(str(created.id))
        # The original folder contents (jumper.xml + manifest +
        # attachments/) round-trip into the trashed folder.
        assert (trashed / "jumper.xml").is_file()
        assert (trashed / "SHA256SUMS").is_file()
        assert (trashed / "attachments").is_dir()

    def test_subsequent_get_raises_not_found(self, bootstrapped_root: Path):
        created = jumper_service.create_jumper(
            bootstrapped_root, "default", _create_payload()
        )
        jumper_service.delete_jumper(
            bootstrapped_root, "default", created.id
        )
        with pytest.raises(NotFoundError):
            jumper_service.get_jumper(
                bootstrapped_root, "default", created.id
            )

    def test_subsequent_list_omits(self, bootstrapped_root: Path):
        a = jumper_service.create_jumper(
            bootstrapped_root, "default", _create_payload(name="A")
        )
        b = jumper_service.create_jumper(
            bootstrapped_root, "default", _create_payload(name="B")
        )
        jumper_service.delete_jumper(bootstrapped_root, "default", a.id)
        result = jumper_service.list_jumpers(bootstrapped_root, "default")
        assert [j.id for j in result] == [b.id]

    def test_unknown_id_raises_not_found(self, bootstrapped_root: Path):
        with pytest.raises(NotFoundError):
            jumper_service.delete_jumper(
                bootstrapped_root, "default", uuid4()
            )


# --------------------------------------------------------------------------- #
# Structured logging — guard against extra={"name": ...} collisions
# --------------------------------------------------------------------------- #


class TestStructuredLoggingExtraKeys:
    """Same regression class as task #45 / dropzone hotfix.

    Special hazard for jumper_service: the model has a ``name`` field
    that maps directly to a reserved ``LogRecord`` attribute. The
    service deliberately does NOT log the name (it's user-provided
    free text without observability value) — but if a future drift
    adds ``extra={"name": j.name}`` this regression test fires.
    Tests run under INFO so ``makeRecord`` actually validates the dict.
    """

    def test_create_jumper_log_has_no_collision(
        self, bootstrapped_root: Path, caplog
    ):
        caplog.set_level(logging.INFO, logger="backend.services.jumper")
        jumper_service.create_jumper(
            bootstrapped_root, "default", _create_payload()
        )
        assert any(r.message == "jumper_created" for r in caplog.records)

    def test_create_jumper_with_unicode_name_log_clean(
        self, bootstrapped_root: Path, caplog
    ):
        # Belt-and-braces: a unicode display name is a likely place
        # to accidentally route through extra={"name": ...}; make
        # sure that path stays clean even with that input.
        caplog.set_level(logging.INFO, logger="backend.services.jumper")
        jumper_service.create_jumper(
            bootstrapped_root,
            "default",
            _create_payload(name="François Béland"),
        )
        assert any(r.message == "jumper_created" for r in caplog.records)

    def test_update_jumper_log_has_no_collision(
        self, bootstrapped_root: Path, caplog
    ):
        created = jumper_service.create_jumper(
            bootstrapped_root, "default", _create_payload()
        )
        caplog.set_level(logging.INFO, logger="backend.services.jumper")
        jumper_service.update_jumper(
            bootstrapped_root, "default", created.id, _update_payload()
        )
        assert any(r.message == "jumper_updated" for r in caplog.records)

    def test_delete_jumper_log_has_no_collision(
        self, bootstrapped_root: Path, caplog
    ):
        created = jumper_service.create_jumper(
            bootstrapped_root, "default", _create_payload()
        )
        caplog.set_level(logging.INFO, logger="backend.services.jumper")
        jumper_service.delete_jumper(
            bootstrapped_root, "default", created.id
        )
        assert any(r.message == "jumper_deleted" for r in caplog.records)
