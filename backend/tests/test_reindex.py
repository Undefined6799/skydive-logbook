"""Tests for the Phase 3.6 reindex service (D3, D25, D32).

Reindex is the "rebuild the index from XML" operation that makes
D3's "SQLite is rebuildable" property concrete. The tests below
cover:

  * Idempotence: running reindex on a healthy logbook doesn't
    change the row values.
  * Resurrection: wiping ``jumps`` rows and running reindex
    restores the row set exactly.
  * D25 skip behaviour: folders in the "invalid" / "incomplete"
    crash states from D25's table are skipped (not errored on) —
    reindex is a recovery operation, it should not crash halfway.
  * D25 duplicate abort: two folders claiming the same
    ``(user_id, jump_number)`` abort the whole reindex with a
    clear report.
  * D32 timestamps: XML ``<created_at>`` / ``<updated_at>`` are
    used when present; missing values fall back to the jump.xml
    file mtime with a WARNING log.
  * D19 trash isolation: ``.trash/`` folders are NOT reindexed —
    soft-deleted jumps stay gone until the user manually restores.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date
from pathlib import Path

import pytest

from backend.models.jump import JumpCreate
from backend.services.jump_service import create_jump, delete_jump
from backend.services.reindex_service import reindex_from_xml
from backend.storage.bootstrap import bootstrap_logbook
from backend.storage.index import open_index
from backend.storage.manifest import JUMP_XML_NAME


@pytest.fixture
def bootstrapped_root(tmp_path: Path) -> Path:
    root = tmp_path / "logbook"
    bootstrap_logbook(root)
    result = open_index(root)
    result.conn.close()
    return root


def _minimal_create(**overrides) -> JumpCreate:
    data = dict(
        jump_number=1,
        date=date(2026, 4, 22),
        dropzone="Skydive Elsinore",
        exit_altitude_m=4000,
        deployment_altitude_m=900,
    )
    data.update(overrides)
    return JumpCreate(**data)


def _fetch_index_rows(logbook_root: Path) -> list[dict]:
    result = open_index(logbook_root)
    try:
        rows = result.conn.execute(
            "SELECT id, jump_number, date, dropzone, title, folder, "
            "schema_ns, created_at, updated_at FROM jumps "
            "ORDER BY jump_number"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        result.conn.close()


# --------------------------------------------------------------------------- #
# Idempotence + happy path
# --------------------------------------------------------------------------- #

class TestIdempotence:
    def test_noop_on_clean_logbook(self, bootstrapped_root: Path):
        create_jump(
            bootstrapped_root, "default", _minimal_create(jump_number=1, title="A")
        )
        create_jump(
            bootstrapped_root, "default", _minimal_create(jump_number=2, title="B")
        )
        before = _fetch_index_rows(bootstrapped_root)
        report = reindex_from_xml(bootstrapped_root)
        after = _fetch_index_rows(bootstrapped_root)
        # Re-running reindex on an already-current index leaves
        # every row identical.
        assert before == after
        assert report.jumps_indexed == 2
        assert report.clean

    def test_rebuild_after_wiping_rows(self, bootstrapped_root: Path):
        # D3 in action: wipe the jumps table, reindex, confirm rows
        # reappear from XML alone.
        created = [
            create_jump(
                bootstrapped_root,
                "default",
                _minimal_create(jump_number=n, title=f"T{n}"),
            )
            for n in (1, 2, 3)
        ]
        ids_before = sorted(str(j.id) for j in created)

        result = open_index(bootstrapped_root)
        try:
            result.conn.execute("DELETE FROM jumps")
        finally:
            result.conn.close()
        assert _fetch_index_rows(bootstrapped_root) == []

        report = reindex_from_xml(bootstrapped_root)
        assert report.jumps_indexed == 3

        ids_after = sorted(r["id"] for r in _fetch_index_rows(bootstrapped_root))
        # Same ids — id is stamped in XML (D4), so reindex restores it.
        assert ids_after == ids_before

    def test_preserves_title_and_folder(self, bootstrapped_root: Path):
        # title and folder are denormalizations in the index; they
        # must both rebuild correctly because list views depend on
        # them.
        create_jump(
            bootstrapped_root,
            "default",
            _minimal_create(jump_number=7, title="Seven"),
        )
        result = open_index(bootstrapped_root)
        try:
            result.conn.execute("DELETE FROM jumps")
        finally:
            result.conn.close()

        reindex_from_xml(bootstrapped_root)
        rows = _fetch_index_rows(bootstrapped_root)
        assert rows[0]["title"] == "Seven"
        assert rows[0]["folder"] == "jumps/[7] Seven"

    def test_preserves_timestamps_from_xml(
        self, bootstrapped_root: Path
    ):
        # D32: reindex uses created_at/updated_at from the XML, not
        # mtime. A regression that flipped the fallback default
        # would overwrite authentic audit timestamps with file
        # mtime — this test catches that.
        created = create_jump(
            bootstrapped_root, "default", _minimal_create(jump_number=1)
        )
        result = open_index(bootstrapped_root)
        try:
            before_row = result.conn.execute(
                "SELECT created_at, updated_at FROM jumps WHERE id = ?",
                (str(created.id),),
            ).fetchone()
        finally:
            result.conn.close()

        # Wipe index, reindex, compare timestamps.
        result = open_index(bootstrapped_root)
        try:
            result.conn.execute("DELETE FROM jumps")
        finally:
            result.conn.close()

        reindex_from_xml(bootstrapped_root)

        result = open_index(bootstrapped_root)
        try:
            after_row = result.conn.execute(
                "SELECT created_at, updated_at FROM jumps WHERE id = ?",
                (str(created.id),),
            ).fetchone()
        finally:
            result.conn.close()
        assert before_row["created_at"] == after_row["created_at"]
        assert before_row["updated_at"] == after_row["updated_at"]


# --------------------------------------------------------------------------- #
# Skip behaviour on D25 crash-states
# --------------------------------------------------------------------------- #

class TestSkipsInvalidFolders:
    def test_skips_folder_without_jump_xml(self, bootstrapped_root: Path):
        # D25 crash state: folder exists but jump.xml is missing
        # (crashed after mkdir, before anything was written).
        # Reindex should note but not fail.
        create_jump(bootstrapped_root, "default", _minimal_create())
        (bootstrapped_root / "jumps" / "[99] incomplete").mkdir()

        report = reindex_from_xml(bootstrapped_root)
        assert report.jumps_indexed == 1
        assert len(report.skipped) == 1
        assert report.skipped[0][0] == "jumps/[99] incomplete"
        assert "missing" in report.skipped[0][1]
        # report.clean is False because we skipped something.
        assert not report.clean

    def test_skips_folder_with_garbage_jump_xml(
        self, bootstrapped_root: Path
    ):
        create_jump(bootstrapped_root, "default", _minimal_create())
        bad = bootstrapped_root / "jumps" / "[99] garbled"
        bad.mkdir()
        (bad / "jump.xml").write_bytes(b"not xml at all")

        report = reindex_from_xml(bootstrapped_root)
        assert report.jumps_indexed == 1
        assert len(report.skipped) == 1
        assert "invalid" in report.skipped[0][1].lower()

    def test_skip_does_not_abort_run(self, bootstrapped_root: Path):
        # A skipped folder in the middle must not interrupt the
        # reindex of the others — all valid folders must still
        # appear in the index.
        for n in (1, 3, 5):
            create_jump(
                bootstrapped_root,
                "default",
                _minimal_create(jump_number=n, title=f"T{n}"),
            )
        (bootstrapped_root / "jumps" / "[2] skip-me").mkdir()
        (bootstrapped_root / "jumps" / "[2] skip-me" / "jump.xml").write_bytes(
            b"<bad/>"
        )

        report = reindex_from_xml(bootstrapped_root)
        assert report.jumps_indexed == 3
        assert len(report.skipped) == 1
        rows = _fetch_index_rows(bootstrapped_root)
        assert [r["jump_number"] for r in rows] == [1, 3, 5]


# --------------------------------------------------------------------------- #
# D25 duplicate-number abort
# --------------------------------------------------------------------------- #

class TestDuplicateAbort:
    def test_abort_on_duplicate_jump_number(
        self, bootstrapped_root: Path
    ):
        # Create two jumps, then manually rename one folder so both
        # XMLs claim jump_number=1 on disk (simulating a
        # post-backup-restore conflict). Reindex must refuse to
        # pick a winner.
        j1 = create_jump(
            bootstrapped_root, "default", _minimal_create(jump_number=1, title="A")
        )
        create_jump(
            bootstrapped_root, "default", _minimal_create(jump_number=2, title="B")
        )
        # Overwrite folder B's jump.xml with the same jump_number=1
        # as folder A by re-parsing and editing. Simpler: write a
        # hand-crafted XML claiming jump_number=1.
        folder_b = bootstrapped_root / "jumps" / "[2] B"
        # Read A's jump.xml, change id to avoid primary-key collision
        # but keep jump_number=1.
        a_xml = (bootstrapped_root / "jumps" / "[1] A" / JUMP_XML_NAME).read_bytes()
        # Change the id to a different UUIDv4.
        new_id = b"11111111-1111-4111-9111-111111111111"
        forged = a_xml.replace(str(j1.id).encode(), new_id)
        (folder_b / JUMP_XML_NAME).write_bytes(forged)
        # Regenerate manifest on folder_b so reconcile doesn't
        # complain about hashes — test is about jump_number, not
        # manifest integrity.
        from backend.storage.manifest import MANIFEST_NAME, from_jump_xml
        (folder_b / MANIFEST_NAME).write_bytes(
            from_jump_xml(folder_b, logbook_root=bootstrapped_root)
        )

        report = reindex_from_xml(bootstrapped_root)
        assert report.aborted is not None
        assert "duplicate jump_number 1" in report.aborted
        # Nothing indexed — all-or-nothing abort.
        assert report.jumps_indexed == 0

    def test_abort_leaves_existing_index_untouched(
        self, bootstrapped_root: Path
    ):
        # If reindex aborts, the existing index rows must not be
        # half-overwritten. We wrap in a transaction so either all
        # rows land or none do.
        j1 = create_jump(
            bootstrapped_root, "default", _minimal_create(jump_number=1, title="A")
        )
        create_jump(
            bootstrapped_root, "default", _minimal_create(jump_number=2, title="B")
        )
        before = _fetch_index_rows(bootstrapped_root)

        # Forge duplicate.
        folder_b = bootstrapped_root / "jumps" / "[2] B"
        a_xml = (bootstrapped_root / "jumps" / "[1] A" / JUMP_XML_NAME).read_bytes()
        forged = a_xml.replace(
            str(j1.id).encode(), b"11111111-1111-4111-9111-111111111111"
        )
        (folder_b / JUMP_XML_NAME).write_bytes(forged)
        from backend.storage.manifest import MANIFEST_NAME, from_jump_xml
        (folder_b / MANIFEST_NAME).write_bytes(
            from_jump_xml(folder_b, logbook_root=bootstrapped_root)
        )

        report = reindex_from_xml(bootstrapped_root)
        assert report.aborted is not None
        # Index state unchanged — the original rows are intact.
        after = _fetch_index_rows(bootstrapped_root)
        assert before == after


# --------------------------------------------------------------------------- #
# D32 timestamp fallback
# --------------------------------------------------------------------------- #

class TestTimestampFallback:
    def test_xml_without_timestamps_uses_mtime(
        self, bootstrapped_root: Path, caplog
    ):
        # Create a jump, then strip the timestamp elements from
        # jump.xml (simulating a third-party-authored file or a
        # pre-D32 jump). Reindex must fall back to mtime and log
        # the WARNING.
        caplog.set_level(logging.WARNING, logger="backend.services.reindex")
        create_jump(
            bootstrapped_root, "default", _minimal_create(jump_number=1)
        )
        xml_path = bootstrapped_root / "jumps" / "[1]" / JUMP_XML_NAME
        # Strip D32 elements via string replace (crude but
        # sufficient for the XSD-optional fields).
        raw = xml_path.read_text()
        stripped = "\n".join(
            line for line in raw.splitlines()
            if "<created_at>" not in line and "<updated_at>" not in line
        )
        xml_path.write_text(stripped)

        # Set a known mtime so the fallback is deterministic.
        target_mtime = time.mktime((2020, 1, 2, 3, 4, 5, 0, 0, -1))
        os.utime(xml_path, (target_mtime, target_mtime))

        # Wipe index and reindex.
        result = open_index(bootstrapped_root)
        try:
            result.conn.execute("DELETE FROM jumps")
        finally:
            result.conn.close()
        # Also fix the manifest since we changed jump.xml bytes —
        # reconcile handles that for us during reindex.
        report = reindex_from_xml(bootstrapped_root)

        assert report.jumps_indexed == 1
        assert len(report.timestamp_fallbacks) == 1
        assert "jumps/[1]" in report.timestamp_fallbacks
        # WARNING was logged.
        warnings = [
            r for r in caplog.records if r.message == "reindex_timestamp_fallback"
        ]
        assert len(warnings) == 1
        assert warnings[0].folder == "jumps/[1]"

        # Index timestamp matches the mtime we set, in D17 form.
        rows = _fetch_index_rows(bootstrapped_root)
        assert rows[0]["created_at"].startswith("2020-01-02T")

    def test_timestamp_fallback_does_not_fail_reindex(
        self, bootstrapped_root: Path
    ):
        # Fallback is recoverable; reindex should still succeed and
        # the resulting row should be queryable. This is the "we
        # can read a file crafted without our audit metadata"
        # invariant from D32.
        create_jump(bootstrapped_root, "default", _minimal_create())
        xml_path = bootstrapped_root / "jumps" / "[1]" / JUMP_XML_NAME
        raw = xml_path.read_text()
        stripped = "\n".join(
            line for line in raw.splitlines()
            if "<created_at>" not in line and "<updated_at>" not in line
        )
        xml_path.write_text(stripped)

        result = open_index(bootstrapped_root)
        try:
            result.conn.execute("DELETE FROM jumps")
        finally:
            result.conn.close()

        report = reindex_from_xml(bootstrapped_root)
        assert report.jumps_indexed == 1
        # A fallback is NOT an error — but the report isn't
        # ``clean`` either (any non-trivial observation surfaces
        # somewhere). timestamp_fallbacks alone does not flip
        # ``clean`` to False — only skipped/aborted do. That's the
        # contract from ReindexReport.clean.
        assert report.clean  # fallbacks are warnings, not issues


# --------------------------------------------------------------------------- #
# D19 trash isolation
# --------------------------------------------------------------------------- #

class TestTrashIsolation:
    def test_trashed_folders_are_not_reindexed(
        self, bootstrapped_root: Path
    ):
        c1 = create_jump(
            bootstrapped_root, "default", _minimal_create(jump_number=1, title="live")
        )
        c2 = create_jump(
            bootstrapped_root, "default", _minimal_create(jump_number=2, title="gone")
        )
        delete_jump(bootstrapped_root, "default", c2.id)

        # Wipe and reindex — only the active folder should come back.
        result = open_index(bootstrapped_root)
        try:
            result.conn.execute("DELETE FROM jumps")
        finally:
            result.conn.close()

        report = reindex_from_xml(bootstrapped_root)
        assert report.jumps_indexed == 1
        rows = _fetch_index_rows(bootstrapped_root)
        assert len(rows) == 1
        assert rows[0]["id"] == str(c1.id)

    def test_trashed_jump_number_does_not_collide_with_active(
        self, bootstrapped_root: Path
    ):
        # A trashed folder with jump_number=1 plus an active folder
        # with jump_number=1 is NOT a duplicate per D19 (trash is
        # outside the uniqueness namespace). Reindex must not
        # abort.
        c1 = create_jump(
            bootstrapped_root, "default", _minimal_create(jump_number=1, title="first")
        )
        delete_jump(bootstrapped_root, "default", c1.id)
        create_jump(
            bootstrapped_root, "default", _minimal_create(jump_number=1, title="second")
        )

        report = reindex_from_xml(bootstrapped_root)
        assert report.aborted is None
        assert report.jumps_indexed == 1


# --------------------------------------------------------------------------- #
# Missing jumps directory
# --------------------------------------------------------------------------- #

class TestEmptyRoot:
    def test_returns_empty_report_when_jumps_dir_missing(
        self, tmp_path: Path
    ):
        # Edge case: running reindex on a root that hasn't been
        # bootstrapped yet. No jumps/ directory. Should return a
        # sensible empty report, not crash.
        root = tmp_path / "fresh"
        root.mkdir()
        report = reindex_from_xml(root)
        assert report.folders_scanned == 0
        assert report.jumps_indexed == 0
        assert report.clean

    def test_empty_jumps_dir(self, bootstrapped_root: Path):
        # Bootstrapped but no jumps logged yet. Also clean.
        report = reindex_from_xml(bootstrapped_root)
        assert report.folders_scanned == 0
        assert report.jumps_indexed == 0
        assert report.clean


# --------------------------------------------------------------------------- #
# R.D.3 (D44) — dropzone reindex path
# --------------------------------------------------------------------------- #

class TestDropzoneReindex:
    """Reindex must rebuild the ``dropzones`` SQLite table from
    ``logbook_root/dropzones/*.xml`` files. Same posture as jumps
    (D3): on-disk XML is authoritative; the index is rebuildable.
    """

    def _create_dz(self, root: Path, **overrides):
        from backend.models.dropzone import DropzoneCreate, Environment
        from backend.services.dropzone_service import create_dropzone
        payload_data = dict(
            name="DZ",
            city="City",
            country="US",
            environment=Environment.CLEAN_GRASS,
        )
        payload_data.update(overrides)
        return create_dropzone(root, "default", DropzoneCreate(**payload_data))

    def _fetch_dz_rows(self, root: Path) -> list[dict]:
        result = open_index(root)
        try:
            rows = result.conn.execute(
                "SELECT id, name, city, country, environment, schema_ns, "
                "created_at, updated_at FROM dropzones ORDER BY name"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            result.conn.close()

    def test_indexes_each_dropzone_xml(self, bootstrapped_root: Path):
        a = self._create_dz(bootstrapped_root, name="A")
        b = self._create_dz(bootstrapped_root, name="B")
        # Wipe the table so the rebuild path is exercised.
        result = open_index(bootstrapped_root)
        try:
            result.conn.execute("DELETE FROM dropzones")
        finally:
            result.conn.close()

        report = reindex_from_xml(bootstrapped_root)
        assert report.dropzones_indexed == 2
        assert report.dropzones_scanned == 2
        rows = self._fetch_dz_rows(bootstrapped_root)
        assert {r["id"] for r in rows} == {str(a.id), str(b.id)}

    def test_idempotent(self, bootstrapped_root: Path):
        # Running twice in a row produces identical row state.
        self._create_dz(bootstrapped_root, name="A")
        self._create_dz(bootstrapped_root, name="B")
        reindex_from_xml(bootstrapped_root)
        before = self._fetch_dz_rows(bootstrapped_root)
        reindex_from_xml(bootstrapped_root)
        after = self._fetch_dz_rows(bootstrapped_root)
        assert before == after

    def test_skips_invalid_dropzone_xml(self, bootstrapped_root: Path):
        good = self._create_dz(bootstrapped_root, name="Good")
        # Plant garbage at a UUID-shaped filename so the walk picks
        # it up.
        from uuid import uuid4
        rogue_path = bootstrapped_root / "dropzones" / f"{uuid4()}.xml"
        rogue_path.write_text("not XML")

        # Wipe + reindex.
        result = open_index(bootstrapped_root)
        try:
            result.conn.execute("DELETE FROM dropzones")
        finally:
            result.conn.close()

        report = reindex_from_xml(bootstrapped_root)
        assert report.dropzones_scanned == 2
        assert report.dropzones_indexed == 1
        assert len(report.dropzones_skipped) == 1
        assert report.dropzones_skipped[0][0].startswith("dropzones/")
        rows = self._fetch_dz_rows(bootstrapped_root)
        assert [r["id"] for r in rows] == [str(good.id)]

    def test_skipped_breaks_clean(self, bootstrapped_root: Path):
        # Same posture as jump skip: a dropzone-skipped report is
        # NOT clean — the operator should look at the file.
        from uuid import uuid4
        (bootstrapped_root / "dropzones" / f"{uuid4()}.xml").write_text("garbage")
        report = reindex_from_xml(bootstrapped_root)
        assert not report.clean
        assert report.aborted is None  # skip is not abort
        assert len(report.dropzones_skipped) == 1

    def test_drops_rows_for_files_no_longer_on_disk(
        self, bootstrapped_root: Path
    ):
        # If a DZ file is deleted out-of-band (e.g. ``rm
        # dropzones/<uuid>.xml`` from a shell), the next reindex
        # should drop the corresponding row. Reindex pre-clears the
        # table to make this true (R.D.3 contract).
        a = self._create_dz(bootstrapped_root, name="Stays")
        b = self._create_dz(bootstrapped_root, name="Disappears")
        # Out-of-band delete.
        (bootstrapped_root / "dropzones" / f"{b.id}.xml").unlink()

        report = reindex_from_xml(bootstrapped_root)
        assert report.dropzones_indexed == 1
        rows = self._fetch_dz_rows(bootstrapped_root)
        assert [r["id"] for r in rows] == [str(a.id)]

    def test_jump_abort_does_not_corrupt_dropzones(
        self, bootstrapped_root: Path
    ):
        # If the jump pass aborts (duplicate jump_number), the
        # dropzone table must remain in its pre-reindex state.
        # R.D.3 puts the dropzone walk after the jump COMMIT, so
        # an abort short-circuits before the dropzone DELETE runs.
        from backend.storage.manifest import (
            JUMP_XML_NAME,
            MANIFEST_NAME,
            from_jump_xml,
        )

        a_dz = self._create_dz(bootstrapped_root, name="A")
        # Set up a duplicate-jump-number scenario.
        j1 = create_jump(
            bootstrapped_root, "default", _minimal_create(jump_number=1, title="A")
        )
        create_jump(
            bootstrapped_root, "default", _minimal_create(jump_number=2, title="B")
        )
        folder_b = bootstrapped_root / "jumps" / "[2] B"
        a_xml = (
            bootstrapped_root / "jumps" / "[1] A" / JUMP_XML_NAME
        ).read_bytes()
        forged = a_xml.replace(
            str(j1.id).encode(),
            b"11111111-1111-4111-9111-111111111111",
        )
        (folder_b / JUMP_XML_NAME).write_bytes(forged)
        (folder_b / MANIFEST_NAME).write_bytes(
            from_jump_xml(folder_b, logbook_root=bootstrapped_root)
        )

        report = reindex_from_xml(bootstrapped_root)
        assert report.aborted is not None
        # Dropzones table is whatever create_dropzone left it as —
        # the reindex never reached the dropzone pass.
        rows = self._fetch_dz_rows(bootstrapped_root)
        assert [r["id"] for r in rows] == [str(a_dz.id)]

    def test_d32_timestamp_fallback_for_dropzone(
        self, bootstrapped_root: Path, caplog
    ):
        # Strip <created_at> / <updated_at> from a dropzone XML;
        # reindex must fall back to file mtime and log the WARNING.
        # Mirrors test_xml_without_timestamps_uses_mtime for jumps.
        caplog.set_level(logging.WARNING, logger="backend.services.reindex")
        dz = self._create_dz(bootstrapped_root, name="Strip me")
        dz_path = bootstrapped_root / "dropzones" / f"{dz.id}.xml"
        raw = dz_path.read_text()
        stripped = "\n".join(
            line for line in raw.splitlines()
            if "<created_at>" not in line and "<updated_at>" not in line
        )
        dz_path.write_text(stripped)
        target_mtime = time.mktime((2020, 1, 2, 3, 4, 5, 0, 0, -1))
        os.utime(dz_path, (target_mtime, target_mtime))

        # Wipe + reindex.
        result = open_index(bootstrapped_root)
        try:
            result.conn.execute("DELETE FROM dropzones")
        finally:
            result.conn.close()
        report = reindex_from_xml(bootstrapped_root)

        assert report.dropzones_indexed == 1
        # The fallback was logged.
        warnings = [
            r for r in caplog.records
            if r.message == "reindex_dropzone_timestamp_fallback"
        ]
        assert len(warnings) == 1
        # Index timestamp matches the mtime we set.
        rows = self._fetch_dz_rows(bootstrapped_root)
        assert rows[0]["created_at"].startswith("2020-01-02T")
