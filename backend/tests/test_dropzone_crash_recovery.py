"""Subprocess-kill crash tests for dropzone CRUD (D44, TEST-2 — audit 2026-04-29).

D25's crash-states invariant applies to every multi-file/multi-step
write, not just ``create_jump``. Dropzone CRUD has the same shape:

  * ``create_dropzone``: atomic_write the new XML → INSERT OR REPLACE
    the index row.
  * ``update_dropzone``: atomic_write the rewritten XML → INSERT OR
    REPLACE.
  * ``delete_dropzone``: soft_delete_file the active XML →
    DELETE FROM dropzones.

Each test below kills the process between the two halves of one of
those flows and asserts the post-crash state is recognisably
mid-write — never silently corrupt.

The harness lives in ``_crash_child.py`` and is shared with
``test_crash_recovery.py`` (jump CRUD). Crash points used here:

  ``dropzone_create_after_xml_write``
      atomic_write returned for ``dropzones/<uuid>.xml``; SQLite
      INSERT did not. ``reindex_from_xml`` repopulates the row on
      next launch (D3 contract).
  ``dropzone_update_after_xml_write``
      atomic_write returned with the rewritten XML; SQLite INSERT
      OR REPLACE did not. Index reflects pre-update fields; reindex
      reconciles next launch.
  ``dropzone_delete_after_trash_move``
      soft_delete_file moved the XML to ``.trash/dropzones/``;
      SQLite DELETE did not. Index references a now-trashed UUID;
      reindex notices the missing file and removes the row.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from backend.models.dropzone import Dropzone, DropzoneCreate, Environment
from backend.services.dropzone_service import (
    create_dropzone,
    list_dropzones,
)
from backend.services.reindex_service import reindex_from_xml
from backend.storage.bootstrap import bootstrap_logbook
from backend.storage.index import open_index

# Same Windows constraint as test_crash_recovery.py — see the comment
# there for the full rationale. Skip the whole module on Windows so
# CI stays green; the harness needs a Windows-specific reimplementation
# (TerminateProcess + a different "did the child die where we asked?"
# probe), which is post-v0.1 work.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="crash-recovery harness uses POSIX SIGKILL; Windows needs its own",
)

# --------------------------------------------------------------------------- #
# Fixtures + helpers
# --------------------------------------------------------------------------- #

@pytest.fixture
def bootstrapped_root(tmp_path: Path) -> Path:
    root = tmp_path / "logbook"
    bootstrap_logbook(root)
    result = open_index(root)
    result.conn.close()
    return root


def _run_dropzone_crash_child(
    *,
    root: Path,
    operation: str,
    crash_point: str,
    payload: dict | None = None,
    dropzone_id: str | None = None,
    timeout: float = 10.0,
) -> subprocess.CompletedProcess:
    """Run the crash harness child for a dropzone op and return the proc.

    Mirrors ``_run_crash_child`` in ``test_crash_recovery.py`` but with
    the dropzone-side env-var contract. The child is expected to die
    via SIGKILL — ``check=False`` and the caller asserts on
    ``returncode``.
    """
    env = {
        **os.environ,
        "LOGBOOK_ROOT": str(root),
        "OPERATION": operation,
        "DROPZONE_PAYLOAD": json.dumps(payload or {}),
        "CRASH_POINT": crash_point,
        # Same PYTHONPATH trick as the jump tests — keeps the child
        # finding ``backend`` from any cwd.
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
    }
    if dropzone_id is not None:
        env["DROPZONE_ID"] = dropzone_id
    return subprocess.run(
        [sys.executable, "-m", "backend.tests._crash_child"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _assert_sigkilled(proc: subprocess.CompletedProcess) -> None:
    assert proc.returncode == -signal.SIGKILL, (
        f"expected SIGKILL, got returncode={proc.returncode}; "
        f"stderr:\n{proc.stderr}"
    )


def _minimal_dropzone_payload(name: str = "Skydive Elsinore") -> dict:
    return {
        "name": name,
        "city": "Lake Elsinore",
        "country": "US",
        "environment": Environment.DUST_SAND_SALT.value,
        "aircraft": [],
    }


def _seed_dropzone(root: Path, name: str = "Skydive Elsinore") -> Dropzone:
    """Create a dropzone in-process and return it.

    Used for update/delete crash setup — same posture as
    ``_seeded_jump`` in ``test_crash_recovery.py``: avoid involving
    the create-path's crash semantics in tests of update/delete.
    """
    payload = DropzoneCreate(**_minimal_dropzone_payload(name=name))
    return create_dropzone(root, "default", payload)


def _index_has_dropzone(root: Path, dropzone_id: UUID) -> bool:
    result = open_index(root)
    try:
        row = result.conn.execute(
            "SELECT id FROM dropzones WHERE id = ?", (str(dropzone_id),)
        ).fetchone()
        return row is not None
    finally:
        result.conn.close()


def _index_dropzone_name(root: Path, dropzone_id: UUID) -> str | None:
    result = open_index(root)
    try:
        row = result.conn.execute(
            "SELECT name FROM dropzones WHERE id = ?", (str(dropzone_id),)
        ).fetchone()
        return row["name"] if row is not None else None
    finally:
        result.conn.close()


# --------------------------------------------------------------------------- #
# create_dropzone — Row DC1: after_xml_write
# --------------------------------------------------------------------------- #

class TestDropzoneCreateAfterXmlWrite:
    """D25-class invariant: XML on disk, index row missing.

    The XML is the authoritative record (D3); the index is rebuildable
    from XML. ``reindex_from_xml`` is what closes the gap.
    """

    def test_xml_on_disk_index_row_missing(
        self, bootstrapped_root: Path
    ) -> None:
        proc = _run_dropzone_crash_child(
            root=bootstrapped_root,
            operation="dropzone-create",
            crash_point="dropzone_create_after_xml_write",
            payload=_minimal_dropzone_payload(),
        )
        _assert_sigkilled(proc)

        # XML files in the dropzones folder — exactly one was written.
        dz_dir = bootstrapped_root / "dropzones"
        xml_files = sorted(p for p in dz_dir.glob("*.xml"))
        assert len(xml_files) == 1
        new_id = UUID(xml_files[0].stem)

        # Index does NOT see this id.
        assert not _index_has_dropzone(bootstrapped_root, new_id)

        # Other invariants: the listed dropzones (read from index) is
        # empty — the XML is the only place the new DZ exists.
        assert list_dropzones(bootstrapped_root, "default") == []

    def test_reindex_repopulates_missing_row(
        self, bootstrapped_root: Path
    ) -> None:
        proc = _run_dropzone_crash_child(
            root=bootstrapped_root,
            operation="dropzone-create",
            crash_point="dropzone_create_after_xml_write",
            payload=_minimal_dropzone_payload(name="Reindex DZ"),
        )
        _assert_sigkilled(proc)

        # Sanity: index empty pre-reindex.
        assert list_dropzones(bootstrapped_root, "default") == []

        reindex_from_xml(bootstrapped_root)

        # Post-reindex: the dropzone is back in the index, and a list
        # call sees it via the index path.
        names = [
            dz.name for dz in list_dropzones(bootstrapped_root, "default")
        ]
        assert names == ["Reindex DZ"]


# --------------------------------------------------------------------------- #
# update_dropzone — Row DU1: after_xml_write
# --------------------------------------------------------------------------- #

class TestDropzoneUpdateAfterXmlWrite:
    """D25-class invariant: rewritten XML on disk, index row stale.

    The post-crash state is internally consistent at the XML layer
    (the new field values are durable on disk); the SQLite index
    still carries the pre-update field values. ``reindex_from_xml``
    is what reconciles.
    """

    def test_xml_rewritten_index_stale(
        self, bootstrapped_root: Path
    ) -> None:
        dz = _seed_dropzone(bootstrapped_root, name="Original")
        # Pre-crash sanity: the index sees the seeded name.
        assert _index_dropzone_name(bootstrapped_root, dz.id) == "Original"

        new_payload = _minimal_dropzone_payload(name="Edited")
        proc = _run_dropzone_crash_child(
            root=bootstrapped_root,
            operation="dropzone-update",
            crash_point="dropzone_update_after_xml_write",
            payload=new_payload,
            dropzone_id=str(dz.id),
        )
        _assert_sigkilled(proc)

        # XML on disk has the new name.
        xml_path = bootstrapped_root / "dropzones" / f"{dz.id}.xml"
        assert xml_path.is_file()
        assert b"<name>Edited</name>" in xml_path.read_bytes()

        # Index still carries the pre-update name (row was not
        # updated — that's the crash point we hit).
        assert _index_dropzone_name(bootstrapped_root, dz.id) == "Original"

    def test_reindex_heals_index_to_match_new_xml(
        self, bootstrapped_root: Path
    ) -> None:
        dz = _seed_dropzone(bootstrapped_root, name="Original")

        new_payload = _minimal_dropzone_payload(name="Healed")
        proc = _run_dropzone_crash_child(
            root=bootstrapped_root,
            operation="dropzone-update",
            crash_point="dropzone_update_after_xml_write",
            payload=new_payload,
            dropzone_id=str(dz.id),
        )
        _assert_sigkilled(proc)

        reindex_from_xml(bootstrapped_root)

        assert _index_dropzone_name(bootstrapped_root, dz.id) == "Healed"


# --------------------------------------------------------------------------- #
# delete_dropzone — Row DD1: after_trash_move
# --------------------------------------------------------------------------- #

class TestDropzoneDeleteAfterTrashMove:
    """D25-class invariant: active XML moved to trash, index row stale.

    The trash move is the destructive step; once done, the dropzone is
    no longer in the active dataset. The index still references the
    UUID until the SQLite DELETE runs; ``reindex_from_xml`` notices
    the missing file and removes the row.
    """

    def test_file_in_trash_index_row_stale(
        self, bootstrapped_root: Path
    ) -> None:
        dz = _seed_dropzone(bootstrapped_root, name="To-Delete")
        assert _index_has_dropzone(bootstrapped_root, dz.id)

        proc = _run_dropzone_crash_child(
            root=bootstrapped_root,
            operation="dropzone-delete",
            crash_point="dropzone_delete_after_trash_move",
            dropzone_id=str(dz.id),
        )
        _assert_sigkilled(proc)

        # Active XML is gone from the dropzones folder.
        active = bootstrapped_root / "dropzones" / f"{dz.id}.xml"
        assert not active.exists()

        # A trashed copy exists under .trash/dropzones/.
        trashed = list((bootstrapped_root / ".trash" / "dropzones").glob(
            f"*{dz.id}*"
        ))
        assert len(trashed) == 1, trashed

        # Index still references the (now-orphaned) UUID.
        assert _index_has_dropzone(bootstrapped_root, dz.id)

    def test_reindex_removes_orphan_index_row(
        self, bootstrapped_root: Path
    ) -> None:
        dz = _seed_dropzone(bootstrapped_root, name="Orphaned")

        proc = _run_dropzone_crash_child(
            root=bootstrapped_root,
            operation="dropzone-delete",
            crash_point="dropzone_delete_after_trash_move",
            dropzone_id=str(dz.id),
        )
        _assert_sigkilled(proc)

        # Sanity: still orphaned pre-reindex.
        assert _index_has_dropzone(bootstrapped_root, dz.id)

        reindex_from_xml(bootstrapped_root)

        # Post-reindex: the index no longer references the UUID; the
        # active dataset reflects the deletion.
        assert not _index_has_dropzone(bootstrapped_root, dz.id)
        assert list_dropzones(bootstrapped_root, "default") == []


# --------------------------------------------------------------------------- #
# Harness self-check — guard against silently-broken crash points
# --------------------------------------------------------------------------- #

class TestDropzoneHarnessSelfCheck:
    """If the harness misconfigures a crash point, the child reaches
    end-of-main without dying — returncode 0 and a fully-applied
    write. Pin that the child uses the documented sentinel and
    surfaces a clear failure if not.
    """

    def test_unknown_crash_point_completes_normally(
        self, bootstrapped_root: Path
    ) -> None:
        # An unknown crash_point string is harmless to the child —
        # the hooks are conditionals; none fire — so the create
        # completes normally and the child exits 0. Pin that, so a
        # typo in the actual crash point would surface (mismatch
        # → no SIGKILL → assertion later).
        proc = _run_dropzone_crash_child(
            root=bootstrapped_root,
            operation="dropzone-create",
            crash_point="this-sentinel-does-not-exist",
            payload=_minimal_dropzone_payload(name="No-Crash"),
        )
        assert proc.returncode == 0, proc.stderr
        # The DZ landed normally — both XML and index.
        names = [
            dz.name for dz in list_dropzones(bootstrapped_root, "default")
        ]
        assert "No-Crash" in names

    def test_unknown_dropzone_id_in_update_surfaces_cleanly(
        self, bootstrapped_root: Path
    ) -> None:
        # NotFoundError surfaces as a non-zero exit code (Python
        # default for an uncaught exception). The child does NOT
        # SIGKILL because the hook only fires after atomic_write
        # returns — but atomic_write is never reached (the read of
        # the current dropzone fails first).
        proc = _run_dropzone_crash_child(
            root=bootstrapped_root,
            operation="dropzone-update",
            crash_point="dropzone_update_after_xml_write",
            payload=_minimal_dropzone_payload(name="Whatever"),
            dropzone_id=str(uuid4()),
        )
        # Some non-zero, non-SIGKILL exit (Python's default).
        assert proc.returncode != 0
        assert proc.returncode != -signal.SIGKILL
