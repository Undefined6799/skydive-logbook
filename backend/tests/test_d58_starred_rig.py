"""Tests for the D58 starred-rig feature.

The D58 invariant under test: while ≥1 non-trashed rig exists, exactly
one rig has ``starred=true``. Three service transitions maintain it:

  1. ``create_rig`` auto-stars when the logbook is empty.
  2. ``set_star`` atomically transfers the flag (defensive clear of
     any prior star, then write the target).
  3. ``delete_rig`` of a starred rig auto-elects a successor via
     most-recently-jumped, tiebreaker ``created_at`` DESC.

Each transition is covered here. We also cover the parser/serializer
round-trip for the new ``<starred>`` element and the
invariant-drift recovery posture (a hand-edited or crash-recovery
state with multiple starred rigs is squashed on the next mutation).

Every test uses a real tmp-path-backed logbook root per CLAUDE.md §7
("integration tests for storage primitives must touch a real temp
directory, not mocks"). The fixture composition mirrors
``test_rig_service.py``.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from backend.api.errors import NotFoundError
from backend.models._component_base import ComponentStatus
from backend.models.aad import AADCreate
from backend.models.container import ContainerCreate
from backend.models.jump import JumpCreate
from backend.models.main import MainCreate
from backend.models.reserve import ReserveCreate
from backend.models.rig import Jurisdiction, Rig, RigCreate
from backend.services import (
    aad_service,
    container_service,
    jump_service,
    main_service,
    reserve_service,
    rig_service,
)
from backend.storage.bootstrap import bootstrap_logbook
from backend.storage.index import open_index
from backend.xml.serialize import element_to_rig, rig_to_element

# --------------------------------------------------------------------------- #
# Fixtures + helpers — mirror the test_rig_service.py shape
# --------------------------------------------------------------------------- #


@pytest.fixture
def bootstrapped_root(logbook_root: Path) -> Path:
    """A logbook root with bootstrap applied and the SQLite index open
    so the jumps table exists for the successor-election query.
    """
    bootstrap_logbook(logbook_root)
    result = open_index(logbook_root)
    result.conn.close()
    return logbook_root


def _seed_components(root: Path) -> dict[str, UUID]:
    """Create one of each inventory component and return their ids."""
    main = main_service.create_main(
        root, "default", MainCreate(status=ComponentStatus.ACTIVE, jump_count_initial=0)
    )
    reserve = reserve_service.create_reserve(
        root,
        "default",
        ReserveCreate(
            status=ComponentStatus.ACTIVE,
            repack_count_initial=0,
            ride_count_initial=0,
        ),
    )
    aad = aad_service.create_aad(
        root,
        "default",
        AADCreate(
            status=ComponentStatus.ACTIVE,
            jump_count_initial=0,
            fire_count_initial=0,
        ),
    )
    container = container_service.create_container(
        root,
        "default",
        ContainerCreate(status=ComponentStatus.ACTIVE, jump_count_initial=0),
    )
    return {
        "main": main.id,
        "reserve": reserve.id,
        "aad": aad.id,
        "container": container.id,
    }


def _create_rig(root: Path, nickname: str) -> Rig:
    """Create a rig with a fresh set of seeded components."""
    components = _seed_components(root)
    return rig_service.create_rig(
        root,
        "default",
        RigCreate(
            nickname=nickname,
            jurisdiction=Jurisdiction.USPA,
            current_main_id=components["main"],
            current_reserve_id=components["reserve"],
            current_aad_id=components["aad"],
            current_container_id=components["container"],
        ),
    )


def _log_jump_against(
    root: Path, rig_id: UUID, *, jump_number: int, jump_date: date
) -> None:
    """Persist a real jump record pointing at ``rig_id``.

    The successor-election query in :func:`rig_service._elect_successor_star`
    reads ``MAX(date)`` from the ``jumps`` index, grouped by ``rig_id``.
    Tests for "most-recently-jumped wins" need real rows in that index;
    going through ``jump_service.create_jump`` is the only path that
    populates them (D3: index is rebuilt from XML, but ``create_jump``
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
            rig_id=rig_id,
        ),
    )


def _read_rig_from_disk(root: Path, rig_id: UUID) -> Rig:
    """Fresh disk read — bypass any in-memory state the service holds."""
    return rig_service.get_rig(root, "default", rig_id)


# --------------------------------------------------------------------------- #
# XSD + serialize round-trip
# --------------------------------------------------------------------------- #


class TestXmlRoundtrip:
    """The ``<starred>`` element is optional in the XSD and elided when
    false, so unstarred rig.xml files stay byte-stable with pre-D58
    files. starred=true must round-trip through serialize → XSD validate
    → parse without information loss.
    """

    def test_unstarred_rig_elides_starred_element(self):
        rig = Rig(
            id=uuid4(),
            nickname="Unstarred",
            jurisdiction=Jurisdiction.USPA,
            current_main_id=uuid4(),
            current_reserve_id=uuid4(),
            current_aad_id=uuid4(),
            current_container_id=uuid4(),
            starred=False,
        )
        element = rig_to_element(rig)
        # The element is elided when False so a pre-D58 rig.xml without
        # the field still passes XSD validation and round-trips byte-
        # stable. Find by local-name (lxml namespace stripping).
        starred_children = [
            child for child in element if child.tag.endswith("}starred")
        ]
        assert starred_children == []

    def test_starred_true_roundtrips(self):
        original = Rig(
            id=uuid4(),
            nickname="Starred",
            jurisdiction=Jurisdiction.USPA,
            current_main_id=uuid4(),
            current_reserve_id=uuid4(),
            current_aad_id=uuid4(),
            current_container_id=uuid4(),
            starred=True,
        )
        element = rig_to_element(original)
        restored = element_to_rig(element)
        assert restored.starred is True
        # Round-trip preserves every other field.
        assert restored.id == original.id
        assert restored.nickname == original.nickname

    def test_absent_starred_parses_as_false(self):
        # Pre-D58 file: the rig was serialized before this field
        # existed. The parser must default to False so old rigs
        # don't trip on the missing element.
        rig = Rig(
            id=uuid4(),
            nickname="Pre-D58",
            jurisdiction=Jurisdiction.USPA,
            current_main_id=uuid4(),
            current_reserve_id=uuid4(),
            current_aad_id=uuid4(),
            current_container_id=uuid4(),
            starred=False,
        )
        element = rig_to_element(rig)
        # Confirm no element written.
        assert not [c for c in element if c.tag.endswith("}starred")]
        restored = element_to_rig(element)
        assert restored.starred is False


# --------------------------------------------------------------------------- #
# Transition 1 — create_rig auto-star on empty logbook
# --------------------------------------------------------------------------- #


class TestCreateAutoStar:
    """D58 transition 1: when the logbook contains zero non-trashed
    rigs, the new rig is created with starred=True. Otherwise the
    existing star is left untouched and the new rig is unstarred.
    """

    def test_first_rig_in_empty_logbook_is_starred(
        self, bootstrapped_root: Path
    ):
        rig = _create_rig(bootstrapped_root, "First")
        assert rig.starred is True
        # Confirm on disk too — the in-memory return value must
        # match what got persisted.
        on_disk = _read_rig_from_disk(bootstrapped_root, rig.id)
        assert on_disk.starred is True

    def test_second_rig_does_not_steal_the_star(
        self, bootstrapped_root: Path
    ):
        first = _create_rig(bootstrapped_root, "First")
        second = _create_rig(bootstrapped_root, "Second")
        assert first.starred is True
        # The first's starred flag is what we *wrote* on create; refresh
        # from disk to be sure no one cleared it.
        assert _read_rig_from_disk(bootstrapped_root, first.id).starred is True
        assert second.starred is False
        assert _read_rig_from_disk(bootstrapped_root, second.id).starred is False

    def test_invariant_holds_after_three_creates(
        self, bootstrapped_root: Path
    ):
        # End-to-end: after creating three rigs in sequence, exactly
        # one is starred.
        rigs = [
            _create_rig(bootstrapped_root, f"Rig {i}") for i in range(3)
        ]
        starred = [r for r in rigs if r.starred]
        assert len(starred) == 1
        assert starred[0].id == rigs[0].id


# --------------------------------------------------------------------------- #
# Transition 2 — set_star moves the flag atomically and is idempotent
# --------------------------------------------------------------------------- #


class TestSetStar:
    """D58 transition 2: PUT /rigs/{id}/star → ``set_star``. The only
    mutator. Idempotent, with a defensive clear of any prior stars.
    """

    def test_moves_star_from_one_rig_to_another(
        self, bootstrapped_root: Path
    ):
        first = _create_rig(bootstrapped_root, "First")  # auto-starred
        second = _create_rig(bootstrapped_root, "Second")
        assert first.starred is True
        assert second.starred is False

        result = rig_service.set_star(
            bootstrapped_root, "default", second.id
        )
        assert result.starred is True
        # The prior star must be cleared.
        assert _read_rig_from_disk(bootstrapped_root, first.id).starred is False
        # And the target is the only starred rig.
        assert _read_rig_from_disk(bootstrapped_root, second.id).starred is True

    def test_idempotent_on_already_starred(self, bootstrapped_root: Path):
        first = _create_rig(bootstrapped_root, "First")
        # PUT same id twice — second call is a no-op.
        result_1 = rig_service.set_star(
            bootstrapped_root, "default", first.id
        )
        result_2 = rig_service.set_star(
            bootstrapped_root, "default", first.id
        )
        assert result_1.starred is True
        assert result_2.starred is True
        # Still exactly one starred rig.
        all_rigs = rig_service.list_rigs(bootstrapped_root, "default")
        assert sum(1 for r in all_rigs if r.starred) == 1

    def test_missing_rig_raises_not_found(self, bootstrapped_root: Path):
        # No rigs exist → any id is missing.
        with pytest.raises(NotFoundError):
            rig_service.set_star(bootstrapped_root, "default", uuid4())

    def test_clears_drift_from_multiple_starred(
        self, bootstrapped_root: Path
    ):
        # Simulate invariant drift: hand-edited XML with two starred
        # rigs (or a crash recovery state). set_star must clear *both*
        # priors, not just one — the defensive clear scans every rig.
        # ``_first`` is intentionally unread — it's the auto-starred
        # rig we're forging into a multi-starred state alongside
        # ``second``.
        _first = _create_rig(bootstrapped_root, "First")  # auto-starred
        second = _create_rig(bootstrapped_root, "Second")
        # Manually star ``second`` by writing rig.xml directly through
        # the service helper. We can't do this through set_star — that
        # would clear first as a side-effect. We need to forge the
        # multi-starred state.
        folder = bootstrapped_root / "rigs" / "second"
        starred_second = second.model_copy(update={"starred": True})
        rig_service._write_rig_folder(folder, starred_second)
        # Sanity: both are starred now.
        all_rigs = rig_service.list_rigs(bootstrapped_root, "default")
        assert sum(1 for r in all_rigs if r.starred) == 2

        # Now create a third rig and star it. Drift should be healed.
        third = _create_rig(bootstrapped_root, "Third")
        rig_service.set_star(bootstrapped_root, "default", third.id)

        all_rigs = rig_service.list_rigs(bootstrapped_root, "default")
        starred_ids = {r.id for r in all_rigs if r.starred}
        assert starred_ids == {third.id}


# --------------------------------------------------------------------------- #
# Transition 3 — delete_rig auto-moves the star
# --------------------------------------------------------------------------- #


class TestDeleteAutoMove:
    """D58 transition 3: soft-delete of the starred rig elects a
    successor from the remaining rigs (most-recent jump, then
    created_at DESC).
    """

    def test_delete_starred_with_no_jumps_picks_leftmost_remaining(
        self, bootstrapped_root: Path
    ):
        # D58 + D59 tiebreaker: no rig has any jumps logged, so the
        # election falls through to display_order ASC (D59 amended
        # D58 to match the carousel's user-visible order). ``first``
        # was created earliest (display_order=0, starred); after
        # delete, ``second`` (display_order=1, now the leftmost
        # remaining) should inherit the star — NOT ``third``
        # (display_order=2, rightmost).
        first = _create_rig(bootstrapped_root, "First")  # starred
        second = _create_rig(bootstrapped_root, "Second")
        third = _create_rig(bootstrapped_root, "Third")
        assert first.starred is True

        rig_service.delete_rig(bootstrapped_root, "default", first.id)

        # Successor: leftmost remaining (lowest display_order).
        second_after = _read_rig_from_disk(bootstrapped_root, second.id)
        third_after = _read_rig_from_disk(bootstrapped_root, third.id)
        assert second_after.starred is True
        assert third_after.starred is False

    def test_delete_starred_with_jumps_picks_most_recently_used(
        self, bootstrapped_root: Path
    ):
        # Primary election rule: MAX(date) per rig_id from the jumps
        # index. We log a *later* jump against ``second`` than against
        # ``third`` so the election picks ``second`` even though
        # ``third`` is the more-recently-created rig.
        first = _create_rig(bootstrapped_root, "First")  # starred
        second = _create_rig(bootstrapped_root, "Second")
        third = _create_rig(bootstrapped_root, "Third")

        _log_jump_against(
            bootstrapped_root, third.id, jump_number=1,
            jump_date=date(2026, 1, 10),
        )
        _log_jump_against(
            bootstrapped_root, second.id, jump_number=2,
            jump_date=date(2026, 4, 15),
        )

        rig_service.delete_rig(bootstrapped_root, "default", first.id)

        # second has the most-recent jump (2026-04-15) so it wins,
        # even though third was created later.
        assert _read_rig_from_disk(bootstrapped_root, second.id).starred is True
        assert _read_rig_from_disk(bootstrapped_root, third.id).starred is False

    def test_delete_unstarred_does_not_disturb_the_star(
        self, bootstrapped_root: Path
    ):
        # Soft-deleting a non-starred rig must not trigger any star
        # transition — the starred rig keeps its flag.
        first = _create_rig(bootstrapped_root, "First")  # starred
        second = _create_rig(bootstrapped_root, "Second")

        rig_service.delete_rig(bootstrapped_root, "default", second.id)

        assert _read_rig_from_disk(bootstrapped_root, first.id).starred is True

    def test_delete_only_rig_leaves_zero_starred(
        self, bootstrapped_root: Path
    ):
        # Last rig in the logbook is starred (auto-star on create).
        # Deleting it leaves zero rigs, zero starred — consistent
        # with the D58 invariant.
        only = _create_rig(bootstrapped_root, "Only")
        assert only.starred is True

        rig_service.delete_rig(bootstrapped_root, "default", only.id)

        remaining = rig_service.list_rigs(bootstrapped_root, "default")
        assert remaining == []

    def test_next_create_after_full_delete_auto_stars(
        self, bootstrapped_root: Path
    ):
        # The "zero rigs ⇒ next create auto-stars" rule has to keep
        # working after a full delete cycle, not just on a brand-new
        # logbook.
        only = _create_rig(bootstrapped_root, "Only")
        rig_service.delete_rig(bootstrapped_root, "default", only.id)
        # Index now has stale jump rows? No — we logged none. Just
        # confirm the next create restarts the auto-star.
        replacement = _create_rig(bootstrapped_root, "Replacement")
        assert replacement.starred is True
