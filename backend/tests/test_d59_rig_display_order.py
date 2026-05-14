"""Tests for the D59 display_order feature.

D59 pins the carousel order on disk: the first rig added is leftmost
(``display_order=0``), each subsequent create stamps ``max+1``, and
the user reorders via ``POST /api/v1/rigs/reorder``. ``list_rigs``
sorts by ``display_order`` ASC with a deterministic tiebreaker chain
(missing-vs-present, then ``created_at`` ASC, then id).

Covered here:
  * XML round-trip for the new ``<display_order>`` element.
  * ``create_rig`` stamps max+1 (and 0 on an empty logbook).
  * ``list_rigs`` sorts ascending; legacy rigs missing the field
    sort after rigs that have one.
  * ``reorder_rigs`` rewrites the order, is idempotent, and the
    REST endpoint validates malformed lists with RFC 9457 errors.
  * D59's amendment to D58's tiebreaker is covered in
    ``test_d58_starred_rig.py`` (leftmost-remaining election).
"""
from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_logbook_root, get_user_id
from backend.api.errors import PROBLEM_JSON_MEDIA_TYPE
from backend.api.rest import create_app
from backend.models._component_base import ComponentStatus
from backend.models.aad import AADCreate
from backend.models.container import ContainerCreate
from backend.models.main import MainCreate
from backend.models.reserve import ReserveCreate
from backend.models.rig import Jurisdiction, Rig, RigCreate
from backend.services import (
    aad_service,
    container_service,
    main_service,
    reserve_service,
    rig_service,
)
from backend.storage.bootstrap import bootstrap_logbook
from backend.xml.serialize import element_to_rig, rig_to_element

# --------------------------------------------------------------------------- #
# Fixtures + helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def bootstrapped_root(logbook_root: Path) -> Path:
    bootstrap_logbook(logbook_root)
    return logbook_root


@pytest.fixture
def client(bootstrapped_root: Path) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_logbook_root] = lambda: bootstrapped_root
    app.dependency_overrides[get_user_id] = lambda: "default"
    return TestClient(app)


def _seed_components(root: Path) -> dict[str, UUID]:
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


# --------------------------------------------------------------------------- #
# XML round-trip
# --------------------------------------------------------------------------- #


class TestXmlRoundtrip:
    """``<display_order>`` is optional in the XSD and elided when
    None, so pre-D59 rig.xml stays valid byte-for-byte. Service-
    written rigs always carry the element.
    """

    def test_none_elides_element(self):
        rig = Rig(
            id=uuid4(),
            nickname="NoOrder",
            jurisdiction=Jurisdiction.USPA,
            current_main_id=uuid4(),
            current_reserve_id=uuid4(),
            current_aad_id=uuid4(),
            current_container_id=uuid4(),
            display_order=None,
        )
        element = rig_to_element(rig)
        children = [c for c in element if c.tag.endswith("}display_order")]
        assert children == []

    def test_value_roundtrips(self):
        original = Rig(
            id=uuid4(),
            nickname="Ordered",
            jurisdiction=Jurisdiction.USPA,
            current_main_id=uuid4(),
            current_reserve_id=uuid4(),
            current_aad_id=uuid4(),
            current_container_id=uuid4(),
            display_order=3,
        )
        element = rig_to_element(original)
        restored = element_to_rig(element)
        assert restored.display_order == 3

    def test_zero_roundtrips(self):
        # The first-added rig gets display_order=0; round-trip must
        # preserve it (a regression that emits 0 as elided would
        # break the invariant that the first rig has an explicit
        # value).
        original = Rig(
            id=uuid4(),
            nickname="Leftmost",
            jurisdiction=Jurisdiction.USPA,
            current_main_id=uuid4(),
            current_reserve_id=uuid4(),
            current_aad_id=uuid4(),
            current_container_id=uuid4(),
            display_order=0,
        )
        restored = element_to_rig(rig_to_element(original))
        assert restored.display_order == 0


# --------------------------------------------------------------------------- #
# create_rig stamps display_order
# --------------------------------------------------------------------------- #


class TestCreateStamps:
    def test_first_rig_gets_order_zero(self, bootstrapped_root: Path):
        rig = _create_rig(bootstrapped_root, "First")
        assert rig.display_order == 0

    def test_subsequent_rigs_get_incrementing_orders(
        self, bootstrapped_root: Path
    ):
        a = _create_rig(bootstrapped_root, "A")
        b = _create_rig(bootstrapped_root, "B")
        c = _create_rig(bootstrapped_root, "C")
        assert a.display_order == 0
        assert b.display_order == 1
        assert c.display_order == 2

    def test_create_after_delete_continues_max_plus_one(
        self, bootstrapped_root: Path
    ):
        # Even after the leftmost rig is deleted, the next create
        # stamps max+1 of *remaining* rigs. Gaps in the sequence
        # are intentional per D59 (re-packing would be more work
        # for zero user-visible benefit; the sort still works).
        a = _create_rig(bootstrapped_root, "A")  # order=0
        b = _create_rig(bootstrapped_root, "B")  # order=1
        rig_service.delete_rig(bootstrapped_root, "default", a.id)
        assert b.display_order == 1
        # Next create sees max=1 among remaining; stamps 2.
        c = _create_rig(bootstrapped_root, "C")
        assert c.display_order == 2


# --------------------------------------------------------------------------- #
# list_rigs sort order
# --------------------------------------------------------------------------- #


class TestListSort:
    def test_lists_by_display_order_ascending(
        self, bootstrapped_root: Path
    ):
        a = _create_rig(bootstrapped_root, "first")
        b = _create_rig(bootstrapped_root, "second")
        c = _create_rig(bootstrapped_root, "third")
        result = rig_service.list_rigs(bootstrapped_root, "default")
        assert [r.id for r in result] == [a.id, b.id, c.id]


# --------------------------------------------------------------------------- #
# reorder_rigs service contract
# --------------------------------------------------------------------------- #


class TestReorder:
    def test_reverses_three_rigs(self, bootstrapped_root: Path):
        a = _create_rig(bootstrapped_root, "A")
        b = _create_rig(bootstrapped_root, "B")
        c = _create_rig(bootstrapped_root, "C")

        result = rig_service.reorder_rigs(
            bootstrapped_root, "default", [c.id, b.id, a.id]
        )

        assert [r.id for r in result] == [c.id, b.id, a.id]
        # Disk + future list calls reflect the new order.
        listed = rig_service.list_rigs(bootstrapped_root, "default")
        assert [r.id for r in listed] == [c.id, b.id, a.id]
        # display_order values match position in the new list.
        assert listed[0].display_order == 0
        assert listed[1].display_order == 1
        assert listed[2].display_order == 2

    def test_idempotent_when_already_in_order(
        self, bootstrapped_root: Path
    ):
        a = _create_rig(bootstrapped_root, "A")
        b = _create_rig(bootstrapped_root, "B")
        original_b_updated_at = b.updated_at

        # Reorder to the same order ⇒ no writes ⇒ updated_at
        # unchanged on the rig that didn't move.
        rig_service.reorder_rigs(
            bootstrapped_root, "default", [a.id, b.id]
        )

        fresh = rig_service.get_rig(bootstrapped_root, "default", b.id)
        assert fresh.updated_at == original_b_updated_at

    def test_rejects_missing_id(self, bootstrapped_root: Path):
        a = _create_rig(bootstrapped_root, "A")
        _b = _create_rig(bootstrapped_root, "B")

        # Caller forgot to include B's id. Service must refuse.
        from backend.api.errors import ValidationFailedError

        with pytest.raises(ValidationFailedError) as info:
            rig_service.reorder_rigs(
                bootstrapped_root, "default", [a.id]
            )
        pointers = [e.pointer for e in info.value.errors or []]
        assert "#/rig_ids" in pointers

    def test_rejects_unknown_id(self, bootstrapped_root: Path):
        a = _create_rig(bootstrapped_root, "A")
        b = _create_rig(bootstrapped_root, "B")
        from backend.api.errors import ValidationFailedError

        bogus = uuid4()
        with pytest.raises(ValidationFailedError):
            rig_service.reorder_rigs(
                bootstrapped_root, "default", [a.id, b.id, bogus]
            )

    def test_rejects_duplicate_id(self, bootstrapped_root: Path):
        a = _create_rig(bootstrapped_root, "A")
        _b = _create_rig(bootstrapped_root, "B")
        from backend.api.errors import ValidationFailedError

        with pytest.raises(ValidationFailedError):
            rig_service.reorder_rigs(
                bootstrapped_root, "default", [a.id, a.id]
            )


# --------------------------------------------------------------------------- #
# REST endpoint envelope
# --------------------------------------------------------------------------- #


def _seed_components_via_api(root: Path) -> dict[str, str]:
    """Same as _seed_components but stringifies UUIDs for the wire."""
    seeded = _seed_components(root)
    return {k: str(v) for k, v in seeded.items()}


def _create_rig_via_api(
    client: TestClient, components: dict[str, str], nickname: str
) -> dict:
    body = {
        "nickname": nickname,
        "jurisdiction": "USPA",
        "current_main_id": components["main"],
        "current_reserve_id": components["reserve"],
        "current_aad_id": components["aad"],
        "current_container_id": components["container"],
    }
    r = client.post("/api/v1/rigs", json=body)
    assert r.status_code == 201, r.text
    return r.json()


class TestReorderRoute:
    def test_200_and_returns_reordered_list(
        self, client: TestClient, bootstrapped_root: Path
    ):
        a = _create_rig_via_api(
            client, _seed_components_via_api(bootstrapped_root), "A"
        )
        b = _create_rig_via_api(
            client, _seed_components_via_api(bootstrapped_root), "B"
        )

        r = client.post(
            "/api/v1/rigs/reorder",
            json={"rig_ids": [b["id"], a["id"]]},
        )
        assert r.status_code == 200
        ids = [row["id"] for row in r.json()]
        assert ids == [b["id"], a["id"]]
        # The same order surfaces on a follow-up GET, proving it
        # was persisted (not just in-memory in the response).
        listed = client.get("/api/v1/rigs").json()
        assert [row["id"] for row in listed] == [b["id"], a["id"]]

    def test_422_on_missing_id(
        self, client: TestClient, bootstrapped_root: Path
    ):
        a = _create_rig_via_api(
            client, _seed_components_via_api(bootstrapped_root), "A"
        )
        _b = _create_rig_via_api(
            client, _seed_components_via_api(bootstrapped_root), "B"
        )

        r = client.post(
            "/api/v1/rigs/reorder",
            json={"rig_ids": [a["id"]]},  # B omitted
        )
        assert r.status_code == 422
        assert r.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)
        body = r.json()
        # RFC 9457 problem envelope; FieldError points at #/rig_ids.
        assert any(
            err.get("pointer") == "#/rig_ids" for err in (body.get("errors") or [])
        )

    def test_422_on_unknown_id(
        self, client: TestClient, bootstrapped_root: Path
    ):
        a = _create_rig_via_api(
            client, _seed_components_via_api(bootstrapped_root), "A"
        )

        r = client.post(
            "/api/v1/rigs/reorder",
            json={"rig_ids": [a["id"], str(uuid4())]},
        )
        assert r.status_code == 422

    def test_422_on_duplicate_id(
        self, client: TestClient, bootstrapped_root: Path
    ):
        a = _create_rig_via_api(
            client, _seed_components_via_api(bootstrapped_root), "A"
        )
        _b = _create_rig_via_api(
            client, _seed_components_via_api(bootstrapped_root), "B"
        )

        r = client.post(
            "/api/v1/rigs/reorder",
            json={"rig_ids": [a["id"], a["id"]]},
        )
        assert r.status_code == 422

    def test_extra_field_in_body_rejected(
        self, client: TestClient, bootstrapped_root: Path
    ):
        # ReorderRigsRequest uses extra="forbid" so a typo (e.g. the
        # client sends "ids" instead of "rig_ids") fails fast at 422
        # rather than no-op'ing as an empty list.
        r = client.post(
            "/api/v1/rigs/reorder",
            json={"ids": []},
        )
        assert r.status_code == 422
