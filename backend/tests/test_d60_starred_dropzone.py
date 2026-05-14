"""Tests for the D60 starred-dropzone feature.

The D60 invariant under test: while ≥1 non-trashed dropzone exists,
exactly one DZ has ``starred=true``. Three service transitions
maintain it:

  1. ``create_dropzone`` auto-stars when the logbook is empty.
  2. ``set_star`` atomically transfers the flag (defensive clear of
     any prior star, then write the target).
  3. ``delete_dropzone`` of a starred DZ auto-elects a successor via
     most-recently-jumped (MAX(date) GROUP BY dropzone_id over the
     v10 jumps.dropzone_id column), alphabetical fallback when no
     candidate has any jumps logged.

Each transition is covered here. We also cover the parser/serializer
round-trip for the new ``<starred>`` element, the index column
projection through DropzoneSummary, and the invariant-drift recovery
posture (a hand-edited or crash-recovery state with multiple
starred DZs is squashed on the next mutation).

Every test uses a real tmp_path-backed logbook root per CLAUDE.md §7.
Mirrors the shape of ``test_d58_starred_rig.py``.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from backend.api.errors import NotFoundError
from backend.models.dropzone import (
    Dropzone,
    DropzoneCreate,
    DropzoneUpdate,
    Environment,
)
from backend.models.jump import JumpCreate
from backend.services import dropzone_service, jump_service
from backend.storage.bootstrap import bootstrap_logbook
from backend.storage.index import open_index
from backend.xml.serialize import dropzone_to_element, element_to_dropzone
from backend.xml.validator import validate as xsd_validate

# --------------------------------------------------------------------------- #
# Fixtures + helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def bootstrapped_root(logbook_root: Path) -> Path:
    """A logbook root with bootstrap applied and the SQLite index open
    so the dropzones / jumps tables exist for the count + election
    queries.
    """
    bootstrap_logbook(logbook_root)
    result = open_index(logbook_root)
    result.conn.close()
    return logbook_root


def _create_payload(
    *,
    name: str = "Skydive Elsinore",
    city: str = "Lake Elsinore",
    country: str = "US",
    environment: Environment = Environment.DUST_SAND_SALT,
) -> DropzoneCreate:
    return DropzoneCreate(
        name=name,
        city=city,
        country=country,
        environment=environment,
    )


def _create_dz(
    root: Path, *, name: str, city: str = "City"
) -> Dropzone:
    return dropzone_service.create_dropzone(
        root, "default", _create_payload(name=name, city=city)
    )


def _log_jump_against(
    root: Path,
    dropzone_id: UUID,
    *,
    jump_number: int,
    jump_date: date,
) -> None:
    """Persist a real jump record pointing at ``dropzone_id``.

    The D60 successor-election query reads ``MAX(date)`` from the
    jumps index, grouped by ``dropzone_id``. Tests for
    "most-recently-jumped wins" need real rows in that index;
    ``jump_service.create_jump`` is the only path that populates
    them (D3: index is rebuildable from XML, but create_jump
    writes both in one transaction).
    """
    jump_service.create_jump(
        root,
        "default",
        JumpCreate(
            jump_number=jump_number,
            date=jump_date,  # type: ignore[arg-type]  # pydantic coerces date
            dropzone="Skydive Elsinore",
            exit_altitude_m=4000,
            deployment_altitude_m=900,
            dropzone_id=dropzone_id,
        ),
    )


def _read_dz_from_disk(root: Path, dropzone_id: UUID) -> Dropzone:
    """Fresh disk read — bypass any in-memory state the service holds."""
    return dropzone_service.get_dropzone(root, "default", dropzone_id)


# --------------------------------------------------------------------------- #
# XSD + serialize round-trip
# --------------------------------------------------------------------------- #


class TestXmlRoundtrip:
    """The ``<starred>`` element is optional in the XSD and elided when
    false, so unstarred dropzone.xml files stay byte-stable with
    pre-D60 files. starred=true must round-trip through serialize →
    XSD validate → parse without information loss.
    """

    def test_unstarred_dz_elides_starred_element(self):
        dz = Dropzone(
            id=uuid4(),
            name="Unstarred",
            city="Lake X",
            country="US",
            environment=Environment.CLEAN_GRASS,
            starred=False,
        )
        element = dropzone_to_element(dz)
        starred_children = [
            child for child in element if child.tag.endswith("}starred")
        ]
        assert starred_children == []
        # And it XSD-validates without the element.
        xsd_validate(element)

    def test_starred_true_roundtrips(self):
        original = Dropzone(
            id=uuid4(),
            name="Starred",
            city="Lake X",
            country="US",
            environment=Environment.CLEAN_GRASS,
            starred=True,
        )
        element = dropzone_to_element(original)
        xsd_validate(element)
        restored = element_to_dropzone(element)
        assert restored.starred is True
        # Round-trip preserves every other field.
        assert restored.id == original.id
        assert restored.name == original.name
        assert restored.city == original.city

    def test_absent_starred_parses_as_false(self):
        # Pre-D60 file: DZ serialized before the field existed.
        # Parser must default to False so old DZs don't trip.
        dz = Dropzone(
            id=uuid4(),
            name="Pre-D60",
            city="Lake X",
            country="US",
            environment=Environment.CLEAN_GRASS,
            starred=False,
        )
        element = dropzone_to_element(dz)
        assert not [c for c in element if c.tag.endswith("}starred")]
        restored = element_to_dropzone(element)
        assert restored.starred is False


# --------------------------------------------------------------------------- #
# Transition 1 — create_dropzone auto-star on empty logbook
# --------------------------------------------------------------------------- #


class TestCreateAutoStar:
    """D60 transition 1: when the logbook contains zero non-trashed
    DZs, the new dropzone is created with starred=True. Otherwise the
    existing star is left untouched and the new dropzone is unstarred.
    """

    def test_first_dz_in_empty_logbook_is_starred(
        self, bootstrapped_root: Path
    ):
        dz = _create_dz(bootstrapped_root, name="First")
        assert dz.starred is True
        # Confirm on disk too — the returned value must match what got
        # persisted.
        on_disk = _read_dz_from_disk(bootstrapped_root, dz.id)
        assert on_disk.starred is True

    def test_second_dz_does_not_steal_the_star(
        self, bootstrapped_root: Path
    ):
        first = _create_dz(bootstrapped_root, name="First")
        second = _create_dz(bootstrapped_root, name="Second")
        assert first.starred is True
        assert _read_dz_from_disk(bootstrapped_root, first.id).starred is True
        assert second.starred is False
        assert _read_dz_from_disk(bootstrapped_root, second.id).starred is False

    def test_invariant_holds_after_three_creates(
        self, bootstrapped_root: Path
    ):
        dzs = [
            _create_dz(bootstrapped_root, name=f"DZ {i}") for i in range(3)
        ]
        starred = [d for d in dzs if d.starred]
        assert len(starred) == 1
        assert starred[0].id == dzs[0].id


# --------------------------------------------------------------------------- #
# Transition 2 — set_star atomic transfer + idempotency
# --------------------------------------------------------------------------- #


class TestSetStar:
    """D60 transition 2: PUT /dropzones/{id}/star → ``set_star``. The
    only mutator for the flag. Idempotent; defensive clear of any
    prior stars.
    """

    def test_moves_star_from_one_dz_to_another(
        self, bootstrapped_root: Path
    ):
        first = _create_dz(bootstrapped_root, name="First")
        second = _create_dz(bootstrapped_root, name="Second")
        # Sanity: D60 transition 1 starred first only.
        assert first.starred is True
        assert second.starred is False

        result = dropzone_service.set_star(
            bootstrapped_root, "default", second.id
        )
        assert result.starred is True
        # Both reflect the transfer on disk.
        assert _read_dz_from_disk(bootstrapped_root, first.id).starred is False
        assert _read_dz_from_disk(bootstrapped_root, second.id).starred is True

    def test_idempotent_on_already_starred_target(
        self, bootstrapped_root: Path
    ):
        first = _create_dz(bootstrapped_root, name="First")
        assert first.starred is True
        # No-op write path. The returned record still reports starred.
        result = dropzone_service.set_star(
            bootstrapped_root, "default", first.id
        )
        assert result.starred is True
        assert _read_dz_from_disk(bootstrapped_root, first.id).starred is True

    def test_missing_target_raises_not_found(
        self, bootstrapped_root: Path
    ):
        _create_dz(bootstrapped_root, name="First")
        with pytest.raises(NotFoundError):
            dropzone_service.set_star(
                bootstrapped_root, "default", uuid4()
            )

    def test_metadata_update_preserves_star(
        self, bootstrapped_root: Path
    ):
        # D60: ``starred`` is NOT on DropzoneUpdate. A metadata edit
        # via PUT /dropzones/{id} must NOT silently drop the star —
        # update_dropzone preserves the current value.
        first = _create_dz(bootstrapped_root, name="First")
        assert first.starred is True
        updated = dropzone_service.update_dropzone(
            bootstrapped_root,
            "default",
            first.id,
            DropzoneUpdate(
                name="First Renamed",
                city="Lake X",
                country="US",
                environment=Environment.CLEAN_GRASS,
            ),
        )
        assert updated.starred is True
        assert _read_dz_from_disk(bootstrapped_root, first.id).starred is True

    def test_squashes_multi_starred_drift(self, bootstrapped_root: Path):
        # Simulate the drift case: two DZs both starred on disk
        # (could happen via hand-edit or pre-D60 file). set_star
        # clears every prior star, not just "the" one.
        first = _create_dz(bootstrapped_root, name="First")
        second = _create_dz(bootstrapped_root, name="Second")
        # Force-write second as starred behind the service's back —
        # mirrors a hand-edited dropzone.xml.
        forged = second.model_copy(update={"starred": True})
        dropzone_service._write_dropzone(bootstrapped_root, forged)
        dropzone_service._upsert_index_row(bootstrapped_root, forged)
        # Now both are starred. Pick a *third* as the new star.
        third = _create_dz(bootstrapped_root, name="Third")
        assert third.starred is False  # count was non-zero on create
        dropzone_service.set_star(bootstrapped_root, "default", third.id)
        # Invariant: exactly one starred after the mutation.
        assert _read_dz_from_disk(bootstrapped_root, first.id).starred is False
        assert _read_dz_from_disk(bootstrapped_root, second.id).starred is False
        assert _read_dz_from_disk(bootstrapped_root, third.id).starred is True


# --------------------------------------------------------------------------- #
# Transition 3 — delete_dropzone elects a successor via most-recently-jumped
# --------------------------------------------------------------------------- #


class TestDeleteStarred:
    """D60 transition 3: deleting the starred DZ with others remaining
    auto-moves the star to the most-recently-jumped successor, with
    alphabetical tiebreak when no candidate has any jumps logged.
    Last-DZ delete leaves zero starred.
    """

    def test_most_recently_jumped_wins(self, bootstrapped_root: Path):
        # Three DZs; first is auto-starred. Log jumps against second
        # (recent) and third (older). After deleting first, second
        # should win the star.
        first = _create_dz(bootstrapped_root, name="First")
        second = _create_dz(bootstrapped_root, name="Second")
        third = _create_dz(bootstrapped_root, name="Third")
        _log_jump_against(
            bootstrapped_root, third.id,
            jump_number=1, jump_date=date(2026, 1, 1),
        )
        _log_jump_against(
            bootstrapped_root, second.id,
            jump_number=2, jump_date=date(2026, 5, 1),
        )
        dropzone_service.delete_dropzone(
            bootstrapped_root, "default", first.id
        )
        assert _read_dz_from_disk(bootstrapped_root, second.id).starred is True
        assert _read_dz_from_disk(bootstrapped_root, third.id).starred is False

    def test_alphabetical_fallback_when_no_candidate_has_jumps(
        self, bootstrapped_root: Path
    ):
        # Three DZs, none have any jumps logged. After deleting the
        # starred one, the alphabetical-first remaining DZ wins.
        first = _create_dz(bootstrapped_root, name="First")
        # Names chosen so alphabetical NOCASE order is
        # Bravo < Charlie. Delete first → between Bravo and Charlie,
        # Bravo wins.
        bravo = _create_dz(bootstrapped_root, name="Bravo")
        charlie = _create_dz(bootstrapped_root, name="Charlie")
        dropzone_service.delete_dropzone(
            bootstrapped_root, "default", first.id
        )
        assert _read_dz_from_disk(bootstrapped_root, bravo.id).starred is True
        assert _read_dz_from_disk(bootstrapped_root, charlie.id).starred is False

    def test_alphabetical_is_case_insensitive(
        self, bootstrapped_root: Path
    ):
        # NOCASE collation: "alpha" sorts before "Bravo" before "charlie".
        first = _create_dz(bootstrapped_root, name="zulu")
        # first is starred (only DZ at time of create).
        alpha = _create_dz(bootstrapped_root, name="alpha")
        _create_dz(bootstrapped_root, name="Bravo")
        dropzone_service.delete_dropzone(
            bootstrapped_root, "default", first.id
        )
        assert _read_dz_from_disk(bootstrapped_root, alpha.id).starred is True

    def test_last_dz_delete_leaves_zero_starred(
        self, bootstrapped_root: Path
    ):
        # Last (and only) starred DZ deleted — no successor exists.
        # Invariant "≥1 DZ ⇒ exactly one starred" is trivially
        # satisfied because zero DZs remain.
        only = _create_dz(bootstrapped_root, name="Only")
        dropzone_service.delete_dropzone(
            bootstrapped_root, "default", only.id
        )
        # No DZs in index — trivially zero starred.
        result = open_index(bootstrapped_root)
        try:
            rows = result.conn.execute(
                "SELECT COUNT(*) AS n FROM dropzones"
            ).fetchone()
        finally:
            result.conn.close()
        assert rows["n"] == 0

    def test_delete_unstarred_dz_leaves_star_alone(
        self, bootstrapped_root: Path
    ):
        first = _create_dz(bootstrapped_root, name="First")  # starred
        second = _create_dz(bootstrapped_root, name="Second")  # not
        dropzone_service.delete_dropzone(
            bootstrapped_root, "default", second.id
        )
        # first still starred — deleting a non-starred DZ doesn't
        # trigger the auto-move path.
        assert _read_dz_from_disk(bootstrapped_root, first.id).starred is True


# --------------------------------------------------------------------------- #
# Index projection — DropzoneSummary.starred round-trips through list
# --------------------------------------------------------------------------- #


class TestListSummaryStarred:
    """The v10 ``dropzones.starred`` column powers DropzoneSummary.starred
    so the LogJumpModal can find the default DZ in one round-trip
    through GET /dropzones.
    """

    def test_starred_flag_round_trips_through_list(
        self, bootstrapped_root: Path
    ):
        first = _create_dz(bootstrapped_root, name="First")
        second = _create_dz(bootstrapped_root, name="Second")
        summaries = dropzone_service.list_dropzones(
            bootstrapped_root, "default"
        )
        by_id = {s.id: s for s in summaries}
        assert by_id[first.id].starred is True
        assert by_id[second.id].starred is False

    def test_star_transfer_reflected_in_list(
        self, bootstrapped_root: Path
    ):
        first = _create_dz(bootstrapped_root, name="First")
        second = _create_dz(bootstrapped_root, name="Second")
        dropzone_service.set_star(bootstrapped_root, "default", second.id)
        summaries = dropzone_service.list_dropzones(
            bootstrapped_root, "default"
        )
        by_id = {s.id: s for s in summaries}
        assert by_id[first.id].starred is False
        assert by_id[second.id].starred is True


# --------------------------------------------------------------------------- #
# Reindex — starred + dropzone_id rebuild from XML alone (D3)
# --------------------------------------------------------------------------- #


class TestReindex:
    """D3 guarantee: the SQLite index is rebuildable from XML alone.
    Drop the index, reindex, verify the ``starred`` and
    ``dropzone_id`` columns repopulate from the on-disk XML.
    """

    def test_reindex_repopulates_starred_and_dropzone_id(
        self, bootstrapped_root: Path
    ):
        first = _create_dz(bootstrapped_root, name="First")
        second = _create_dz(bootstrapped_root, name="Second")
        dropzone_service.set_star(bootstrapped_root, "default", second.id)
        _log_jump_against(
            bootstrapped_root, second.id,
            jump_number=1, jump_date=date(2026, 5, 1),
        )

        # Drop the index file entirely so open_index runs a fresh
        # install on next access, then run reindex_from_xml.
        from backend.services import reindex_service
        from backend.storage.index import INDEX_FILENAME
        (bootstrapped_root / INDEX_FILENAME).unlink()
        reindex_service.reindex_from_xml(bootstrapped_root)

        # ``starred`` survives: second still wears the star.
        result = open_index(bootstrapped_root)
        try:
            rows = {
                r["id"]: r["starred"]
                for r in result.conn.execute(
                    "SELECT id, starred FROM dropzones"
                ).fetchall()
            }
        finally:
            result.conn.close()
        assert rows[str(first.id)] == 0
        assert rows[str(second.id)] == 1

        # ``dropzone_id`` survives on the jump row.
        result = open_index(bootstrapped_root)
        try:
            row = result.conn.execute(
                "SELECT dropzone_id FROM jumps WHERE jump_number = ?",
                (1,),
            ).fetchone()
        finally:
            result.conn.close()
        assert row["dropzone_id"] == str(second.id)
