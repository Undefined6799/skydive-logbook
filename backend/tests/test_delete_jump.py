"""Tests for the Phase 3.5 ``delete_jump`` service-layer flow (D19, D31).

Soft delete: the jump folder moves to ``.trash/<timestamp>_<name>/``
and the index row is removed. Restoration is a manual
``mv + reindex`` (v0.1 — no Restore UI yet, D19).

D-entries exercised:

  * D19: destination is ``.trash/<timestamp>_<original-name>/``.
    ``soft_delete`` is already unit-tested in ``test_trash.py``;
    this file exercises the service-layer integration (index row
    removal, user isolation, 404 on missing).
  * D23: after delete, the jump_number is free to reuse — trashed
    folders are outside the uniqueness namespace.
  * D27: ``jump_deleted`` INFO log on success with the new trash
    path.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest

from backend.api.errors import NotFoundError
from backend.models.jump import JumpCreate
from backend.services.jump_service import (
    create_jump,
    delete_jump,
    get_jump,
    list_jumps,
)
from backend.storage.bootstrap import bootstrap_logbook
from backend.storage.index import open_index


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


class TestHappyPath:
    def test_folder_moves_to_trash(self, bootstrapped_root: Path):
        created = create_jump(
            bootstrapped_root, "default", _minimal_create(title="Goodbye")
        )
        folder = bootstrapped_root / "jumps" / "[1] Goodbye"
        assert folder.is_dir()

        delete_jump(bootstrapped_root, "default", created.id)

        assert not folder.exists()
        # .trash/ now contains one timestamped entry matching the
        # D19 naming scheme. We don't pin the exact timestamp
        # because it's wall-clock; we assert the shape.
        trash_entries = list((bootstrapped_root / ".trash").iterdir())
        assert len(trash_entries) == 1
        assert trash_entries[0].name.endswith("_[1] Goodbye")

    def test_get_returns_not_found_after_delete(
        self, bootstrapped_root: Path
    ):
        created = create_jump(bootstrapped_root, "default", _minimal_create())
        delete_jump(bootstrapped_root, "default", created.id)
        with pytest.raises(NotFoundError):
            get_jump(bootstrapped_root, "default", created.id)

    def test_list_excludes_deleted(self, bootstrapped_root: Path):
        c1 = create_jump(
            bootstrapped_root,
            "default",
            _minimal_create(jump_number=1, title="Keep"),
        )
        c2 = create_jump(
            bootstrapped_root,
            "default",
            _minimal_create(jump_number=2, title="Drop"),
        )
        delete_jump(bootstrapped_root, "default", c2.id)

        listed = list_jumps(bootstrapped_root, "default")
        ids = {s.id for s in listed}
        assert c1.id in ids
        assert c2.id not in ids

    def test_index_row_removed(self, bootstrapped_root: Path):
        created = create_jump(bootstrapped_root, "default", _minimal_create())
        delete_jump(bootstrapped_root, "default", created.id)

        result = open_index(bootstrapped_root)
        try:
            row = result.conn.execute(
                "SELECT id FROM jumps WHERE id = ?", (str(created.id),)
            ).fetchone()
        finally:
            result.conn.close()
        assert row is None

    def test_jump_number_reusable_after_delete(
        self, bootstrapped_root: Path
    ):
        # D23 uniqueness is about ACTIVE jumps — trashed folders are
        # not part of the active namespace. A fresh jump can claim
        # the freed number without a collision.
        c1 = create_jump(
            bootstrapped_root, "default", _minimal_create(jump_number=1, title="Old")
        )
        delete_jump(bootstrapped_root, "default", c1.id)

        c2 = create_jump(
            bootstrapped_root,
            "default",
            _minimal_create(jump_number=1, title="Fresh"),
        )
        assert c2.jump_number == 1
        # New folder exists under [1] — the trashed one has a
        # different name (timestamp-prefixed), so no collision.
        assert (bootstrapped_root / "jumps" / "[1] Fresh").is_dir()

    def test_emits_jump_deleted_log(self, bootstrapped_root: Path, caplog):
        caplog.set_level(logging.INFO, logger="backend.services.jump")
        created = create_jump(
            bootstrapped_root, "default", _minimal_create(title="Log me")
        )
        delete_jump(bootstrapped_root, "default", created.id)
        records = [r for r in caplog.records if r.message == "jump_deleted"]
        assert len(records) == 1
        record = records[0]
        assert record.levelname == "INFO"
        assert record.jump_id == str(created.id)
        assert record.folder == "jumps/[1] Log me"
        # trashed_to path is relative; starts with .trash/ and ends
        # with the original folder name.
        assert record.trashed_to.startswith(".trash/")
        assert record.trashed_to.endswith("_[1] Log me")


class TestNotFound:
    def test_unknown_id_raises(self, bootstrapped_root: Path):
        with pytest.raises(NotFoundError):
            delete_jump(
                bootstrapped_root,
                "default",
                UUID("00000000-0000-4000-8000-000000000000"),
            )

    def test_wrong_user_raises(self, bootstrapped_root: Path):
        # User isolation: alice cannot delete bob's jump even by id.
        created = create_jump(bootstrapped_root, "bob", _minimal_create())
        with pytest.raises(NotFoundError):
            delete_jump(bootstrapped_root, "alice", created.id)
        # And bob's jump is still there.
        assert (bootstrapped_root / "jumps" / "[1]").is_dir()


class TestVerifyCompatibility:
    def test_verify_walks_trash_after_delete(
        self, bootstrapped_root: Path
    ):
        # D19: ``.trash/`` is IN verify's scope (per-folder integrity
        # still applies) but OUT of the duplicate-number namespace.
        # A deleted-then-recreated jump_number pair must NOT trip
        # the duplicate check.
        from backend.storage.verify import verify_logbook

        c1 = create_jump(
            bootstrapped_root, "default", _minimal_create(jump_number=1, title="v1")
        )
        delete_jump(bootstrapped_root, "default", c1.id)
        create_jump(
            bootstrapped_root,
            "default",
            _minimal_create(jump_number=1, title="v2"),
        )
        report = verify_logbook(bootstrapped_root)
        assert report.clean, f"expected clean, got: {report.issues}"
