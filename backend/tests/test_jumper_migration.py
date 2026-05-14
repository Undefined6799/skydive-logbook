"""Phase C.1 — Migration: jumpers/<uuid>.xml → jumpers/<uuid>/jumper.xml.

This test surface covers the migration's three load-bearing
properties:

  * **Idempotent.** Running migrate_all_jumpers on an already-migrated
    logbook is a no-op.
  * **Crash-resistant.** A migration interrupted partway through can
    be re-run and completes the move without data loss.
  * **Conservative.** A corrupt legacy file is skipped with a WARNING
    rather than promoted into the new layout (corruption stays put;
    ``verify`` flags it).

Plus the happy-path end-to-end through ``bootstrap_logbook``: a fresh
logbook created at the pre-D47 layout and bootstrapped under the
post-D47 code lands in folder shape automatically.
"""
from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

import pytest

from backend.models.jumper import Jumper, JumperCreate
from backend.services import jumper_service
from backend.storage.bootstrap import bootstrap_logbook
from backend.storage.jumper_migration import (
    ATTACHMENTS_DIRNAME,
    JUMPER_XML_NAME,
    JUMPERS_DIRNAME,
    migrate_all_jumpers,
    migrate_one_jumper,
)
from backend.xml.serialize import jumper_to_bytes


def _bootstrapped_root(logbook_root: Path) -> Path:
    """Bootstrap a logbook root and return it for tests that don't
    use the existing bootstrapped_root fixture (this module's
    fixture lives in conftest)."""
    bootstrap_logbook(logbook_root)
    return logbook_root


def _make_legacy_jumper(root: Path, jumper_id: UUID, weight: float = 180.0) -> bytes:
    """Write a valid pre-D47 flat jumper file at ``jumpers/<uuid>.xml``.

    Returns the bytes that landed on disk so callers can assert
    byte-for-byte preservation across the migration.
    """
    j = Jumper(id=jumper_id, exit_weight_lb=weight)
    legacy_bytes = jumper_to_bytes(j)
    legacy_path = root / JUMPERS_DIRNAME / f"{jumper_id}.xml"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_bytes(legacy_bytes)
    return legacy_bytes


# --------------------------------------------------------------------- #
# migrate_all_jumpers — idempotency + happy paths
# --------------------------------------------------------------------- #

class TestMigrateAllJumpers:
    def test_empty_logbook_returns_zero(self, logbook_root: Path) -> None:
        # Pre-bootstrap: jumpers/ doesn't exist yet. Migration is a
        # no-op rather than an error.
        assert migrate_all_jumpers(logbook_root) == 0

    def test_already_migrated_returns_zero(self, logbook_root: Path) -> None:
        bootstrap_logbook(logbook_root)
        # Use the service to create a jumper at the new shape — that
        # leaves no legacy files behind, so migration has nothing to do.
        jumper_service.create_jumper(
            logbook_root, "default", JumperCreate(exit_weight_lb=180)
        )
        assert migrate_all_jumpers(logbook_root) == 0

    def test_single_legacy_file_migrates(self, logbook_root: Path) -> None:
        bootstrap_logbook(logbook_root)
        jid = UUID("11111111-1111-4111-8111-111111111111")
        legacy_bytes = _make_legacy_jumper(logbook_root, jid)

        changes = migrate_all_jumpers(logbook_root)
        assert changes == 1

        folder = logbook_root / JUMPERS_DIRNAME / str(jid)
        # Folder + jumper.xml + manifest + attachments/ all present.
        assert folder.is_dir()
        assert (folder / JUMPER_XML_NAME).is_file()
        assert (folder / "SHA256SUMS").is_file()
        assert (folder / ATTACHMENTS_DIRNAME).is_dir()
        # Bytes are preserved verbatim.
        assert (folder / JUMPER_XML_NAME).read_bytes() == legacy_bytes
        # Legacy file is gone.
        assert not (logbook_root / JUMPERS_DIRNAME / f"{jid}.xml").exists()

    def test_multiple_legacy_files_all_migrate(
        self, logbook_root: Path
    ) -> None:
        bootstrap_logbook(logbook_root)
        ids = [
            UUID("11111111-1111-4111-8111-111111111111"),
            UUID("22222222-2222-4222-8222-222222222222"),
            UUID("33333333-3333-4333-8333-333333333333"),
        ]
        for jid in ids:
            _make_legacy_jumper(logbook_root, jid)

        assert migrate_all_jumpers(logbook_root) == 3

        for jid in ids:
            folder = logbook_root / JUMPERS_DIRNAME / str(jid)
            assert (folder / JUMPER_XML_NAME).is_file()
            assert not (logbook_root / JUMPERS_DIRNAME / f"{jid}.xml").exists()

    def test_idempotent_repeat_run(self, logbook_root: Path) -> None:
        # First run migrates; second run sees the folder shape and
        # returns 0 without modifying disk.
        bootstrap_logbook(logbook_root)
        jid = UUID("11111111-1111-4111-8111-111111111111")
        _make_legacy_jumper(logbook_root, jid)

        first = migrate_all_jumpers(logbook_root)
        second = migrate_all_jumpers(logbook_root)
        assert first == 1
        assert second == 0

    def test_mixed_legacy_and_migrated_only_migrates_legacy(
        self, logbook_root: Path
    ) -> None:
        bootstrap_logbook(logbook_root)
        # One already in folder shape (via the service)
        already = jumper_service.create_jumper(
            logbook_root, "default", JumperCreate(exit_weight_lb=200)
        )
        # One legacy file
        legacy_id = UUID("11111111-1111-4111-8111-111111111111")
        _make_legacy_jumper(logbook_root, legacy_id)

        assert migrate_all_jumpers(logbook_root) == 1

        # Both readable through the service
        listed = jumper_service.list_jumpers(logbook_root, "default")
        assert {j.id for j in listed} == {already.id, legacy_id}


# --------------------------------------------------------------------- #
# Crash-harness — partial migration states heal on retry
# --------------------------------------------------------------------- #

class TestCrashHarness:
    """Per CLAUDE.md §7, multi-file writes need crash-path tests. The
    migration is a four-step write (mkdir, write jumper.xml, write
    manifest, unlink legacy); each intermediate state must be safely
    completable by a re-run."""

    def test_legacy_survives_when_folder_already_complete(
        self, logbook_root: Path
    ) -> None:
        # Crash state: previous run wrote jumper.xml + manifest but
        # crashed before unlinking the legacy file. Re-run cleans up.
        bootstrap_logbook(logbook_root)
        jid = UUID("11111111-1111-4111-8111-111111111111")
        legacy_bytes = _make_legacy_jumper(logbook_root, jid)
        # Simulate a successful prior migration by creating the folder
        # shape directly, then leave the legacy file alongside.
        folder = logbook_root / JUMPERS_DIRNAME / str(jid)
        folder.mkdir()
        (folder / ATTACHMENTS_DIRNAME).mkdir()
        (folder / JUMPER_XML_NAME).write_bytes(legacy_bytes)
        # No manifest yet — will be regenerated when the service next
        # writes. For migration's purposes the trigger is "folder_xml
        # exists" so this is enough.
        legacy_path = logbook_root / JUMPERS_DIRNAME / f"{jid}.xml"
        assert legacy_path.exists(), "test setup: legacy must still exist"

        changes = migrate_all_jumpers(logbook_root)

        assert changes == 1  # cleanup of the stale legacy counts as a change
        assert not legacy_path.exists()
        # Folder content is preserved.
        assert (folder / JUMPER_XML_NAME).read_bytes() == legacy_bytes

    def test_partial_folder_with_legacy_completes_migration(
        self, logbook_root: Path
    ) -> None:
        # Crash state: previous run created the folder but crashed
        # before writing jumper.xml. Both folder and legacy exist. The
        # re-run must complete the move.
        bootstrap_logbook(logbook_root)
        jid = UUID("11111111-1111-4111-8111-111111111111")
        legacy_bytes = _make_legacy_jumper(logbook_root, jid)
        # Pre-create an empty folder to simulate the partial state.
        (logbook_root / JUMPERS_DIRNAME / str(jid)).mkdir()

        changes = migrate_all_jumpers(logbook_root)

        assert changes == 1
        folder = logbook_root / JUMPERS_DIRNAME / str(jid)
        assert (folder / JUMPER_XML_NAME).read_bytes() == legacy_bytes
        assert (folder / "SHA256SUMS").is_file()
        assert (folder / ATTACHMENTS_DIRNAME).is_dir()
        assert not (logbook_root / JUMPERS_DIRNAME / f"{jid}.xml").exists()

    def test_orphan_folder_no_legacy_no_op(
        self, logbook_root: Path
    ) -> None:
        # Crash state we can't recover from: folder exists but is
        # incomplete (no jumper.xml inside) AND legacy file is also
        # gone. migrate_one_jumper returns False — verify will flag
        # the empty folder later.
        bootstrap_logbook(logbook_root)
        jid = UUID("11111111-1111-4111-8111-111111111111")
        (logbook_root / JUMPERS_DIRNAME / str(jid)).mkdir()
        # No legacy file. No jumper.xml inside.

        changes = migrate_all_jumpers(logbook_root)

        # The migrate_all helper iterates legacy files only, so this
        # zero-legacy state produces zero changes.
        assert changes == 0

    def test_migrate_one_handles_orphan_folder_directly(
        self, logbook_root: Path
    ) -> None:
        # Direct call to migrate_one_jumper for the orphan-folder
        # state. Returns False, doesn't raise.
        bootstrap_logbook(logbook_root)
        jid = UUID("11111111-1111-4111-8111-111111111111")
        (logbook_root / JUMPERS_DIRNAME / str(jid)).mkdir()
        legacy_path = logbook_root / JUMPERS_DIRNAME / f"{jid}.xml"

        result = migrate_one_jumper(legacy_path)
        assert result is False


# --------------------------------------------------------------------- #
# Conservative path — corrupt legacy stays put
# --------------------------------------------------------------------- #

class TestCorruptLegacy:
    def test_corrupt_legacy_skipped_with_warning(
        self, logbook_root: Path, caplog
    ) -> None:
        bootstrap_logbook(logbook_root)
        # Hand-write garbage at the legacy path. The XSD-validation
        # step inside the migration must reject it; the file stays
        # in place and the migration logs a WARNING rather than
        # corrupting the new layout.
        bad_path = (
            logbook_root
            / JUMPERS_DIRNAME
            / "deadbeef-dead-4eef-8eef-deadbeefdead.xml"
        )
        bad_path.write_bytes(b"<jumper>not valid</jumper>")

        caplog.set_level(
            logging.WARNING, logger="backend.storage.jumper_migration"
        )
        changes = migrate_all_jumpers(logbook_root)

        assert changes == 0
        assert bad_path.exists(), "corrupt legacy must stay in place"
        assert any(
            r.message == "jumper_migration_skip_invalid"
            for r in caplog.records
        ), "expected skip-invalid warning"

    def test_corrupt_legacy_does_not_block_other_jumpers(
        self, logbook_root: Path, caplog
    ) -> None:
        bootstrap_logbook(logbook_root)
        # One valid + one corrupt — the valid one must still migrate.
        good_id = UUID("11111111-1111-4111-8111-111111111111")
        _make_legacy_jumper(logbook_root, good_id)
        bad_path = (
            logbook_root
            / JUMPERS_DIRNAME
            / "deadbeef-dead-4eef-8eef-deadbeefdead.xml"
        )
        bad_path.write_bytes(b"<jumper>not valid</jumper>")

        caplog.set_level(
            logging.WARNING, logger="backend.storage.jumper_migration"
        )
        changes = migrate_all_jumpers(logbook_root)

        assert changes == 1
        assert (
            logbook_root
            / JUMPERS_DIRNAME
            / str(good_id)
            / JUMPER_XML_NAME
        ).is_file()
        assert bad_path.exists()


# --------------------------------------------------------------------- #
# bootstrap_logbook integration
# --------------------------------------------------------------------- #

class TestBootstrapIntegration:
    def test_bootstrap_migrates_legacy_jumpers(
        self, logbook_root: Path
    ) -> None:
        # Simulate a pre-D47 logbook: bootstrap creates the
        # jumpers/ subdir, then we drop a legacy flat file in. A
        # second bootstrap must migrate it (idempotent + the
        # migration happens at every bootstrap call).
        bootstrap_logbook(logbook_root)
        jid = UUID("11111111-1111-4111-8111-111111111111")
        legacy_bytes = _make_legacy_jumper(logbook_root, jid)
        legacy_path = logbook_root / JUMPERS_DIRNAME / f"{jid}.xml"
        assert legacy_path.exists()

        # Re-run bootstrap. The migration step inside should promote
        # the legacy file to the folder shape.
        bootstrap_logbook(logbook_root)

        assert not legacy_path.exists()
        folder = logbook_root / JUMPERS_DIRNAME / str(jid)
        assert (folder / JUMPER_XML_NAME).read_bytes() == legacy_bytes
        assert (folder / "SHA256SUMS").is_file()
        assert (folder / ATTACHMENTS_DIRNAME).is_dir()

    def test_post_migration_jumper_readable_by_service(
        self, logbook_root: Path
    ) -> None:
        # End-to-end: after migration the service's get_jumper
        # surfaces the migrated record without any additional setup.
        bootstrap_logbook(logbook_root)
        jid = UUID("11111111-1111-4111-8111-111111111111")
        _make_legacy_jumper(logbook_root, jid, weight=205.5)

        bootstrap_logbook(logbook_root)  # migration runs

        fetched = jumper_service.get_jumper(logbook_root, "default", jid)
        assert fetched.id == jid
        assert fetched.exit_weight_lb == 205.5

    def test_migrated_jumper_listed_by_service(
        self, logbook_root: Path
    ) -> None:
        bootstrap_logbook(logbook_root)
        jid = UUID("11111111-1111-4111-8111-111111111111")
        _make_legacy_jumper(logbook_root, jid)

        bootstrap_logbook(logbook_root)  # migration runs

        listed = jumper_service.list_jumpers(logbook_root, "default")
        assert [j.id for j in listed] == [jid]


# --------------------------------------------------------------------- #
# Manifest invariant — generated SHA256SUMS matches on-disk content
# --------------------------------------------------------------------- #

def test_post_migration_manifest_verifies(logbook_root: Path) -> None:
    """The manifest written by migration must match the actual bytes
    on disk. ``manifest.verify`` returns an empty list when every
    file's hash matches the manifest claim."""
    from backend.storage import manifest as _manifest

    bootstrap_logbook(logbook_root)
    jid = UUID("11111111-1111-4111-8111-111111111111")
    _make_legacy_jumper(logbook_root, jid)
    bootstrap_logbook(logbook_root)  # migrate

    folder = logbook_root / JUMPERS_DIRNAME / str(jid)
    problems = _manifest.verify(folder)
    assert problems == [], f"manifest should verify cleanly, got: {problems}"


@pytest.fixture
def logbook_root(tmp_path: Path) -> Path:
    """Local fixture used by the tests in this module that do their
    own bootstrap timing. Mirrors the conftest fixture's contract
    (returns a fresh path inside tmp_path) but is named so the
    bootstrapped_root fixture from conftest stays out of the way."""
    return tmp_path / "logbook"
