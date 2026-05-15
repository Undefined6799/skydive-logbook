"""Schema-version refusal semantics for the SQLite index (D26 amendment).

`open_index` had a "drop and reinstall in either direction" branch that
silently downgraded the on-disk schema when it was newer than the
running build. Per the 2026-05-15 audit §2.1, that path lost columns
the build couldn't repopulate from XML on the next reindex, so the
build's user_version got stamped over a *newer* logbook without any
warning. The current contract is: refuse to start with
:class:`IndexSchemaTooNewError`; the user upgrades the app or deletes
the index file consciously.

The older-on-disk case is still a routine drop-and-reindex (Branch 3b)
and stays tested via the existing reindex suite.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.storage.index import (
    INDEX_FILENAME,
    INDEX_SCHEMA_VERSION,
    IndexSchemaTooNewError,
    open_index,
)


def _stamp_user_version(path: Path, version: int) -> None:
    """Open the SQLite index file directly and set ``user_version``.

    Used by these tests to plant a target ``previous_version`` without
    going through the full schema-install path.
    """
    conn = sqlite3.connect(str(path), isolation_level=None)
    try:
        conn.execute(f"PRAGMA user_version = {int(version)}")
    finally:
        conn.close()


def test_open_index_refuses_when_on_disk_version_is_newer(logbook_root: Path) -> None:
    """A logbook stamped at a future schema version must refuse to open."""
    # First open creates the file at the current version.
    result = open_index(logbook_root)
    result.conn.close()
    index_path = logbook_root / INDEX_FILENAME

    # Stamp a future version directly on the file.
    _stamp_user_version(index_path, INDEX_SCHEMA_VERSION + 1)

    # Next open must refuse, not silently drop and downgrade.
    with pytest.raises(IndexSchemaTooNewError) as excinfo:
        open_index(logbook_root)
    msg = str(excinfo.value)
    # Message must name both versions so the user can act on it.
    assert str(INDEX_SCHEMA_VERSION + 1) in msg
    assert str(INDEX_SCHEMA_VERSION) in msg
    # And must point at the file the user can delete to recover.
    assert str(index_path) in msg


def test_open_index_still_rebuilds_when_on_disk_version_is_older(
    logbook_root: Path,
) -> None:
    """The older-on-disk case stays a routine drop-and-reindex (Branch 3b).

    Regression guard against accidentally turning the older case into
    a refusal alongside the newer one.
    """
    # Fresh open establishes the current version.
    result = open_index(logbook_root)
    result.conn.close()
    index_path = logbook_root / INDEX_FILENAME

    # Stamp an older version — Branch 3b territory.
    _stamp_user_version(index_path, 1)

    # Should succeed and report a rebuild.
    result = open_index(logbook_root)
    try:
        assert result.schema_was_rebuilt is True
        assert result.previous_version == 1
    finally:
        result.conn.close()


def test_open_index_refusal_closes_the_connection(logbook_root: Path) -> None:
    """A refusal must not leak the sqlite3.Connection.

    The exception path runs ``conn.close()`` before raising; this test
    asserts that no file lock survives by reopening the index from a
    fresh connection.
    """
    result = open_index(logbook_root)
    result.conn.close()
    index_path = logbook_root / INDEX_FILENAME

    _stamp_user_version(index_path, INDEX_SCHEMA_VERSION + 99)

    with pytest.raises(IndexSchemaTooNewError):
        open_index(logbook_root)

    # If the connection from the failed open were leaked, this raw
    # connection on the same file would block on a SQLite busy state.
    # 250ms is more than enough for the close() to have completed.
    direct = sqlite3.connect(str(index_path), timeout=0.25)
    try:
        v = direct.execute("PRAGMA user_version").fetchone()[0]
        assert v == INDEX_SCHEMA_VERSION + 99
    finally:
        direct.close()
