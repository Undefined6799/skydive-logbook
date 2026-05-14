"""Tests for ``list_jumps`` — Phase 3.1 read path.

Contracts under test:

  * Empty index → ``[]`` (no error).
  * Rows come back as ``JumpSummary`` — the slim index-only projection.
  * Ordering: ``date DESC, jump_number DESC``. Most recent first;
    same-day ties break by higher number first (later jump on a busy
    day appears ahead of earlier jumps).
  * ``limit`` + ``offset`` pagination works as a simple window.
  * User isolation: ``list_jumps("alice")`` sees only alice's rows.
  * Title round-trips through the index (populated by ``create_jump``
    in Phase 3.1 — previously wasn't in the index at all).
  * No XML reads during ``list_jumps`` — service reads from the index
    only, so adding N jumps is O(N) SQLite + zero lxml calls. Proven
    structurally by deleting a jump.xml post-creation and asserting
    list still returns the row (index is the source of truth for
    list views).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from backend.models.jump import JumpSummary
from backend.services.jump_service import create_jump, list_jumps
from backend.storage.bootstrap import bootstrap_logbook
from backend.storage.index import open_index

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

@pytest.fixture
def bootstrapped_root(tmp_path: Path) -> Path:
    root = tmp_path / "logbook"
    bootstrap_logbook(root)
    result = open_index(root)
    result.conn.close()
    return root


def _create(root: Path, *, user_id: str = "default", **overrides):
    from backend.models.jump import JumpCreate

    data = dict(
        jump_number=1,
        date=date(2026, 1, 1),
        dropzone="Skydive Elsinore",
        exit_altitude_m=4000,
        deployment_altitude_m=900,
    )
    data.update(overrides)
    return create_jump(root, user_id, JumpCreate(**data))


# --------------------------------------------------------------------------- #
# Empty / basic
# --------------------------------------------------------------------------- #

class TestEmpty:
    def test_empty_index_returns_empty_list(self, bootstrapped_root: Path):
        assert list_jumps(bootstrapped_root, "default") == []

    def test_returns_jump_summary_type(self, bootstrapped_root: Path):
        _create(bootstrapped_root, jump_number=1)
        result = list_jumps(bootstrapped_root, "default")
        assert len(result) == 1
        assert isinstance(result[0], JumpSummary)


# --------------------------------------------------------------------------- #
# Ordering
# --------------------------------------------------------------------------- #

class TestOrdering:
    def test_reverse_chronological_by_date(self, bootstrapped_root: Path):
        _create(bootstrapped_root, jump_number=1, date=date(2026, 1, 1))
        _create(bootstrapped_root, jump_number=2, date=date(2026, 3, 1))
        _create(bootstrapped_root, jump_number=3, date=date(2026, 2, 1))

        result = list_jumps(bootstrapped_root, "default")
        assert [r.jump_number for r in result] == [2, 3, 1]

    def test_same_day_ties_break_by_higher_jump_number(
        self, bootstrapped_root: Path
    ):
        # Same calendar date → the LATER jump_number (i.e., the jump
        # logged later that day) sorts first. Matches how a skydiver
        # would scan their most recent session.
        _create(bootstrapped_root, jump_number=100, date=date(2026, 5, 1))
        _create(bootstrapped_root, jump_number=101, date=date(2026, 5, 1))
        _create(bootstrapped_root, jump_number=102, date=date(2026, 5, 1))

        result = list_jumps(bootstrapped_root, "default")
        assert [r.jump_number for r in result] == [102, 101, 100]


# --------------------------------------------------------------------------- #
# Pagination
# --------------------------------------------------------------------------- #

class TestPagination:
    def test_limit_caps_page_size(self, bootstrapped_root: Path):
        for n in range(1, 6):
            _create(bootstrapped_root, jump_number=n, date=date(2026, 1, n))
        assert len(list_jumps(bootstrapped_root, "default", limit=3)) == 3

    def test_offset_skips_rows(self, bootstrapped_root: Path):
        # Ordering is date DESC, jump_number DESC, so with dates 1..5
        # we expect the offset to skip the most recent first.
        for n in range(1, 6):
            _create(bootstrapped_root, jump_number=n, date=date(2026, 1, n))
        result = list_jumps(bootstrapped_root, "default", limit=2, offset=2)
        assert [r.jump_number for r in result] == [3, 2]

    def test_offset_past_end_returns_empty(self, bootstrapped_root: Path):
        _create(bootstrapped_root, jump_number=1)
        assert list_jumps(bootstrapped_root, "default", limit=10, offset=5) == []


# --------------------------------------------------------------------------- #
# User isolation
# --------------------------------------------------------------------------- #

class TestUserIsolation:
    def test_default_user_does_not_see_other_users(
        self, bootstrapped_root: Path
    ):
        # Different user, different jump_number so filesystem collision
        # doesn't hit us (v0.1 folder namespace is shared per D23).
        _create(bootstrapped_root, user_id="default", jump_number=1)
        _create(bootstrapped_root, user_id="alice", jump_number=2)

        default_list = list_jumps(bootstrapped_root, "default")
        alice_list = list_jumps(bootstrapped_root, "alice")

        assert len(default_list) == 1
        assert default_list[0].jump_number == 1
        assert len(alice_list) == 1
        assert alice_list[0].jump_number == 2

    def test_unknown_user_sees_no_rows(self, bootstrapped_root: Path):
        _create(bootstrapped_root, jump_number=1)
        assert list_jumps(bootstrapped_root, "nobody") == []


# --------------------------------------------------------------------------- #
# Title denormalization
# --------------------------------------------------------------------------- #

class TestTitle:
    def test_title_present_round_trips(self, bootstrapped_root: Path):
        _create(
            bootstrapped_root, jump_number=1, title="First 4-way of the season"
        )
        (summary,) = list_jumps(bootstrapped_root, "default")
        assert summary.title == "First 4-way of the season"

    def test_title_absent_is_none(self, bootstrapped_root: Path):
        # No title kwarg → summary.title is None, not an empty string.
        _create(bootstrapped_root, jump_number=1)
        (summary,) = list_jumps(bootstrapped_root, "default")
        assert summary.title is None


# --------------------------------------------------------------------------- #
# Index-only path (no XML reads)
# --------------------------------------------------------------------------- #

class TestIndexOnly:
    def test_list_works_even_if_jump_xml_is_removed(
        self, bootstrapped_root: Path
    ):
        # Regression pin: list_jumps reads from the index only. If the
        # on-disk XML is missing (a user deleted the folder, sync hasn't
        # completed, etc.) the list still returns the index row. Detail
        # reads (``get_jump``) would fail on the missing XML, but
        # browsing the list must not.
        jump = _create(bootstrapped_root, jump_number=42, title="Gone")
        folder = bootstrapped_root / "jumps" / "[42] Gone"
        (folder / "jump.xml").unlink()

        result = list_jumps(bootstrapped_root, "default")
        assert len(result) == 1
        assert result[0].id == jump.id
        assert result[0].title == "Gone"
