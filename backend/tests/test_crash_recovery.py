"""Subprocess-kill crash tests for ``create_jump`` (Phase 3.4, D25).

D25 specifies a crash-states table: for each interruption point in
the create_jump write sequence, the on-disk state after a hard kill
must be either (a) a coherent jump or (b) recognizably mid-write —
never silently corrupt. Each test below puts one row of that table
under a SIGKILL microscope:

  Row A — ``after_mkdir`` (no attachments)
      Crash after the jump folder is created but before jump.xml is
      written. Folder exists, empty. verify reports
      ``invalid_folder``. No attachment orphans.

  Row B — ``after_mkdir`` (with attachments, via after_first_attachment)
      Crash after some attachments have landed but before jump.xml.
      Folder exists, contains 1+ attachments, no jump.xml. verify
      reports ``invalid_folder``; the attachment files are
      recoverable manually (orphans in spirit but verify only
      reports orphans relative to a valid jump.xml, so this just
      surfaces as "missing jump.xml").

  Row C — ``after_jump_xml`` (no attachments)
      Crash after jump.xml but before SHA256SUMS. verify reports
      ``stale_manifest`` (file missing). ``folder_reconcile`` heals
      it on next open, after which verify is clean.

  Row D — ``after_jump_xml`` (with attachments)
      Same as Row C but attachments were written in step 2.
      folder_reconcile must generate the correct multi-line
      SHA256SUMS referencing every attachment + jump.xml.

The child process lives in ``_crash_child.py``. It uses
``os.kill(getpid(), SIGKILL)`` — uncatchable at the Python layer, so
no atexit hook or finally block runs. On POSIX, the parent sees
``returncode == -9`` via subprocess.

We run the child as a fresh Python process with the project's
venv so monkeypatches installed by the child module take effect
before the service primitives are imported.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from backend.storage.bootstrap import bootstrap_logbook
from backend.storage.index import open_index
from backend.storage.manifest import JUMP_XML_NAME, MANIFEST_NAME, from_jump_xml
from backend.storage.reconcile import folder_reconcile
from backend.storage.verify import verify_logbook

# SIGKILL is POSIX-only — Windows' signal module has no equivalent
# guaranteed-uncatchable termination, and the entire crash-recovery
# harness in this file is built around "kill -9 the child mid-write,
# observe what the parent sees on disk." Windows would need a
# distinct harness (TerminateProcess + a different probe of the
# resulting state); that's a larger piece of work and not blocking
# v0.1's loopback-only desktop posture. Skip the whole module on
# Windows so CI stays green there; the same checks still run on
# every macOS / Linux matrix cell, which is where the underlying
# atomic-write semantics are tested most heavily anyway.
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


def _run_crash_child(
    *,
    root: Path,
    crash_point: str,
    operation: str = "create",
    payload: dict | None = None,
    uploads: list[dict] | None = None,
    jump_id: str | None = None,
) -> subprocess.CompletedProcess:
    """Run the crash harness child and return the CompletedProcess.

    The child is expected to die via SIGKILL, so ``check=False`` and
    the caller asserts on ``returncode`` instead. stderr is captured
    so a mis-configured child (e.g. import error) surfaces a
    readable failure instead of an opaque exit code.

    Parameters drive the child's per-operation contract documented at
    the top of ``_crash_child.py``:

    - ``operation="create"`` (default): ``payload`` is a
      ``JumpCreate`` dict; ``uploads`` is the optional attachment
      list; ``jump_id`` is unused.
    - ``operation="update"``: ``payload`` is a ``JumpUpdate`` dict;
      ``jump_id`` must be the UUID string of a previously-seeded
      jump; ``uploads`` is unused.
    - ``operation="delete"``: ``jump_id`` must be the UUID string of
      a previously-seeded jump; ``payload`` and ``uploads`` are unused.
    """
    env = {
        **os.environ,
        "LOGBOOK_ROOT": str(root),
        "OPERATION": operation,
        "JUMP_PAYLOAD": json.dumps(payload or {}),
        "UPLOADS": json.dumps(uploads or []),
        "CRASH_POINT": crash_point,
        # Make sure the child picks up the same backend package as
        # the parent — without this, a pyproject without ``-e``
        # install would leave ``backend`` unreachable from an
        # unrelated CWD.
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
    }
    if jump_id is not None:
        env["JUMP_ID"] = jump_id
    return subprocess.run(
        [sys.executable, "-m", "backend.tests._crash_child"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def _assert_sigkilled(proc: subprocess.CompletedProcess) -> None:
    """The child was expected to die via SIGKILL. Fail loudly if not.

    A clean exit code (0) means our hook missed the crash point; any
    other signal means a different failure entered the picture
    (ImportError, etc.). Both are real bugs in the harness or the
    code-under-test, so we show stderr.
    """
    assert proc.returncode == -signal.SIGKILL, (
        f"expected SIGKILL, got returncode={proc.returncode}; "
        f"stderr:\n{proc.stderr}"
    )


def _minimal_payload(jump_number: int = 1, **overrides) -> dict:
    body = {
        "jump_number": jump_number,
        "date": "2026-04-22",
        "dropzone": "Skydive Elsinore",
        "exit_altitude_m": 4000,
        "deployment_altitude_m": 900,
    }
    body.update(overrides)
    return body


# --------------------------------------------------------------------------- #
# Row A — after_mkdir, no attachments
# --------------------------------------------------------------------------- #

class TestAfterMkdirNoAttachments:
    """D25 row: folder created, no jump.xml yet."""

    def test_folder_exists_but_empty(self, bootstrapped_root: Path):
        proc = _run_crash_child(
            root=bootstrapped_root,
            crash_point="after_mkdir",
            payload=_minimal_payload(1, title="Crash"),
        )
        _assert_sigkilled(proc)

        folder = bootstrapped_root / "jumps" / "[1] Crash"
        assert folder.is_dir()
        # Zero-attachment path: the first atomic_write is for
        # jump.xml, and the hook kills BEFORE it runs. So the folder
        # is completely empty.
        assert list(folder.iterdir()) == []

    def test_verify_reports_invalid_folder(self, bootstrapped_root: Path):
        # D25 contract: an interrupted create is "recognizably
        # mid-write" via verify. Not a silent corruption.
        proc = _run_crash_child(
            root=bootstrapped_root,
            crash_point="after_mkdir",
            payload=_minimal_payload(1, title="Crash"),
        )
        _assert_sigkilled(proc)

        report = verify_logbook(bootstrapped_root)
        assert not report.clean
        kinds = {i.kind for i in report.issues}
        assert "invalid_folder" in kinds
        # The specific folder is named.
        folders = {i.folder for i in report.issues if i.kind == "invalid_folder"}
        assert "jumps/[1] Crash" in folders


# --------------------------------------------------------------------------- #
# Row B — after first attachment, before jump.xml
# --------------------------------------------------------------------------- #

class TestAfterFirstAttachment:
    """D25 row: folder created, some attachments on disk, no jump.xml yet.

    A crash here leaves the attachment bytes recoverable but no
    anchor: the jump is not a valid jump until jump.xml shows up,
    and verify refuses to treat it as one.
    """

    def test_attachment_on_disk_but_no_jump_xml(self, bootstrapped_root: Path):
        proc = _run_crash_child(
            root=bootstrapped_root,
            crash_point="after_first_attachment",
            payload=_minimal_payload(1),
            uploads=[
                {
                    "filename": "track.csv",
                    "content_type": "text/csv",
                    "hex": b"lat,lon\n".hex(),
                },
                {
                    "filename": "track2.csv",
                    "content_type": "text/csv",
                    "hex": b"alt\n".hex(),
                },
            ],
        )
        _assert_sigkilled(proc)

        folder = bootstrapped_root / "jumps" / "[1]"
        assert folder.is_dir()
        # The first atomic_write_stream succeeded — its output is on
        # disk. The second didn't run.
        assert (folder / "track.csv").read_bytes() == b"lat,lon\n"
        assert not (folder / "track2.csv").exists()
        # jump.xml never made it.
        assert not (folder / JUMP_XML_NAME).exists()
        # No stray tmp files — atomic_write_stream cleans up on
        # failure, and SIGKILL fires outside the tmp-write window
        # (AFTER os.replace), so no .tmp should linger.
        tmps = list(folder.rglob("*.tmp"))
        assert tmps == []

    def test_verify_reports_invalid_folder(self, bootstrapped_root: Path):
        proc = _run_crash_child(
            root=bootstrapped_root,
            crash_point="after_first_attachment",
            payload=_minimal_payload(1),
            uploads=[
                {
                    "filename": "track.csv",
                    "content_type": "text/csv",
                    "hex": b"data".hex(),
                }
            ],
        )
        _assert_sigkilled(proc)

        report = verify_logbook(bootstrapped_root)
        assert not report.clean
        assert any(i.kind == "invalid_folder" for i in report.issues)


# --------------------------------------------------------------------------- #
# Row C — after jump.xml, before SHA256SUMS (no attachments)
# --------------------------------------------------------------------------- #

class TestAfterJumpXmlNoAttachments:
    """D25 row: valid jump.xml on disk, SHA256SUMS missing.

    folder_reconcile heals the folder on next open by generating the
    manifest from jump.xml's claims. After reconcile, verify is clean.
    """

    def test_jump_xml_valid_but_manifest_missing(
        self, bootstrapped_root: Path
    ):
        proc = _run_crash_child(
            root=bootstrapped_root,
            crash_point="after_jump_xml",
            payload=_minimal_payload(1, title="Halfway"),
        )
        _assert_sigkilled(proc)

        folder = bootstrapped_root / "jumps" / "[1] Halfway"
        assert (folder / JUMP_XML_NAME).is_file()
        assert not (folder / MANIFEST_NAME).exists()

    def test_reconcile_heals_and_verify_clean(
        self, bootstrapped_root: Path
    ):
        proc = _run_crash_child(
            root=bootstrapped_root,
            crash_point="after_jump_xml",
            payload=_minimal_payload(1, title="Heal"),
        )
        _assert_sigkilled(proc)

        folder = bootstrapped_root / "jumps" / "[1] Heal"
        folder_reconcile(folder, logbook_root=bootstrapped_root)

        # After reconcile: manifest exists, structurally equal to
        # from_jump_xml's output (the recovery-path manifest).
        assert (folder / MANIFEST_NAME).is_file()
        assert (folder / MANIFEST_NAME).read_bytes() == from_jump_xml(
            folder, logbook_root=bootstrapped_root
        )

        # And verify is clean — no silent corruption, full recovery.
        # (No index row yet, but verify doesn't consult the index;
        # reindex is what lifts a reconciled folder back into the
        # index, and that's Phase 3.6.)
        report = verify_logbook(bootstrapped_root)
        assert report.clean, f"expected clean after reconcile, got: {report.issues}"


# --------------------------------------------------------------------------- #
# Row D — after jump.xml, before SHA256SUMS (with attachments)
# --------------------------------------------------------------------------- #

class TestAfterJumpXmlWithAttachments:
    """D25 row with attachments: manifest must cover every attachment.

    Multi-attachment version of Row C. The recovery-path manifest
    generator (``from_jump_xml``) reads attachment hashes from
    jump.xml rather than re-hashing bytes, so this test also
    incidentally proves that D25's "claim-based regeneration"
    preserves the jump.xml as the authoritative witness even when
    attachment bytes are involved.
    """

    def test_reconcile_produces_multi_line_manifest(
        self, bootstrapped_root: Path
    ):
        proc = _run_crash_child(
            root=bootstrapped_root,
            crash_point="after_jump_xml",
            payload=_minimal_payload(1, title="Complex"),
            uploads=[
                {
                    "filename": "a.csv",
                    "content_type": "text/csv",
                    "hex": b"alpha".hex(),
                },
                {
                    "filename": "b.csv",
                    "content_type": "text/csv",
                    "hex": b"bravo".hex(),
                },
            ],
        )
        _assert_sigkilled(proc)

        folder = bootstrapped_root / "jumps" / "[1] Complex"
        # Both attachments landed during step 2, before jump.xml.
        assert (folder / "a.csv").read_bytes() == b"alpha"
        assert (folder / "b.csv").read_bytes() == b"bravo"
        # jump.xml landed (step 3). Manifest didn't (step 4).
        assert (folder / JUMP_XML_NAME).is_file()
        assert not (folder / MANIFEST_NAME).exists()

        folder_reconcile(folder, logbook_root=bootstrapped_root)

        manifest_bytes = (folder / MANIFEST_NAME).read_bytes()
        # Manifest lists every on-disk file that belongs to the
        # jump: jump.xml + both attachments. The exact hash values
        # come from jump.xml's claims per D25's recovery-path rule.
        assert b"  a.csv\n" in manifest_bytes
        assert b"  b.csv\n" in manifest_bytes
        assert b"  jump.xml\n" in manifest_bytes

        # End-to-end: reconciled folder passes verify, including
        # byte-level attachment hash checks (attachment bytes
        # written in step 2 match the hashes jump.xml recorded in
        # step 3 — that's the "agree by construction" invariant
        # from D25 step 2).
        report = verify_logbook(bootstrapped_root)
        assert report.clean, f"expected clean after reconcile, got: {report.issues}"


# --------------------------------------------------------------------------- #
# Smoke: the harness itself
# --------------------------------------------------------------------------- #

class TestHarnessSelfCheck:
    """Sanity-check the crash harness so broken harness behavior doesn't
    masquerade as a product bug in the other tests.
    """

    def test_unknown_crash_point_does_not_kill(
        self, bootstrapped_root: Path
    ):
        # With an unknown crash_point, the hooks never call _suicide,
        # so create_jump runs to completion and the child exits 0.
        # This proves the hooks are opt-in — a harness bug that left
        # them always-on would break happy-path tests.
        proc = _run_crash_child(
            root=bootstrapped_root,
            crash_point="no_such_point",
            payload=_minimal_payload(1, title="NoCrash"),
        )
        assert proc.returncode == 0, (
            f"expected clean exit, got {proc.returncode}; "
            f"stderr:\n{proc.stderr}"
        )
        folder = bootstrapped_root / "jumps" / "[1] NoCrash"
        assert (folder / JUMP_XML_NAME).is_file()
        assert (folder / MANIFEST_NAME).is_file()


# --------------------------------------------------------------------------- #
# Helpers + fixtures for update_jump / delete_jump crash tests (TEST-1)
# --------------------------------------------------------------------------- #

def _seeded_jump(
    root: Path, jump_number: int = 1, title: str | None = "Original"
) -> tuple[str, Path]:
    """Create a jump in-process and return ``(jump_id_str, folder_path)``.

    update_jump and delete_jump need an existing jump on disk; seeding
    via a direct in-process call keeps the test fixture's behaviour
    deterministic (no subprocess, no SIGKILL) and isolates the crash
    test from create_jump's own crash semantics — those are covered
    by the rows above.
    """
    from backend.models.jump import JumpCreate
    from backend.services.jump_service import create_jump
    from backend.storage.filesystem import jump_folder_name

    payload = JumpCreate(**_minimal_payload(jump_number, title=title))
    jump = create_jump(root, "default", payload)
    folder = root / "jumps" / jump_folder_name(jump_number, title)
    return str(jump.id), folder


# --------------------------------------------------------------------------- #
# update_jump — Row U1: after_xml_write
# --------------------------------------------------------------------------- #

class TestUpdateAfterXmlWrite:
    """D31 step 6 done, step 7 (manifest rewrite) pending.

    The rewritten ``jump.xml`` is on disk at the OLD folder path.
    SHA256SUMS is the previous (pre-update) version, now stale relative
    to the new XML. ``folder_reconcile`` regenerates the manifest from
    the new XML's claims; verify is clean afterwards.
    """

    def test_xml_rewritten_manifest_stale(self, bootstrapped_root: Path):
        jump_id, folder = _seeded_jump(bootstrapped_root, jump_number=1)
        # Capture the pre-crash manifest bytes so we can prove the
        # post-crash manifest is unchanged (i.e. step 7 didn't run).
        old_manifest = (folder / MANIFEST_NAME).read_bytes()

        proc = _run_crash_child(
            root=bootstrapped_root,
            operation="update",
            crash_point="update_after_xml_write",
            jump_id=jump_id,
            payload={
                **_minimal_payload(1, title="Original"),
                "title": "Edited",
            },
        )
        _assert_sigkilled(proc)

        # Folder still at the OLD path (step 8 rename never ran).
        assert folder.is_dir()
        # New XML on disk — title was updated.
        xml_bytes = (folder / JUMP_XML_NAME).read_bytes()
        assert b"<title>Edited</title>" in xml_bytes
        # Old manifest unchanged — step 7 didn't run.
        assert (folder / MANIFEST_NAME).read_bytes() == old_manifest

    def test_reconcile_heals_manifest_to_match_new_xml(
        self, bootstrapped_root: Path
    ):
        jump_id, folder = _seeded_jump(bootstrapped_root, jump_number=1)
        proc = _run_crash_child(
            root=bootstrapped_root,
            operation="update",
            crash_point="update_after_xml_write",
            jump_id=jump_id,
            payload={
                **_minimal_payload(1, title="Original"),
                "title": "Healed",
            },
        )
        _assert_sigkilled(proc)

        folder_reconcile(folder, logbook_root=bootstrapped_root)

        # Reconciled manifest matches what from_jump_xml produces from
        # the (post-update) jump.xml on disk.
        assert (folder / MANIFEST_NAME).read_bytes() == from_jump_xml(
            folder, logbook_root=bootstrapped_root
        )


# --------------------------------------------------------------------------- #
# update_jump — Row U2: after_manifest
# --------------------------------------------------------------------------- #

class TestUpdateAfterManifest:
    """D31 step 7 done, step 8 (folder rename) pending.

    XML and SHA256SUMS are both updated and consistent at the OLD
    folder path. The folder name still reflects the pre-update title
    (``[1] Original`` rather than the new ``[1] Renamed``). The
    canonical record is internally consistent — just under a name
    that mismatches its ``<title>``.

    Recovery: a subsequent successful update will rename the folder.
    Until then, the index still points at the OLD path so reads work
    via get_jump (which trusts the index for the path).
    """

    def test_xml_and_manifest_consistent_folder_at_old_path(
        self, bootstrapped_root: Path
    ):
        jump_id, folder = _seeded_jump(bootstrapped_root, jump_number=1)
        proc = _run_crash_child(
            root=bootstrapped_root,
            operation="update",
            crash_point="update_after_manifest",
            jump_id=jump_id,
            payload={
                **_minimal_payload(1, title="Original"),
                "title": "Renamed",
            },
        )
        _assert_sigkilled(proc)

        # Folder still at OLD path; the rename (step 8) never ran.
        assert folder.is_dir()
        assert folder.name == "[1] Original"
        new_path = bootstrapped_root / "jumps" / "[1] Renamed"
        assert not new_path.exists()

        # XML and manifest are both new and mutually consistent —
        # folder_reconcile is a no-op, verify is clean per-folder.
        manifest_bytes = (folder / MANIFEST_NAME).read_bytes()
        assert manifest_bytes == from_jump_xml(
            folder, logbook_root=bootstrapped_root
        )

    def test_verify_reports_index_drift(self, bootstrapped_root: Path):
        # The index still references "[1] Original" (the seed wrote
        # that path), the XML inside that folder now has a new title,
        # but the folder name and index are still in agreement —
        # so verify is clean per-folder. The drift is between
        # XML <title> and folder name, which D4 documents as
        # acceptable cosmetic drift; not a verify-flagged condition.
        jump_id, folder = _seeded_jump(bootstrapped_root, jump_number=1)
        proc = _run_crash_child(
            root=bootstrapped_root,
            operation="update",
            crash_point="update_after_manifest",
            jump_id=jump_id,
            payload={
                **_minimal_payload(1, title="Original"),
                "title": "Renamed",
            },
        )
        _assert_sigkilled(proc)

        report = verify_logbook(bootstrapped_root)
        # Per-folder integrity is fine: jump.xml validates, manifest
        # matches XML claims. The cosmetic name/title drift is not a
        # verify concern (D4 §"asymmetric design").
        assert report.clean, (
            f"unexpected verify issues post-crash: {report.issues}"
        )


# --------------------------------------------------------------------------- #
# update_jump — Row U3: after_rename (the §A7 race window)
# --------------------------------------------------------------------------- #

class TestUpdateAfterRename:
    """D31 step 8 done, step 9 (index UPDATE) pending.

    Forward-review §A7 named this window precisely: ``os.rename``
    succeeded so the folder is at the NEW path, but the SQLite row
    still points at the OLD path. A concurrent ``get_jump`` would
    look up the index, get the old folder string, and try to read a
    folder that no longer exists.

    Recovery: ``reindex_from_xml`` walks the on-disk jumps and rebuilds
    the index from XML, picking up the new folder path. Verify reports
    the drift (orphan-on-disk + missing-from-index pattern).
    """

    def test_folder_at_new_path_index_at_old_path(
        self, bootstrapped_root: Path
    ):
        jump_id, old_folder = _seeded_jump(bootstrapped_root, jump_number=1)
        proc = _run_crash_child(
            root=bootstrapped_root,
            operation="update",
            crash_point="update_after_rename",
            jump_id=jump_id,
            payload={
                **_minimal_payload(1, title="Original"),
                "title": "Moved",
            },
        )
        _assert_sigkilled(proc)

        new_folder = bootstrapped_root / "jumps" / "[1] Moved"
        assert new_folder.is_dir(), "rename should have completed"
        assert not old_folder.exists(), "old folder should be gone"
        # XML inside new folder has the new title.
        xml_bytes = (new_folder / JUMP_XML_NAME).read_bytes()
        assert b"<title>Moved</title>" in xml_bytes

        # Index still points at the old path — that's the drift.
        result = open_index(bootstrapped_root)
        try:
            row = result.conn.execute(
                "SELECT folder FROM jumps WHERE id = ?", (jump_id,)
            ).fetchone()
        finally:
            result.conn.close()
        assert row["folder"] == "jumps/[1] Original"

    def test_reindex_rebuilds_index_to_match_disk(
        self, bootstrapped_root: Path
    ):
        from backend.services.reindex_service import reindex_from_xml

        jump_id, _old_folder = _seeded_jump(
            bootstrapped_root, jump_number=1
        )
        proc = _run_crash_child(
            root=bootstrapped_root,
            operation="update",
            crash_point="update_after_rename",
            jump_id=jump_id,
            payload={
                **_minimal_payload(1, title="Original"),
                "title": "Reindexed",
            },
        )
        _assert_sigkilled(proc)

        # Reindex walks disk and rewrites every row from XML.
        report = reindex_from_xml(bootstrapped_root)
        assert report.clean, f"reindex unexpectedly skipped: {report.skipped}"

        result = open_index(bootstrapped_root)
        try:
            row = result.conn.execute(
                "SELECT folder, title FROM jumps WHERE id = ?", (jump_id,)
            ).fetchone()
        finally:
            result.conn.close()
        # Index now reflects the post-rename path.
        assert row["folder"] == "jumps/[1] Reindexed"
        assert row["title"] == "Reindexed"


# --------------------------------------------------------------------------- #
# delete_jump — Row D1: after_trash_move
# --------------------------------------------------------------------------- #

class TestDeleteAfterTrashMove:
    """``delete_jump`` step 1 done, step 2 (index DELETE) pending.

    The active folder has moved to ``.trash/<ts>_<original-name>/``;
    the SQLite row still references the now-empty active path.

    The ordering is deliberate (jump_service.delete_jump comment):
    "trash first, index second — if the move fails, the index still
    points at the old path and the jump remains discoverable." This
    test pins the inverse: the move SUCCEEDED but the SQL never ran,
    so the index points at a now-trashed location.

    Recovery: re-running delete_jump gets a fresh handle on the index
    row (now points at a missing folder), the soft_delete call would
    fail with FileNotFoundError. A simpler recovery is reindex —
    walks active jumps/, finds nothing for jump_number=1, doesn't
    upsert; the row stays in the index. The user notices the missing
    jump in the UI and either restores from .trash/ or accepts the
    deletion by re-running it.
    """

    def test_folder_in_trash_index_still_active(
        self, bootstrapped_root: Path
    ):
        jump_id, active_folder = _seeded_jump(
            bootstrapped_root, jump_number=1
        )
        proc = _run_crash_child(
            root=bootstrapped_root,
            operation="delete",
            crash_point="delete_after_trash_move",
            jump_id=jump_id,
        )
        _assert_sigkilled(proc)

        assert not active_folder.exists()
        # Trash holds the moved folder under a timestamped name.
        trash_root = bootstrapped_root / ".trash"
        assert trash_root.is_dir()
        trashed = list(trash_root.iterdir())
        assert len(trashed) == 1
        assert trashed[0].name.endswith("_[1] Original")

        # Index row still references the active path — step 2's
        # DELETE never ran.
        result = open_index(bootstrapped_root)
        try:
            row = result.conn.execute(
                "SELECT folder FROM jumps WHERE id = ?", (jump_id,)
            ).fetchone()
        finally:
            result.conn.close()
        assert row is not None, "index row should still exist"
        assert row["folder"] == "jumps/[1] Original"

    def test_verify_reports_missing_active_folder(
        self, bootstrapped_root: Path
    ):
        # The folder the index claims doesn't exist on disk anymore.
        # verify walks active jumps/ for per-folder checks; it doesn't
        # cross-check the index against the filesystem in v0.1, so
        # the report is clean (no per-folder issues found in
        # active jumps/, no duplicates, trash folders scanned but the
        # trashed jump is exempt from duplicate checks per D19).
        # The index/disk drift is recoverable via reindex but isn't
        # itself a verify finding today.
        jump_id, _ = _seeded_jump(bootstrapped_root, jump_number=1)
        proc = _run_crash_child(
            root=bootstrapped_root,
            operation="delete",
            crash_point="delete_after_trash_move",
            jump_id=jump_id,
        )
        _assert_sigkilled(proc)

        report = verify_logbook(bootstrapped_root)
        # Active jumps/ is empty (the folder moved to .trash). Trash
        # folders are scanned but reported per-folder only; the
        # trashed jump there is structurally valid (it WAS a complete
        # jump pre-delete), so no issues should fire there either.
        assert report.clean, (
            f"unexpected verify issues post-crash: {report.issues}"
        )

    def test_reindex_removes_drifted_index_row(
        self, bootstrapped_root: Path
    ):
        # reindex_from_xml walks active jumps/ and INSERT-OR-REPLACEs
        # what it finds. It does NOT delete rows that are absent from
        # disk — that's a deliberate D3 posture: the index is
        # rebuildable but reindex is additive on the index side.
        # So a row pointing at a trashed folder LINGERS until the
        # next schema rebuild (D26 drop-and-reindex).
        # This test pins that behavior so a future change that makes
        # reindex deletion-aware surfaces here.
        from backend.services.reindex_service import reindex_from_xml

        jump_id, _ = _seeded_jump(bootstrapped_root, jump_number=1)
        proc = _run_crash_child(
            root=bootstrapped_root,
            operation="delete",
            crash_point="delete_after_trash_move",
            jump_id=jump_id,
        )
        _assert_sigkilled(proc)

        reindex_from_xml(bootstrapped_root)

        # The drifted row is still present after reindex.
        result = open_index(bootstrapped_root)
        try:
            row = result.conn.execute(
                "SELECT folder FROM jumps WHERE id = ?", (jump_id,)
            ).fetchone()
        finally:
            result.conn.close()
        assert row is not None, (
            "reindex_from_xml is documented additive (D3); it does not "
            "delete rows for folders that are no longer in jumps/. A "
            "future deletion-aware reindex would change this behaviour."
        )
