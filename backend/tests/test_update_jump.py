"""Tests for the Phase 3.5 ``update_jump`` service-layer flow (D31).

v0.1 scope pinned by D31: metadata-only edits. Attachments and
``id`` are preserved from the on-disk jump; the attachment-edit
phase ships later with its own transport D-entry.

D-entries exercised here:

  * D4: title / jump_number change triggers a folder rename.
    Manual folder renames (not tested here — they go through the
    filesystem directly and never touch this service) keep the
    signature safe; API edits rewrite jump.xml.
  * D23: changing ``jump_number`` to one already used by a
    different jump surfaces as ``JumpNumberConflict`` (409).
    Unchanged ``jump_number`` skips the scan.
  * D25-adjacent: the write order mirrors create's XML-is-truth
    story — ``jump.xml`` is rewritten at the CURRENT folder path
    BEFORE the folder rename so a crash mid-update leaves the
    folder's jump.xml authoritative regardless of the folder name.
  * D27: ``jump_updated`` INFO log on success, with
    ``folder_renamed`` set when applicable.

Out of scope (covered elsewhere or deferred):

  * Attachment editing via PUT (deferred, D31).
  * Crash-path subprocess harness for update_jump (lands with
    attachment-edit phase; the metadata-only flow has no
    orphan-delete step to crash on).
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest

from backend.api.errors import (
    JumpNumberConflict,
    NotFoundError,
    ValidationFailedError,
)
from backend.models.jump import JumpCreate, JumpUpdate
from backend.services.jump_service import (
    Upload,
    create_jump,
    get_jump,
    update_jump,
)
from backend.storage.bootstrap import bootstrap_logbook
from backend.storage.index import open_index
from backend.storage.manifest import MANIFEST_NAME, from_jump_xml

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

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


def _update_body(**overrides) -> JumpUpdate:
    """Same field defaults as ``_minimal_create`` but for a JumpUpdate.

    PUT is a full-replace so every metadata field appears; callers
    override only the fields they want changed.
    """
    data = dict(
        jump_number=1,
        date=date(2026, 4, 22),
        dropzone="Skydive Elsinore",
        exit_altitude_m=4000,
        deployment_altitude_m=900,
    )
    data.update(overrides)
    return JumpUpdate(**data)


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #

class TestHappyPath:
    def test_simple_metadata_edit(self, bootstrapped_root: Path):
        created = create_jump(
            bootstrapped_root, "default", _minimal_create(title="Old")
        )
        updated = update_jump(
            bootstrapped_root,
            "default",
            created.id,
            _update_body(title="Old", notes="Added a note after the fact."),
        )
        # id preserved (D4 stability invariant).
        assert updated.id == created.id
        # Edited field reflects the new value.
        assert updated.notes == "Added a note after the fact."
        # Persisted: re-read via get_jump confirms on-disk state.
        fresh = get_jump(bootstrapped_root, "default", created.id)
        assert fresh.notes == "Added a note after the fact."

    def test_preserves_attachments(self, bootstrapped_root: Path):
        # D31: attachments are NOT editable via PUT in v0.1. Whatever
        # is on disk must survive a metadata edit unchanged.
        created = create_jump(
            bootstrapped_root,
            "default",
            _minimal_create(),
            uploads=[
                Upload(filename="a.txt", content_type="text/plain", chunks=[b"A"]),
                Upload(filename="b.txt", content_type="text/plain", chunks=[b"B"]),
            ],
        )
        updated = update_jump(
            bootstrapped_root,
            "default",
            created.id,
            _update_body(notes="edit"),
        )
        assert updated.attachments == created.attachments
        # Files still on disk, untouched.
        folder = bootstrapped_root / "jumps" / "[1]"
        assert (folder / "a.txt").read_bytes() == b"A"
        assert (folder / "b.txt").read_bytes() == b"B"

    def test_title_change_renames_folder(self, bootstrapped_root: Path):
        created = create_jump(
            bootstrapped_root, "default", _minimal_create(title="Old title")
        )
        old_folder = bootstrapped_root / "jumps" / "[1] Old title"
        assert old_folder.is_dir()

        update_jump(
            bootstrapped_root,
            "default",
            created.id,
            _update_body(title="New title"),
        )
        new_folder = bootstrapped_root / "jumps" / "[1] New title"
        assert new_folder.is_dir()
        # Old path is gone (os.rename, not copy).
        assert not old_folder.exists()

    def test_title_add_renames_folder(self, bootstrapped_root: Path):
        # Bare [<N>] gains a title → folder becomes [<N>] title.
        created = create_jump(bootstrapped_root, "default", _minimal_create())
        assert (bootstrapped_root / "jumps" / "[1]").is_dir()
        update_jump(
            bootstrapped_root,
            "default",
            created.id,
            _update_body(title="Added later"),
        )
        assert (bootstrapped_root / "jumps" / "[1] Added later").is_dir()
        assert not (bootstrapped_root / "jumps" / "[1]").exists()

    def test_title_remove_renames_folder(self, bootstrapped_root: Path):
        created = create_jump(
            bootstrapped_root, "default", _minimal_create(title="Temporary")
        )
        update_jump(
            bootstrapped_root,
            "default",
            created.id,
            _update_body(title=None),
        )
        assert (bootstrapped_root / "jumps" / "[1]").is_dir()
        assert not (bootstrapped_root / "jumps" / "[1] Temporary").exists()

    def test_jump_number_change_renames_folder(self, bootstrapped_root: Path):
        created = create_jump(
            bootstrapped_root, "default", _minimal_create(jump_number=1, title="Same")
        )
        update_jump(
            bootstrapped_root,
            "default",
            created.id,
            _update_body(jump_number=42, title="Same"),
        )
        assert (bootstrapped_root / "jumps" / "[42] Same").is_dir()
        assert not (bootstrapped_root / "jumps" / "[1] Same").exists()

    def test_no_change_is_a_noop_folder_wise(self, bootstrapped_root: Path):
        # Updating with identical metadata rewrites jump.xml (which is
        # fine — same bytes, just a new write). The folder is NOT
        # renamed, and no phantom folder is left behind.
        created = create_jump(
            bootstrapped_root, "default", _minimal_create(title="Stable")
        )
        folder = bootstrapped_root / "jumps" / "[1] Stable"
        before_names = sorted(p.name for p in (bootstrapped_root / "jumps").iterdir())
        update_jump(
            bootstrapped_root,
            "default",
            created.id,
            _update_body(title="Stable"),
        )
        after_names = sorted(p.name for p in (bootstrapped_root / "jumps").iterdir())
        assert before_names == after_names
        assert folder.is_dir()

    def test_index_row_updated(self, bootstrapped_root: Path):
        created = create_jump(
            bootstrapped_root,
            "default",
            _minimal_create(jump_number=1, title="Pre"),
        )
        update_jump(
            bootstrapped_root,
            "default",
            created.id,
            _update_body(jump_number=99, title="Post"),
        )
        result = open_index(bootstrapped_root)
        try:
            row = result.conn.execute(
                "SELECT jump_number, title, folder, created_at, updated_at "
                "FROM jumps WHERE id = ?",
                (str(created.id),),
            ).fetchone()
        finally:
            result.conn.close()
        assert row["jump_number"] == 99
        assert row["title"] == "Post"
        assert row["folder"] == "jumps/[99] Post"
        # updated_at bumped, created_at preserved. A regression that
        # overwrote created_at would fail this.
        assert row["updated_at"] != row["created_at"]

    def test_manifest_matches_from_jump_xml(self, bootstrapped_root: Path):
        # D25-adjacent: after update, SHA256SUMS equals what
        # from_jump_xml would produce. Keeps the first read after
        # update from triggering an unnecessary folder_reconcile
        # rewrite.
        created = create_jump(
            bootstrapped_root, "default", _minimal_create(title="A")
        )
        update_jump(
            bootstrapped_root,
            "default",
            created.id,
            _update_body(title="B"),
        )
        folder = bootstrapped_root / "jumps" / "[1] B"
        on_disk = (folder / MANIFEST_NAME).read_bytes()
        recomputed = from_jump_xml(folder, logbook_root=bootstrapped_root)
        assert on_disk == recomputed

    def test_emits_jump_updated_log_with_rename_flag(
        self, bootstrapped_root: Path, caplog
    ):
        caplog.set_level(logging.INFO, logger="backend.services.jump")
        created = create_jump(
            bootstrapped_root, "default", _minimal_create(title="X")
        )
        update_jump(
            bootstrapped_root,
            "default",
            created.id,
            _update_body(title="Y"),
        )
        records = [r for r in caplog.records if r.message == "jump_updated"]
        assert len(records) == 1
        assert records[0].folder_renamed is True

    def test_no_rename_flag_when_name_unchanged(
        self, bootstrapped_root: Path, caplog
    ):
        caplog.set_level(logging.INFO, logger="backend.services.jump")
        created = create_jump(
            bootstrapped_root, "default", _minimal_create(title="Same")
        )
        update_jump(
            bootstrapped_root,
            "default",
            created.id,
            _update_body(title="Same", notes="edit"),
        )
        records = [r for r in caplog.records if r.message == "jump_updated"]
        assert records[0].folder_renamed is False


# --------------------------------------------------------------------------- #
# Not-found + validation
# --------------------------------------------------------------------------- #

class TestNotFound:
    def test_unknown_id_raises_not_found(self, bootstrapped_root: Path):
        with pytest.raises(NotFoundError):
            update_jump(
                bootstrapped_root,
                "default",
                UUID("00000000-0000-4000-8000-000000000000"),
                _update_body(),
            )

    def test_other_users_jump_is_not_found(self, bootstrapped_root: Path):
        # User isolation at the service layer: alice updating bob's
        # jump sees 404, not 403 — matches D8's "user_id scoped"
        # invariant and avoids existence leaks.
        created = create_jump(bootstrapped_root, "bob", _minimal_create())
        with pytest.raises(NotFoundError):
            update_jump(
                bootstrapped_root, "alice", created.id, _update_body()
            )


class TestValidationErrors:
    def test_bad_title_chars_raises_422(self, bootstrapped_root: Path):
        # D4: title with forbidden char produces an invalid folder
        # name. Service translates to ValidationFailedError.
        created = create_jump(bootstrapped_root, "default", _minimal_create())
        with pytest.raises(ValidationFailedError):
            update_jump(
                bootstrapped_root,
                "default",
                created.id,
                _update_body(title="bad/title"),
            )
        # No folder renamed, no XML rewritten — the 422 happens
        # before any write.
        fresh = get_jump(bootstrapped_root, "default", created.id)
        assert fresh.title is None


# --------------------------------------------------------------------------- #
# D23 jump_number collision
# --------------------------------------------------------------------------- #

class TestJumpNumberCollision:
    def test_change_to_existing_number_raises_409(
        self, bootstrapped_root: Path
    ):
        j1 = create_jump(
            bootstrapped_root, "default", _minimal_create(jump_number=1, title="A")
        )
        create_jump(
            bootstrapped_root, "default", _minimal_create(jump_number=2, title="B")
        )

        with pytest.raises(JumpNumberConflict) as exc_info:
            update_jump(
                bootstrapped_root,
                "default",
                j1.id,
                _update_body(jump_number=2, title="A"),
            )
        assert exc_info.value.code == "jump_number_conflict"
        assert exc_info.value.http_status == 409

        # Original state preserved — no half-applied edit.
        fresh = get_jump(bootstrapped_root, "default", j1.id)
        assert fresh.jump_number == 1
        assert (bootstrapped_root / "jumps" / "[1] A").is_dir()
        assert (bootstrapped_root / "jumps" / "[2] B").is_dir()

    def test_same_number_is_allowed(self, bootstrapped_root: Path):
        # Keeping jump_number unchanged skips the prefix scan
        # entirely — if we re-scanned, the OWN folder would look
        # like a collision and break every metadata edit.
        created = create_jump(
            bootstrapped_root,
            "default",
            _minimal_create(jump_number=42, title="A"),
        )
        update_jump(
            bootstrapped_root,
            "default",
            created.id,
            _update_body(jump_number=42, title="A", notes="edit only notes"),
        )
        # No exception — and the notes landed.
        fresh = get_jump(bootstrapped_root, "default", created.id)
        assert fresh.notes == "edit only notes"


# --------------------------------------------------------------------------- #
# Verify runs clean after update
# --------------------------------------------------------------------------- #

class TestVerifyCompatibility:
    def test_verify_clean_after_title_change(self, bootstrapped_root: Path):
        from backend.storage.verify import verify_logbook

        created = create_jump(
            bootstrapped_root, "default", _minimal_create(title="Pre")
        )
        update_jump(
            bootstrapped_root, "default", created.id, _update_body(title="Post")
        )
        report = verify_logbook(bootstrapped_root)
        assert report.clean, f"expected clean, got: {report.issues}"

    def test_verify_clean_with_attachments_after_update(
        self, bootstrapped_root: Path
    ):
        from backend.storage.verify import verify_logbook

        created = create_jump(
            bootstrapped_root,
            "default",
            _minimal_create(title="With attachments"),
            uploads=[
                Upload(filename="note.txt", content_type="text/plain", chunks=[b"hi"])
            ],
        )
        # Verify the hashes still match after metadata-only edit.
        update_jump(
            bootstrapped_root, "default", created.id, _update_body(
                title="Renamed", notes="tweaked"
            )
        )
        report = verify_logbook(bootstrapped_root)
        assert report.clean, f"expected clean, got: {report.issues}"
