"""Subprocess-side crash harness for D25 crash-state tests.

Invoked by ``test_crash_recovery.py`` as a subprocess: dispatches one
of three jump-service operations (``create``, ``update``, ``delete``)
and ``os.kill(getpid(), SIGKILL)``s itself at a specific step
boundary so the parent can assert the resulting on-disk state matches
D25's crash-states table.

Why a separate Python module and not inline code in the test: we want
the crash to be a real process-level SIGKILL with nothing between it
and the kernel, so the parent can depend on "the OS killed this
process" semantics (no exception-handler rescue, no atexit hook). A
subprocess with a clean entrypoint is the closest we can get to a
power-loss scenario in unit-test territory.

Contract with the parent:

  Input env vars
  --------------
  ``LOGBOOK_ROOT`` : absolute path to the bootstrapped logbook folder.
  ``OPERATION``    : one of ``"create"``, ``"update"``, ``"delete"``.
                     Default ``"create"`` for backward-compatibility
                     with the original Phase 3.4 harness.
  ``JUMP_PAYLOAD`` : JSON document. For ``create``: a ``JumpCreate``
                     shape. For ``update``: a ``JumpUpdate`` shape.
                     Ignored for ``delete``.
  ``UPLOADS``      : JSON list of ``{"filename", "content_type",
                     "hex"}`` dicts. Only used by ``create``.
  ``JUMP_ID``      : UUID string of the jump to update or delete.
                     Required for ``update`` and ``delete``; ignored
                     for ``create``.
  ``CRASH_POINT``  : one of the sentinel strings documented below.

  Crash points (create_jump — Phase 3.4)
  --------------------------------------
  ``after_mkdir``
      SIGKILL after the jump folder ``mkdir(exist_ok=False)`` succeeds,
      BEFORE any attachment or jump.xml write. Models the "mkdir
      done, nothing else on disk" row of D25's table.

  ``after_first_attachment``
      SIGKILL after the first ``atomic_write_stream`` call returns
      successfully (one attachment on disk), BEFORE any further
      write. Models the "folder has some attachments, no jump.xml"
      row of D25's table.

  ``after_jump_xml``
      SIGKILL after ``atomic_write(jump.xml, ...)`` returns
      successfully, BEFORE the SHA256SUMS atomic_write. Models the
      "valid jump.xml, stale/missing SHA256SUMS" row of D25's table.

  Crash points (update_jump — TEST-1, audit 2026-04-29)
  -----------------------------------------------------
  ``update_after_xml_write``
      SIGKILL after ``atomic_write`` returns for the rewritten
      ``jump.xml`` (D31 step 6) but BEFORE the manifest rewrite
      (step 7). Folder still at the OLD path; manifest is stale.
      ``folder_reconcile`` heals the manifest on next read.

  ``update_after_manifest``
      SIGKILL after ``atomic_write`` returns for ``SHA256SUMS``
      (D31 step 7) but BEFORE the optional folder rename (step 8).
      Folder still at the OLD path; the rewritten content's title
      may not match the folder name. Self-heals on a subsequent
      successful update.

  ``update_after_rename``
      SIGKILL after ``os.rename`` succeeds (D31 step 8) but BEFORE
      the index UPDATE (step 9). Folder is at the NEW path; the
      SQLite row still points at the OLD path. This is the precise
      race window named in 2026-04-23 forward-review §A7. Verify
      should detect the index/disk drift; reindex from XML reconciles.

  Crash points (delete_jump — TEST-1, audit 2026-04-29)
  -----------------------------------------------------
  ``delete_after_trash_move``
      SIGKILL after ``soft_delete`` returns (the active folder is
      now in ``.trash/<ts>_<name>/``) but BEFORE the SQLite DELETE
      runs. Active path no longer exists; the index still points at
      it. Per the deliberate ordering in ``delete_jump`` (trash
      first, index second), the jump remains discoverable as long
      as the index sees its old folder reference — the user re-runs
      delete or the next reindex notices the missing path.

  Crash points (dropzone CRUD — TEST-2, audit 2026-04-29)
  -------------------------------------------------------
  ``dropzone_create_after_xml_write``
      SIGKILL after ``atomic_write`` returns for the new
      ``dropzones/<uuid>.xml`` (XML on disk) but BEFORE the
      ``_upsert_index_row`` call commits the SQLite row. Disk has
      authoritative XML; the index does not see the dropzone yet.
      ``reindex_from_xml`` repopulates the missing row (D3).

  ``dropzone_update_after_xml_write``
      SIGKILL after ``atomic_write`` returns for the rewritten
      ``dropzones/<uuid>.xml`` (new content on disk) but BEFORE the
      ``_upsert_index_row`` runs. Disk holds the new authoritative
      bytes; the index still reflects the pre-update fields.
      ``reindex_from_xml`` reconciles on next launch.

  ``dropzone_delete_after_trash_move``
      SIGKILL after ``soft_delete_file`` returns (the dropzone XML
      now lives under ``.trash/dropzones/<ts>_<uuid>.xml``) but
      BEFORE the ``_delete_index_row`` SQL runs. The active path is
      gone; the index still points at the trashed UUID. The next
      reindex notices the missing file and removes the row.

  Input env vars (dropzone)
  -------------------------
  ``DROPZONE_PAYLOAD`` : JSON document. For
                          ``dropzone-create``: a ``DropzoneCreate``
                          shape. For ``dropzone-update``: a
                          ``DropzoneUpdate`` shape. Ignored for
                          ``dropzone-delete``.
  ``DROPZONE_ID``      : UUID string of the dropzone to update or
                          delete. Required for those operations.

  Exit semantics
  --------------
  On SIGKILL, the POSIX return code visible to the parent is ``-9``
  (subprocess translates signals to negative return codes). On any
  path that reaches a clean end-of-main the return code is 0, which
  is a bug (the kill should have fired) — the parent asserts on the
  signal value.
"""
from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path


def _suicide() -> None:
    """Kill this process hard — no cleanup, no exception handling.

    ``os.kill(getpid(), SIGKILL)`` is the closest we can get in Python
    to "power cord yanked" — the default handler is to terminate
    immediately, and SIGKILL is uncatchable, so no finally blocks or
    atexit hooks run. Exactly what D25's crash-table rows need to
    reproduce.
    """
    os.kill(os.getpid(), signal.SIGKILL)


def _install_create_hooks(crash_point: str) -> None:
    """Hooks for the create_jump crash points (Phase 3.4)."""
    from backend.services import jump_service
    from backend.storage import filesystem

    orig_aw = filesystem.atomic_write
    orig_aws = filesystem.atomic_write_stream

    stream_call_count = {"n": 0}

    def hooked_atomic_write_stream(path, chunks):
        if crash_point == "after_mkdir":
            # Kill BEFORE the write happens: mkdir just ran, nothing
            # of ours is on disk yet. Only relevant when the payload
            # includes attachments; zero-attachment payloads crash
            # via the atomic_write hook below on jump.xml instead.
            _suicide()
        result = orig_aws(path, chunks)
        stream_call_count["n"] += 1
        if (
            crash_point == "after_first_attachment"
            and stream_call_count["n"] == 1
        ):
            _suicide()
        return result

    def hooked_atomic_write(path, data):
        path = Path(path)
        if crash_point == "after_mkdir" and path.name == "jump.xml":
            # Zero-attachment path: first write inside the jump folder
            # is jump.xml. Kill BEFORE it lands.
            _suicide()
        result = orig_aw(path, data)
        if crash_point == "after_jump_xml" and path.name == "jump.xml":
            # jump.xml is on disk; kill BEFORE SHA256SUMS runs.
            _suicide()
        return result

    # Patch both the storage module (authoritative) and the service's
    # already-imported references (what actually gets called).
    filesystem.atomic_write = hooked_atomic_write
    filesystem.atomic_write_stream = hooked_atomic_write_stream
    jump_service.atomic_write = hooked_atomic_write
    jump_service.atomic_write_stream = hooked_atomic_write_stream


def _install_update_hooks(crash_point: str) -> None:
    """Hooks for the update_jump crash points (TEST-1).

    update_jump's write order (D31 §"9-step ordering"):
      step 6: atomic_write(current_folder / "jump.xml", ...)
      step 7: atomic_write(current_folder / "SHA256SUMS", ...)
      step 8: os.rename(current_folder, new_folder)   [if renamed]
      step 9: UPDATE jumps SET ... WHERE id = ?

    Each crash point fires AFTER the named step returns, BEFORE the
    next step begins.
    """
    from backend.services import jump_service
    from backend.storage import filesystem

    orig_aw = filesystem.atomic_write

    def hooked_atomic_write(path, data):
        path = Path(path)
        result = orig_aw(path, data)
        if crash_point == "update_after_xml_write" and path.name == "jump.xml":
            _suicide()
        if (
            crash_point == "update_after_manifest"
            and path.name == "SHA256SUMS"
        ):
            _suicide()
        return result

    filesystem.atomic_write = hooked_atomic_write
    jump_service.atomic_write = hooked_atomic_write

    # update_jump calls ``os.rename`` directly (it imports ``os`` at
    # module level). Patching ``jump_service.os.rename`` reaches the
    # exact symbol the service resolves at call time.
    orig_rename = jump_service.os.rename

    def hooked_os_rename(src, dst):
        result = orig_rename(src, dst)
        if crash_point == "update_after_rename":
            _suicide()
        return result

    jump_service.os.rename = hooked_os_rename


def _install_delete_hooks(crash_point: str) -> None:
    """Hooks for the delete_jump crash points (TEST-1).

    delete_jump's write order:
      step 1: soft_delete(folder, logbook_root)  # folder → .trash/
      step 2: result.conn.execute("DELETE FROM jumps ...")

    The harness patches ``soft_delete`` so the kill fires on its
    return — before any code path can reach the SQL DELETE.
    """
    from backend.services import jump_service

    orig_soft_delete = jump_service.soft_delete

    def hooked_soft_delete(folder, logbook_root, subdir=None):
        result = orig_soft_delete(folder, logbook_root, subdir)
        if crash_point == "delete_after_trash_move":
            _suicide()
        return result

    jump_service.soft_delete = hooked_soft_delete


def _install_dropzone_create_hooks(crash_point: str) -> None:
    """Hooks for ``create_dropzone`` (TEST-2 — audit 2026-04-29).

    create_dropzone's write order:
      step 1: _write_dropzone — XSD-validate, then atomic_write
              the dropzones/<uuid>.xml file.
      step 2: _upsert_index_row — SQLite INSERT OR REPLACE.

    The harness patches ``atomic_write`` so the kill fires on its
    return — disk holds the authoritative XML; the index has not yet
    been written. ``reindex_from_xml`` repopulates the row on next
    launch (D3).
    """
    from backend.services import dropzone_service
    from backend.storage import filesystem

    orig_aw = filesystem.atomic_write

    def hooked_atomic_write(path, data):
        path = Path(path)
        result = orig_aw(path, data)
        if (
            crash_point == "dropzone_create_after_xml_write"
            and path.suffix == ".xml"
            and path.parent.name == "dropzones"
        ):
            _suicide()
        return result

    filesystem.atomic_write = hooked_atomic_write
    dropzone_service.atomic_write = hooked_atomic_write


def _install_dropzone_update_hooks(crash_point: str) -> None:
    """Hooks for ``update_dropzone`` (TEST-2 — audit 2026-04-29).

    update_dropzone's write order mirrors create's: atomic_write the
    rewritten XML, then INSERT OR REPLACE the index row. Same crash
    point shape — the kill fires after the XML write returns,
    before the index is updated. The index is stale until the next
    reindex.
    """
    from backend.services import dropzone_service
    from backend.storage import filesystem

    orig_aw = filesystem.atomic_write

    def hooked_atomic_write(path, data):
        path = Path(path)
        result = orig_aw(path, data)
        if (
            crash_point == "dropzone_update_after_xml_write"
            and path.suffix == ".xml"
            and path.parent.name == "dropzones"
        ):
            _suicide()
        return result

    filesystem.atomic_write = hooked_atomic_write
    dropzone_service.atomic_write = hooked_atomic_write


def _install_dropzone_delete_hooks(crash_point: str) -> None:
    """Hooks for ``delete_dropzone`` (TEST-2 — audit 2026-04-29).

    delete_dropzone's write order:
      step 1: soft_delete_file — moves dropzones/<uuid>.xml to
              .trash/dropzones/<ts>_<uuid>.xml.
      step 2: _delete_index_row — SQLite DELETE.

    The harness patches ``soft_delete_file`` so the kill fires on its
    return — the active file is gone; the index still references the
    trashed UUID. Next reindex notices the missing file and removes
    the row.
    """
    from backend.services import dropzone_service

    orig_soft_delete_file = dropzone_service.soft_delete_file

    def hooked_soft_delete_file(path, logbook_root, subdir):
        result = orig_soft_delete_file(path, logbook_root, subdir)
        if crash_point == "dropzone_delete_after_trash_move":
            _suicide()
        return result

    dropzone_service.soft_delete_file = hooked_soft_delete_file


def _install_hooks(operation: str, crash_point: str) -> None:
    """Dispatch to the per-operation hook installer."""
    if operation == "create":
        _install_create_hooks(crash_point)
    elif operation == "update":
        _install_update_hooks(crash_point)
    elif operation == "delete":
        _install_delete_hooks(crash_point)
    elif operation == "dropzone-create":
        _install_dropzone_create_hooks(crash_point)
    elif operation == "dropzone-update":
        _install_dropzone_update_hooks(crash_point)
    elif operation == "dropzone-delete":
        _install_dropzone_delete_hooks(crash_point)
    else:
        raise ValueError(f"unknown operation {operation!r}")


def _run_create(
    logbook_root: Path,
    payload_dict: dict,
    uploads_spec: list[dict],
) -> None:
    from backend.models.jump import JumpCreate
    from backend.services.jump_service import Upload, create_jump

    uploads = [
        Upload(
            filename=u["filename"],
            content_type=u.get("content_type"),
            chunks=[bytes.fromhex(u["hex"])],
        )
        for u in uploads_spec
    ]
    payload = JumpCreate(**payload_dict)
    create_jump(logbook_root, "default", payload, uploads=uploads)


def _run_update(
    logbook_root: Path, jump_id_str: str, payload_dict: dict
) -> None:
    from uuid import UUID

    from backend.models.jump import JumpUpdate
    from backend.services.jump_service import update_jump

    payload = JumpUpdate(**payload_dict)
    update_jump(logbook_root, "default", UUID(jump_id_str), payload)


def _run_delete(logbook_root: Path, jump_id_str: str) -> None:
    from uuid import UUID

    from backend.services.jump_service import delete_jump

    delete_jump(logbook_root, "default", UUID(jump_id_str))


def _run_dropzone_create(logbook_root: Path, payload_dict: dict) -> None:
    from backend.models.dropzone import DropzoneCreate
    from backend.services.dropzone_service import create_dropzone

    payload = DropzoneCreate(**payload_dict)
    create_dropzone(logbook_root, "default", payload)


def _run_dropzone_update(
    logbook_root: Path, dropzone_id_str: str, payload_dict: dict
) -> None:
    from uuid import UUID

    from backend.models.dropzone import DropzoneUpdate
    from backend.services.dropzone_service import update_dropzone

    payload = DropzoneUpdate(**payload_dict)
    update_dropzone(
        logbook_root, "default", UUID(dropzone_id_str), payload
    )


def _run_dropzone_delete(
    logbook_root: Path, dropzone_id_str: str
) -> None:
    from uuid import UUID

    from backend.services.dropzone_service import delete_dropzone

    delete_dropzone(logbook_root, "default", UUID(dropzone_id_str))


def main() -> int:
    operation = os.environ.get("OPERATION", "create")
    crash_point = os.environ["CRASH_POINT"]
    logbook_root = Path(os.environ["LOGBOOK_ROOT"])

    # Imports go inside the dispatcher branches, not at module top —
    # we want hooks installed BEFORE anything grabs a reference to the
    # real primitives.
    _install_hooks(operation, crash_point)

    if operation == "create":
        payload_dict = json.loads(os.environ["JUMP_PAYLOAD"])
        uploads_spec = json.loads(os.environ.get("UPLOADS", "[]"))
        _run_create(logbook_root, payload_dict, uploads_spec)
    elif operation == "update":
        payload_dict = json.loads(os.environ["JUMP_PAYLOAD"])
        _run_update(logbook_root, os.environ["JUMP_ID"], payload_dict)
    elif operation == "delete":
        _run_delete(logbook_root, os.environ["JUMP_ID"])
    elif operation == "dropzone-create":
        payload_dict = json.loads(os.environ["DROPZONE_PAYLOAD"])
        _run_dropzone_create(logbook_root, payload_dict)
    elif operation == "dropzone-update":
        payload_dict = json.loads(os.environ["DROPZONE_PAYLOAD"])
        _run_dropzone_update(
            logbook_root, os.environ["DROPZONE_ID"], payload_dict
        )
    elif operation == "dropzone-delete":
        _run_dropzone_delete(logbook_root, os.environ["DROPZONE_ID"])
    else:
        raise ValueError(f"unknown operation {operation!r}")

    # If the hook fires, the dispatched call never returns — the
    # process dies inside the hooked function. If we reach here, no
    # kill happened and the test will fail on the returncode assertion.
    return 0


if __name__ == "__main__":
    sys.exit(main())
