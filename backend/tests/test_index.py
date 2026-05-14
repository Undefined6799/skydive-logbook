"""Tests for the D26 index-schema-versioning contract.

``open_index`` is the single enforcement point for the index's on-disk
shape. These tests lock down the three-branch logic (fresh / current /
mismatch), the connection-level PRAGMAs that must be set on every
open, and the completeness of the drop-and-reindex loop — specifically
that a stale table from a prior schema does not survive a rebuild.
"""
from __future__ import annotations

import sqlite3

import pytest

from backend.storage.index import (
    INDEX_FILENAME,
    INDEX_SCHEMA_VERSION,
    IndexOpenResult,
    open_index,
)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _user_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row["name"] for row in rows}


# --------------------------------------------------------------------------- #
# Branch 1: fresh DB
# --------------------------------------------------------------------------- #

class TestFreshDatabase:
    def test_returns_fresh_result(self, tmp_path):
        result = open_index(tmp_path)
        try:
            assert isinstance(result, IndexOpenResult)
            # Fresh is deliberately NOT reported as a rebuild (D26):
            # a new logbook has no XML to reindex from.
            assert result.schema_was_rebuilt is False
            assert result.previous_version == 0
        finally:
            result.conn.close()

    def test_stamps_user_version(self, tmp_path):
        result = open_index(tmp_path)
        try:
            assert _user_version(result.conn) == INDEX_SCHEMA_VERSION
        finally:
            result.conn.close()

    def test_creates_expected_tables(self, tmp_path):
        result = open_index(tmp_path)
        try:
            names = _table_names(result.conn)
            assert {"jumps", "dropzones"} <= names
        finally:
            result.conn.close()

    def test_creates_index_file_on_disk(self, tmp_path):
        # Side effect: the SQLite file appears at the canonical path.
        result = open_index(tmp_path)
        try:
            assert (tmp_path / INDEX_FILENAME).is_file()
        finally:
            result.conn.close()


# --------------------------------------------------------------------------- #
# Branch 2: already current
# --------------------------------------------------------------------------- #

class TestAlreadyCurrent:
    def test_reopen_is_noop_rebuild_false(self, tmp_path):
        first = open_index(tmp_path)
        first.conn.close()

        second = open_index(tmp_path)
        try:
            assert second.schema_was_rebuilt is False
            assert second.previous_version == INDEX_SCHEMA_VERSION
        finally:
            second.conn.close()

    def test_reopen_preserves_data(self, tmp_path):
        # Write a row through the first connection, reopen, assert the
        # row is still there. A "same version" reopen must not drop.
        first = open_index(tmp_path)
        try:
            first.conn.execute(
                "INSERT INTO jumps "
                "(id, user_id, jump_number, date, dropzone, folder, "
                " schema_ns, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "00000000-0000-4000-8000-000000000001",
                    "default",
                    1,
                    "2026-04-22",
                    "Skydive Elsinore",
                    "jumps/[1] 2026-04-22",
                    "https://skydive-logbook.org/schema/v1",
                    "2026-04-22T00:00:00.000Z",
                    "2026-04-22T00:00:00.000Z",
                ),
            )
        finally:
            first.conn.close()

        second = open_index(tmp_path)
        try:
            count = second.conn.execute(
                "SELECT COUNT(*) AS n FROM jumps"
            ).fetchone()["n"]
            assert count == 1
        finally:
            second.conn.close()


# --------------------------------------------------------------------------- #
# Branch 3: version mismatch triggers drop + rebuild
# --------------------------------------------------------------------------- #

class TestVersionMismatchRebuilds:
    def test_older_version_drops_and_rebuilds(self, tmp_path):
        # Write some data, then forge PRAGMA user_version to look older.
        first = open_index(tmp_path)
        try:
            first.conn.execute(
                "INSERT INTO jumps "
                "(id, user_id, jump_number, date, dropzone, folder, "
                " schema_ns, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "00000000-0000-4000-8000-000000000002",
                    "default",
                    42,
                    "2026-04-22",
                    "DZ",
                    "jumps/[42] 2026-04-22",
                    "https://skydive-logbook.org/schema/v1",
                    "2026-04-22T00:00:00.000Z",
                    "2026-04-22T00:00:00.000Z",
                ),
            )
            # Simulate a prior schema version by stamping a fake value.
            # Choice is ``INDEX_SCHEMA_VERSION + 100`` simulated as older
            # would be awkward — instead we use ``INDEX_SCHEMA_VERSION - 1``
            # when it's positive; fall back to a sentinel if the current
            # version is ever 0 (it isn't today).
            older = max(INDEX_SCHEMA_VERSION - 1, 0)
            # ``0`` would be interpreted as fresh (branch 1), so we must
            # use a nonzero-but-different value to exercise branch 3.
            # If INDEX_SCHEMA_VERSION ever falls to 1, use 99 as "an
            # unrelated legacy version" — still triggers the mismatch
            # branch per D26.
            stale = 99 if older == 0 else older
            first.conn.execute(f"PRAGMA user_version = {stale}")
        finally:
            first.conn.close()

        second = open_index(tmp_path)
        try:
            assert second.schema_was_rebuilt is True
            assert second.previous_version == stale
            # User data is gone (tables were dropped).
            count = second.conn.execute(
                "SELECT COUNT(*) AS n FROM jumps"
            ).fetchone()["n"]
            assert count == 0
            # Version restamped to current.
            assert _user_version(second.conn) == INDEX_SCHEMA_VERSION
        finally:
            second.conn.close()

    def test_newer_version_also_drops_and_rebuilds(self, tmp_path):
        # Downgrade scenario per D26: a user flips back from a future
        # app to the current one. The higher user_version on disk is
        # still a mismatch and must drop+rebuild to the current schema.
        first = open_index(tmp_path)
        try:
            first.conn.execute(
                f"PRAGMA user_version = {INDEX_SCHEMA_VERSION + 1}"
            )
        finally:
            first.conn.close()

        second = open_index(tmp_path)
        try:
            assert second.schema_was_rebuilt is True
            assert second.previous_version == INDEX_SCHEMA_VERSION + 1
            assert _user_version(second.conn) == INDEX_SCHEMA_VERSION
        finally:
            second.conn.close()

    def test_legacy_table_is_dropped(self, tmp_path):
        # Guards the completeness of the drop loop. A stale table from
        # a prior schema (e.g. a renamed-away table) must not survive
        # a rebuild. Without dynamic enumeration via sqlite_master, a
        # naive "drop the tables I know about" loop would leak it.
        first = open_index(tmp_path)
        try:
            first.conn.execute("CREATE TABLE legacy_obsolete (x TEXT)")
            first.conn.execute("PRAGMA user_version = 99")
        finally:
            first.conn.close()

        second = open_index(tmp_path)
        try:
            assert second.schema_was_rebuilt is True
            assert "legacy_obsolete" not in _table_names(second.conn)
            # And current tables are back.
            assert {"jumps", "dropzones"} <= _table_names(second.conn)
        finally:
            second.conn.close()


# --------------------------------------------------------------------------- #
# Connection PRAGMAs
# --------------------------------------------------------------------------- #

class TestConnectionPragmas:
    def test_wal_mode_is_enabled(self, tmp_path):
        result = open_index(tmp_path)
        try:
            mode = result.conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal"
        finally:
            result.conn.close()

    def test_foreign_keys_are_enabled(self, tmp_path):
        result = open_index(tmp_path)
        try:
            fk = result.conn.execute("PRAGMA foreign_keys").fetchone()[0]
            assert fk == 1
        finally:
            result.conn.close()


# --------------------------------------------------------------------------- #
# D23: UNIQUE(user_id, jump_number) — introduced at schema v2
# --------------------------------------------------------------------------- #

# Boilerplate for the jumps table's non-null columns. Tests that want to
# insert varying (user_id, jump_number) pairs can keep the rest minimal.
_INSERT_JUMP = (
    "INSERT INTO jumps (id, user_id, jump_number, date, dropzone, folder, "
    "schema_ns, created_at, updated_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_FIXED_COLS = (
    "2026-01-01",
    "DZ",
    "jumps/_",
    "https://skydive-logbook.org/schema/v1",
    "2026-01-01T00:00:00.000Z",
    "2026-01-01T00:00:00.000Z",
)


class TestUniquenessConstraint:
    def test_duplicate_user_jump_number_raises(self, tmp_path):
        # The load-bearing invariant of D23: a user cannot have two
        # jumps with the same jump_number. The service layer does an
        # early 409 check (future slice) but the index constraint is
        # the guarantee that holds even if the service check is buggy,
        # the index was just rebuilt, or another process raced us.
        result = open_index(tmp_path)
        try:
            result.conn.execute(
                _INSERT_JUMP,
                ("id-1", "default", 42, *_FIXED_COLS),
            )
            with pytest.raises(sqlite3.IntegrityError):
                result.conn.execute(
                    _INSERT_JUMP,
                    ("id-2", "default", 42, *_FIXED_COLS),
                )
        finally:
            result.conn.close()

    def test_same_jump_number_different_users_is_allowed(self, tmp_path):
        # D8 + D23 compound form: uniqueness is per (user_id,
        # jump_number), not per jump_number alone. Locking this down
        # now means the multi-user rollout (deferred) does not need a
        # migration to widen the constraint.
        result = open_index(tmp_path)
        try:
            result.conn.execute(
                _INSERT_JUMP,
                ("id-1", "alice", 42, *_FIXED_COLS),
            )
            result.conn.execute(
                _INSERT_JUMP,
                ("id-2", "bob", 42, *_FIXED_COLS),
            )
            count = result.conn.execute(
                "SELECT COUNT(*) AS n FROM jumps"
            ).fetchone()["n"]
            assert count == 2
        finally:
            result.conn.close()

    def test_v1_to_v2_rebuild_enforces_uniqueness(self, tmp_path):
        # D26's consequences section explicitly asks for this test:
        # an index at v1 opens, drops, rebuilds to v2. The
        # "reindex from XML" portion of D26's ask is deferred until
        # reindex_from_xml lands; what we verify today is that the
        # rebuild produces a v2-shaped schema — the UNIQUE constraint
        # is in place and a duplicate insert fails.

        # Step 1: build a fresh index (currently v2) and stamp it back
        # to v1. The resulting SQLite file on disk looks like what an
        # older build of the app would have left.
        first = open_index(tmp_path)
        try:
            first.conn.execute("PRAGMA user_version = 1")
        finally:
            first.conn.close()

        # Step 2: reopen. open_index sees user_version=1 ≠ current,
        # drops and rebuilds.
        second = open_index(tmp_path)
        try:
            assert second.schema_was_rebuilt is True
            assert second.previous_version == 1

            # Step 3: the newly-rebuilt schema must enforce D23.
            second.conn.execute(
                _INSERT_JUMP,
                ("id-1", "default", 100, *_FIXED_COLS),
            )
            with pytest.raises(sqlite3.IntegrityError):
                second.conn.execute(
                    _INSERT_JUMP,
                    ("id-2", "default", 100, *_FIXED_COLS),
                )
        finally:
            second.conn.close()
