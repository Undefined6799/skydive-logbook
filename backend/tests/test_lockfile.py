"""Tests for the single-instance lock (D9)."""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.storage.lockfile import LOCK_FILENAME, LockError, acquire


class TestAcquire:
    def test_acquires_and_releases(self, tmp_path: Path):
        lock = acquire(tmp_path)
        try:
            assert lock.is_locked
            assert (tmp_path / LOCK_FILENAME).exists()
        finally:
            lock.release()

    def test_second_acquire_fails_fast(self, tmp_path: Path):
        first = acquire(tmp_path, timeout=0.1)
        try:
            with pytest.raises(LockError):
                acquire(tmp_path, timeout=0.1)
        finally:
            first.release()

    def test_creates_root_if_missing(self, tmp_path: Path):
        root = tmp_path / "does-not-exist-yet"
        lock = acquire(root)
        try:
            assert root.is_dir()
        finally:
            lock.release()
