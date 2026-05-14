"""Soft-resolution helper tests for people_service (D54 §Decision).

``resolve_person_names`` is the read-side counterpart to D54's "soft
resolution" contract: jump-side references (``<packed_by>``,
``<group_members>``) carry UUIDs that may or may not resolve to an
active Person record. Stale refs degrade gracefully into a legible
fallback label rather than raising.

Pinned contracts:
- All-resolve case returns the actual ``name`` for every UUID.
- All-stale case returns ``Unknown person <8-hex-prefix>`` for each.
- Mixed case interleaves both correctly.
- Empty input ⇒ empty dict.
- Duplicate UUIDs collapse to a single entry.
- Iteration order of the returned dict matches the input order
  (post-dedup) so caller-side rendering stays stable.
- Deleting a Person turns subsequent resolutions into the unknown
  fallback (no orphaned cache).
"""
from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest

from backend.models.person import PersonCreate
from backend.services import people_service
from backend.services.people_service import resolve_person_names
from backend.storage.bootstrap import bootstrap_logbook
from backend.storage.index import open_index


@pytest.fixture
def bootstrapped_root(logbook_root: Path) -> Path:
    bootstrap_logbook(logbook_root)
    # Prime the index so service calls see the current schema.
    result = open_index(logbook_root)
    result.conn.close()
    return logbook_root


def _create(root: Path, name: str) -> UUID:
    """Create a Person and return its UUID."""
    return people_service.create_person(
        root, "default", PersonCreate(name=name)
    ).id


# --------------------------------------------------------------------------- #
# Happy paths
# --------------------------------------------------------------------------- #

class TestResolveAllKnown:
    def test_single_uuid_resolves_to_name(self, bootstrapped_root: Path):
        pid = _create(bootstrapped_root, "Alice")
        labels = resolve_person_names(bootstrapped_root, [pid])
        assert labels == {pid: "Alice"}

    def test_multiple_uuids_resolve(self, bootstrapped_root: Path):
        a = _create(bootstrapped_root, "Alice")
        b = _create(bootstrapped_root, "Bob")
        c = _create(bootstrapped_root, "Charlie")
        labels = resolve_person_names(bootstrapped_root, [a, b, c])
        assert labels == {a: "Alice", b: "Bob", c: "Charlie"}

    def test_iteration_order_matches_input(self, bootstrapped_root: Path):
        # The caller's order is preserved: a UI rendering "Group: A,
        # B, C" must see the same order from the resolver as it
        # passed in, not whatever SQLite happened to return.
        a = _create(bootstrapped_root, "Alice")
        b = _create(bootstrapped_root, "Bob")
        c = _create(bootstrapped_root, "Charlie")
        # Pass in non-alphabetical order; resolver preserves it.
        labels = resolve_person_names(bootstrapped_root, [c, a, b])
        assert list(labels.keys()) == [c, a, b]


# --------------------------------------------------------------------------- #
# Unknown / stale references
# --------------------------------------------------------------------------- #

class TestResolveStale:
    def test_all_stale_yields_unknown_labels(self, bootstrapped_root: Path):
        # Brand-new UUIDs that never matched a Person. The label
        # format is fixed by D54: ``Unknown person <8-hex>``.
        pid = uuid4()
        labels = resolve_person_names(bootstrapped_root, [pid])
        expected = f"Unknown person {str(pid)[:8]}"
        assert labels == {pid: expected}

    def test_unknown_label_uses_first_eight_hex_chars(
        self, bootstrapped_root: Path
    ):
        # Construct a deterministic UUID so we can assert the prefix
        # exactly. The first segment of a canonical UUID is its 8-hex
        # leading group.
        pid = UUID("12345678-1234-4567-89ab-cdef01234567")
        labels = resolve_person_names(bootstrapped_root, [pid])
        assert labels[pid] == "Unknown person 12345678"

    def test_empty_index_treats_every_uuid_as_unknown(
        self, bootstrapped_root: Path
    ):
        # No Person records created yet — every input UUID falls
        # through to the unknown branch.
        ids = [uuid4() for _ in range(3)]
        labels = resolve_person_names(bootstrapped_root, ids)
        for pid in ids:
            assert labels[pid] == f"Unknown person {str(pid)[:8]}"

    def test_deleted_person_becomes_unknown(self, bootstrapped_root: Path):
        # Pre-D54-Phase-2b: resolution before delete returns the
        # name. Post-delete: same UUID returns the unknown label.
        # Pin so a future "cache stale Person rows" change has to
        # confront this contract directly.
        pid = _create(bootstrapped_root, "Doomed")
        before = resolve_person_names(bootstrapped_root, [pid])
        assert before == {pid: "Doomed"}

        people_service.delete_person(bootstrapped_root, "default", pid)

        after = resolve_person_names(bootstrapped_root, [pid])
        assert after == {pid: f"Unknown person {str(pid)[:8]}"}


# --------------------------------------------------------------------------- #
# Mixed batches — the real-world shape for jump detail
# --------------------------------------------------------------------------- #

class TestResolveMixed:
    def test_mix_of_known_and_stale(self, bootstrapped_root: Path):
        a = _create(bootstrapped_root, "Alice")
        ghost = uuid4()
        b = _create(bootstrapped_root, "Bob")

        labels = resolve_person_names(bootstrapped_root, [a, ghost, b])
        assert labels[a] == "Alice"
        assert labels[ghost] == f"Unknown person {str(ghost)[:8]}"
        assert labels[b] == "Bob"

    def test_packed_by_overlaps_with_group_member(
        self, bootstrapped_root: Path
    ):
        # Real shape: the same Person can appear as both packer and
        # group member on a jump (a friend who packed for you and
        # also flew on the load). Duplicate UUIDs in the input must
        # collapse to a single dict entry without losing information.
        pid = _create(bootstrapped_root, "Eve")
        labels = resolve_person_names(bootstrapped_root, [pid, pid, pid])
        assert labels == {pid: "Eve"}


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #

class TestResolveEdgeCases:
    def test_empty_input_returns_empty_dict(self, bootstrapped_root: Path):
        # No SQL query, no allocations beyond the empty dict.
        assert resolve_person_names(bootstrapped_root, []) == {}

    def test_tuple_input_works(self, bootstrapped_root: Path):
        # The signature accepts ``list[UUID] | tuple[UUID, ...]``.
        # A caller iterating from a Pydantic ``list[UUID]`` field
        # might hand us a tuple after a model_dump round-trip.
        pid = _create(bootstrapped_root, "Frank")
        labels = resolve_person_names(bootstrapped_root, (pid,))
        assert labels == {pid: "Frank"}

    def test_large_batch(self, bootstrapped_root: Path):
        # Sanity: a batch of 25 UUIDs (well within SQLite's parameter
        # limit). Mixed known + unknown so both branches exercise.
        known = [_create(bootstrapped_root, f"P{i}") for i in range(15)]
        unknown = [uuid4() for _ in range(10)]
        labels = resolve_person_names(bootstrapped_root, known + unknown)
        for i, pid in enumerate(known):
            assert labels[pid] == f"P{i}"
        for pid in unknown:
            assert labels[pid] == f"Unknown person {str(pid)[:8]}"
        assert len(labels) == 25
