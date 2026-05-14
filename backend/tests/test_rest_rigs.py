"""Integration tests for the ``/api/v1/rigs`` routes (R.2.0c.iv).

Mirrors test_rest_jumpers / test_rest_containers in shape, with the
extra D37 surface coverage that's specific to rigs:

  * 422 on a referenced component that doesn't exist or isn't active.
  * 409 ``component_already_assigned`` when a referenced component is
    on another rig.
  * 409 ``rig_nickname_conflict`` on a duplicate sanitized nickname.
  * 409 ``rig_component_swap_unsupported`` on PUT changing any of the
    four ``current_*_id`` refs.
  * Cascade clears ``assigned_rig_id`` on the four components when
    the rig is deleted.

Every error path is exercised through the real FastAPI app with the
``on_service_error`` adapter so the RFC 9457 envelope is verified end-
to-end (D16). ``X-Request-Id`` correlation per D27 is checked at each
status-code path.
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

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
from backend.models.rig import Jurisdiction
from backend.services import (
    aad_service,
    container_service,
    main_service,
    reserve_service,
)
from backend.storage.bootstrap import bootstrap_logbook


@pytest.fixture
def bootstrapped_root(tmp_path: Path) -> Path:
    root = tmp_path / "logbook"
    bootstrap_logbook(root)
    return root


@pytest.fixture
def client(bootstrapped_root: Path) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_logbook_root] = lambda: bootstrapped_root
    app.dependency_overrides[get_user_id] = lambda: "default"
    return TestClient(app)


def _seed_components(
    root: Path,
    *,
    main_status: ComponentStatus = ComponentStatus.ACTIVE,
    reserve_status: ComponentStatus = ComponentStatus.ACTIVE,
    aad_status: ComponentStatus = ComponentStatus.ACTIVE,
    container_status: ComponentStatus = ComponentStatus.ACTIVE,
) -> dict[str, str]:
    """Create one of each inventory component and return their string-form
    UUIDs (the wire format for the JSON payload).

    Ships unassigned by default; tests that need a pre-assigned
    component (e.g. for the D37 already-assigned 409) call
    ``set_assigned_rig_id`` after seeding.
    """
    main = main_service.create_main(
        root, "default", MainCreate(status=main_status, jump_count_initial=0)
    )
    reserve = reserve_service.create_reserve(
        root,
        "default",
        ReserveCreate(
            status=reserve_status,
            repack_count_initial=0,
            ride_count_initial=0,
        ),
    )
    aad = aad_service.create_aad(
        root,
        "default",
        AADCreate(
            status=aad_status,
            jump_count_initial=0,
            fire_count_initial=0,
        ),
    )
    container = container_service.create_container(
        root,
        "default",
        ContainerCreate(status=container_status, jump_count_initial=0),
    )
    return {
        "main": str(main.id),
        "reserve": str(reserve.id),
        "aad": str(aad.id),
        "container": str(container.id),
    }


def _create_body(components: dict[str, str], **overrides) -> dict:
    body = {
        "nickname": "Black Cobra",
        "jurisdiction": Jurisdiction.USPA.value,
        "current_main_id": components["main"],
        "current_reserve_id": components["reserve"],
        "current_aad_id": components["aad"],
        "current_container_id": components["container"],
    }
    body.update(overrides)
    return body


def _update_body_from(rig: dict, **overrides) -> dict:
    body = {
        "nickname": rig["nickname"],
        "jurisdiction": rig["jurisdiction"],
        "current_main_id": rig["current_main_id"],
        "current_reserve_id": rig["current_reserve_id"],
        "current_aad_id": rig["current_aad_id"],
        "current_container_id": rig["current_container_id"],
        "notes_log": list(rig.get("notes_log", []) or []),
    }
    body.update(overrides)
    return body


# --------------------------------------------------------------------------- #
# POST /api/v1/rigs
# --------------------------------------------------------------------------- #


class TestCreate:
    def test_returns_201_and_location_header(
        self, client: TestClient, bootstrapped_root: Path
    ):
        components = _seed_components(bootstrapped_root)
        r = client.post("/api/v1/rigs", json=_create_body(components))
        assert r.status_code == 201
        assert r.headers["Location"].startswith("/api/v1/rigs/")

    def test_assigns_server_id_and_timestamps(
        self, client: TestClient, bootstrapped_root: Path
    ):
        components = _seed_components(bootstrapped_root)
        r = client.post("/api/v1/rigs", json=_create_body(components))
        body = r.json()
        assert "id" in body
        assert "created_at" in body and "updated_at" in body

    def test_returns_full_rig_with_refs(
        self, client: TestClient, bootstrapped_root: Path
    ):
        components = _seed_components(bootstrapped_root)
        r = client.post("/api/v1/rigs", json=_create_body(components))
        body = r.json()
        for field, expected_id in (
            ("current_main_id", components["main"]),
            ("current_reserve_id", components["reserve"]),
            ("current_aad_id", components["aad"]),
            ("current_container_id", components["container"]),
        ):
            assert body[field] == expected_id

    def test_d37_assignment_takes_effect_via_get_component(
        self, client: TestClient, bootstrapped_root: Path
    ):
        # End-to-end: after POST /rigs, GET /containers/{id} (etc.)
        # should show the component's assigned_rig_id pointing at
        # the new rig.
        components = _seed_components(bootstrapped_root)
        r = client.post("/api/v1/rigs", json=_create_body(components))
        rig = r.json()
        cont = client.get(
            f"/api/v1/containers/{components['container']}"
        ).json()
        assert cont["assigned_rig_id"] == rig["id"]

    def test_unknown_main_id_returns_422(
        self, client: TestClient, bootstrapped_root: Path
    ):
        components = _seed_components(bootstrapped_root)
        components["main"] = str(uuid4())  # bogus
        r = client.post("/api/v1/rigs", json=_create_body(components))
        assert r.status_code == 422
        assert r.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)
        body = r.json()
        assert any(
            e["pointer"] == "#/current_main_id"
            for e in body.get("errors", [])
        )

    def test_retired_reserve_returns_422(
        self, client: TestClient, bootstrapped_root: Path
    ):
        components = _seed_components(
            bootstrapped_root, reserve_status=ComponentStatus.RETIRED
        )
        r = client.post("/api/v1/rigs", json=_create_body(components))
        assert r.status_code == 422
        body = r.json()
        assert any(
            e["pointer"] == "#/current_reserve_id"
            for e in body.get("errors", [])
        )

    def test_already_assigned_aad_returns_409(
        self, client: TestClient, bootstrapped_root: Path
    ):
        # First rig consumes a set; second rig tries to reuse the
        # AAD → 409 component_already_assigned.
        first = _seed_components(bootstrapped_root)
        client.post(
            "/api/v1/rigs", json=_create_body(first, nickname="First")
        )
        second = _seed_components(bootstrapped_root)
        second["aad"] = first["aad"]  # collision
        r = client.post(
            "/api/v1/rigs",
            json=_create_body(second, nickname="Second"),
        )
        assert r.status_code == 409
        body = r.json()
        assert body["code"] == "component_already_assigned"
        assert any(
            e["pointer"] == "#/current_aad_id"
            for e in body.get("errors", [])
        )

    def test_duplicate_nickname_returns_409(
        self, client: TestClient, bootstrapped_root: Path
    ):
        first = _seed_components(bootstrapped_root)
        client.post(
            "/api/v1/rigs", json=_create_body(first, nickname="My Rig")
        )
        second = _seed_components(bootstrapped_root)
        r = client.post(
            "/api/v1/rigs", json=_create_body(second, nickname="My Rig")
        )
        assert r.status_code == 409
        assert r.json()["code"] == "rig_nickname_conflict"

    def test_invalid_nickname_returns_422(
        self, client: TestClient, bootstrapped_root: Path
    ):
        components = _seed_components(bootstrapped_root)
        r = client.post(
            "/api/v1/rigs",
            json=_create_body(components, nickname="bad/name"),
        )
        assert r.status_code == 422
        body = r.json()
        assert any(
            e["pointer"] == "#/nickname"
            for e in body.get("errors", [])
        )

    def test_unknown_jurisdiction_returns_422(
        self, client: TestClient, bootstrapped_root: Path
    ):
        components = _seed_components(bootstrapped_root)
        r = client.post(
            "/api/v1/rigs",
            json=_create_body(components, jurisdiction="FAA"),
        )
        # Pydantic-level rejection (closed enum) → FastAPI default 422.
        assert r.status_code == 422


# --------------------------------------------------------------------------- #
# GET /api/v1/rigs (list)
# --------------------------------------------------------------------------- #


class TestList:
    def test_empty_list_returns_array(self, client: TestClient):
        r = client.get("/api/v1/rigs")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_full_records(
        self, client: TestClient, bootstrapped_root: Path
    ):
        components = _seed_components(bootstrapped_root)
        client.post("/api/v1/rigs", json=_create_body(components))
        r = client.get("/api/v1/rigs")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        first = body[0]
        # Full Rig shape, including the four refs.
        for key in (
            "id",
            "nickname",
            "jurisdiction",
            "current_main_id",
            "current_reserve_id",
            "current_aad_id",
            "current_container_id",
            "repack_history",
            "notes_log",
            "created_at",
            "updated_at",
        ):
            assert key in first

    def test_limit_query_param_validated(self, client: TestClient):
        r = client.get("/api/v1/rigs?limit=0")
        assert r.status_code == 422  # ge=1


# --------------------------------------------------------------------------- #
# GET /api/v1/rigs/{id}
# --------------------------------------------------------------------------- #


class TestGet:
    def test_returns_full_record(
        self, client: TestClient, bootstrapped_root: Path
    ):
        components = _seed_components(bootstrapped_root)
        created = client.post(
            "/api/v1/rigs", json=_create_body(components)
        ).json()
        r = client.get(f"/api/v1/rigs/{created['id']}")
        assert r.status_code == 200
        assert r.json()["id"] == created["id"]

    def test_unknown_id_returns_404_problem_json(
        self, client: TestClient
    ):
        r = client.get(f"/api/v1/rigs/{uuid4()}")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)
        body = r.json()
        assert body["code"] == "not_found"
        assert "request_id" in body


# --------------------------------------------------------------------------- #
# PUT /api/v1/rigs/{id}
# --------------------------------------------------------------------------- #


class TestUpdate:
    def test_changes_jurisdiction_returns_updated(
        self, client: TestClient, bootstrapped_root: Path
    ):
        components = _seed_components(bootstrapped_root)
        created = client.post(
            "/api/v1/rigs", json=_create_body(components)
        ).json()
        r = client.put(
            f"/api/v1/rigs/{created['id']}",
            json=_update_body_from(
                created, jurisdiction=Jurisdiction.BOTH.value
            ),
        )
        assert r.status_code == 200
        assert r.json()["jurisdiction"] == Jurisdiction.BOTH.value

    def test_nickname_change_renames_folder(
        self, client: TestClient, bootstrapped_root: Path
    ):
        components = _seed_components(bootstrapped_root)
        created = client.post(
            "/api/v1/rigs",
            json=_create_body(components, nickname="Old"),
        ).json()
        client.put(
            f"/api/v1/rigs/{created['id']}",
            json=_update_body_from(created, nickname="New"),
        )
        # The folder under rigs/ moved.
        assert (bootstrapped_root / "rigs" / "New").is_dir()
        assert not (bootstrapped_root / "rigs" / "Old").exists()

    def test_swap_main_via_put_returns_409(
        self, client: TestClient, bootstrapped_root: Path
    ):
        components = _seed_components(bootstrapped_root)
        created = client.post(
            "/api/v1/rigs", json=_create_body(components)
        ).json()
        r = client.put(
            f"/api/v1/rigs/{created['id']}",
            json=_update_body_from(
                created, current_main_id=str(uuid4())
            ),
        )
        assert r.status_code == 409
        body = r.json()
        assert body["code"] == "rig_component_swap_unsupported"
        assert any(
            e["pointer"] == "#/current_main_id"
            for e in body.get("errors", [])
        )

    def test_swap_multiple_refs_reported_together(
        self, client: TestClient, bootstrapped_root: Path
    ):
        components = _seed_components(bootstrapped_root)
        created = client.post(
            "/api/v1/rigs", json=_create_body(components)
        ).json()
        r = client.put(
            f"/api/v1/rigs/{created['id']}",
            json=_update_body_from(
                created,
                current_main_id=str(uuid4()),
                current_reserve_id=str(uuid4()),
            ),
        )
        assert r.status_code == 409
        body = r.json()
        pointers = {e["pointer"] for e in body.get("errors", [])}
        assert pointers == {"#/current_main_id", "#/current_reserve_id"}

    def test_unknown_id_returns_404(self, client: TestClient):
        # The PUT body needs valid Pydantic shape; the 404 fires
        # at the service layer before any disk write.
        r = client.put(
            f"/api/v1/rigs/{uuid4()}",
            json={
                "nickname": "X",
                "jurisdiction": "USPA",
                "current_main_id": str(uuid4()),
                "current_reserve_id": str(uuid4()),
                "current_aad_id": str(uuid4()),
                "current_container_id": str(uuid4()),
            },
        )
        assert r.status_code == 404
        assert r.json()["code"] == "not_found"

    def test_invalid_nickname_returns_422(
        self, client: TestClient, bootstrapped_root: Path
    ):
        components = _seed_components(bootstrapped_root)
        created = client.post(
            "/api/v1/rigs", json=_create_body(components)
        ).json()
        r = client.put(
            f"/api/v1/rigs/{created['id']}",
            json=_update_body_from(created, nickname="bad/name"),
        )
        assert r.status_code == 422
        body = r.json()
        assert any(
            e["pointer"] == "#/nickname"
            for e in body.get("errors", [])
        )


# --------------------------------------------------------------------------- #
# DELETE /api/v1/rigs/{id}
# --------------------------------------------------------------------------- #


class TestDelete:
    def test_returns_204(
        self, client: TestClient, bootstrapped_root: Path
    ):
        components = _seed_components(bootstrapped_root)
        created = client.post(
            "/api/v1/rigs", json=_create_body(components)
        ).json()
        r = client.delete(f"/api/v1/rigs/{created['id']}")
        assert r.status_code == 204
        assert r.text == ""

    def test_subsequent_get_returns_404(
        self, client: TestClient, bootstrapped_root: Path
    ):
        components = _seed_components(bootstrapped_root)
        created = client.post(
            "/api/v1/rigs", json=_create_body(components)
        ).json()
        client.delete(f"/api/v1/rigs/{created['id']}")
        r = client.get(f"/api/v1/rigs/{created['id']}")
        assert r.status_code == 404

    def test_d37_cascade_clears_component_assignments(
        self, client: TestClient, bootstrapped_root: Path
    ):
        # End-to-end: after DELETE /rigs/{id}, every component
        # returns to assigned_rig_id=None per D37's cascade.
        components = _seed_components(bootstrapped_root)
        created = client.post(
            "/api/v1/rigs", json=_create_body(components)
        ).json()
        # Sanity: the components are now on the rig.
        cont_before = client.get(
            f"/api/v1/containers/{components['container']}"
        ).json()
        assert cont_before["assigned_rig_id"] == created["id"]

        client.delete(f"/api/v1/rigs/{created['id']}")

        # Each of the four refs is back to assigned_rig_id=null.
        for kind, comp_id in components.items():
            kind_path = {
                "main": "mains",
                "reserve": "reserves",
                "aad": "aads",
                "container": "containers",
            }[kind]
            comp = client.get(f"/api/v1/{kind_path}/{comp_id}").json()
            assert comp["assigned_rig_id"] is None, (
                f"{kind} {comp_id} still has assigned_rig_id after "
                "rig delete"
            )

    def test_unknown_id_returns_404(self, client: TestClient):
        r = client.delete(f"/api/v1/rigs/{uuid4()}")
        assert r.status_code == 404
        assert r.json()["code"] == "not_found"


# --------------------------------------------------------------------------- #
# D27 correlation: X-Request-Id present on every response
# --------------------------------------------------------------------------- #


class TestCorrelation:
    def test_x_request_id_on_201(
        self, client: TestClient, bootstrapped_root: Path
    ):
        components = _seed_components(bootstrapped_root)
        r = client.post("/api/v1/rigs", json=_create_body(components))
        assert "x-request-id" in r.headers

    def test_x_request_id_on_404(self, client: TestClient):
        r = client.get(f"/api/v1/rigs/{uuid4()}")
        assert "x-request-id" in r.headers

    def test_x_request_id_on_409(
        self, client: TestClient, bootstrapped_root: Path
    ):
        # RigComponentSwapUnsupported path — confirm the
        # correlation header rides through ServiceError → on_service_error.
        components = _seed_components(bootstrapped_root)
        created = client.post(
            "/api/v1/rigs", json=_create_body(components)
        ).json()
        r = client.put(
            f"/api/v1/rigs/{created['id']}",
            json=_update_body_from(
                created, current_main_id=str(uuid4())
            ),
        )
        assert r.status_code == 409
        assert "x-request-id" in r.headers

    def test_x_request_id_on_204(
        self, client: TestClient, bootstrapped_root: Path
    ):
        components = _seed_components(bootstrapped_root)
        created = client.post(
            "/api/v1/rigs", json=_create_body(components)
        ).json()
        r = client.delete(f"/api/v1/rigs/{created['id']}")
        assert "x-request-id" in r.headers


# --------------------------------------------------------------------------- #
# POST /api/v1/rigs/{rig_id}/swap_main (S.2)
# --------------------------------------------------------------------------- #


def _seed_extra_main_id(root: Path) -> str:
    """Create one extra unassigned main and return its UUID string."""
    return str(
        main_service.create_main(
            root, "default", MainCreate(status=ComponentStatus.ACTIVE)
        ).id
    )


class TestSwapMain:
    """REST surface for the canopy-swap operation."""

    def test_swaps_returns_200_with_updated_rig(
        self, client: TestClient, bootstrapped_root: Path
    ):
        components = _seed_components(bootstrapped_root)
        created = client.post(
            "/api/v1/rigs", json=_create_body(components)
        ).json()
        new_main_id = _seed_extra_main_id(bootstrapped_root)

        r = client.post(
            f"/api/v1/rigs/{created['id']}/swap_main",
            json={"new_main_id": new_main_id},
        )

        assert r.status_code == 200
        body = r.json()
        assert body["current_main_id"] == new_main_id
        assert body["id"] == created["id"]

    def test_swap_persists_via_get(
        self, client: TestClient, bootstrapped_root: Path
    ):
        # The follow-up GET must reflect the swapped main, proving
        # the service layer wrote rig.xml.
        components = _seed_components(bootstrapped_root)
        created = client.post(
            "/api/v1/rigs", json=_create_body(components)
        ).json()
        new_main_id = _seed_extra_main_id(bootstrapped_root)

        client.post(
            f"/api/v1/rigs/{created['id']}/swap_main",
            json={"new_main_id": new_main_id},
        )

        fetched = client.get(f"/api/v1/rigs/{created['id']}").json()
        assert fetched["current_main_id"] == new_main_id

    def test_unknown_rig_returns_404(self, client: TestClient):
        r = client.post(
            f"/api/v1/rigs/{uuid4()}/swap_main",
            json={"new_main_id": str(uuid4())},
        )
        assert r.status_code == 404

    def test_unknown_main_returns_422(
        self, client: TestClient, bootstrapped_root: Path
    ):
        components = _seed_components(bootstrapped_root)
        created = client.post(
            "/api/v1/rigs", json=_create_body(components)
        ).json()
        r = client.post(
            f"/api/v1/rigs/{created['id']}/swap_main",
            json={"new_main_id": str(uuid4())},
        )
        assert r.status_code == 422
        problem = r.json()
        # RFC 9457 problem+json: errors array carries the field
        # pointer so the UI knows which input to highlight.
        pointers = [e["pointer"] for e in problem.get("errors", [])]
        assert "#/new_main_id" in pointers

    def test_inactive_main_returns_422(
        self, client: TestClient, bootstrapped_root: Path
    ):
        components = _seed_components(bootstrapped_root)
        created = client.post(
            "/api/v1/rigs", json=_create_body(components)
        ).json()
        # Mint a retired main directly (skipping the API since the
        # PUT path itself blocks retiring an assigned main).
        retired_main = main_service.create_main(
            bootstrapped_root,
            "default",
            MainCreate(status=ComponentStatus.RETIRED),
        )

        r = client.post(
            f"/api/v1/rigs/{created['id']}/swap_main",
            json={"new_main_id": str(retired_main.id)},
        )
        assert r.status_code == 422

    def test_main_on_other_rig_returns_409(
        self, client: TestClient, bootstrapped_root: Path
    ):
        # Create two rigs, then try to steal rig B's main.
        components_a = _seed_components(bootstrapped_root)
        rig_a = client.post(
            "/api/v1/rigs", json=_create_body(components_a)
        ).json()
        components_b = _seed_components(bootstrapped_root)
        rig_b = client.post(
            "/api/v1/rigs", json=_create_body(components_b, nickname="Red Hawk")
        ).json()

        r = client.post(
            f"/api/v1/rigs/{rig_a['id']}/swap_main",
            json={"new_main_id": rig_b["current_main_id"]},
        )
        assert r.status_code == 409
        problem = r.json()
        assert problem.get("code") == "component_already_assigned"

    def test_same_id_is_noop_returns_200(
        self, client: TestClient, bootstrapped_root: Path
    ):
        components = _seed_components(bootstrapped_root)
        created = client.post(
            "/api/v1/rigs", json=_create_body(components)
        ).json()

        r = client.post(
            f"/api/v1/rigs/{created['id']}/swap_main",
            json={"new_main_id": created["current_main_id"]},
        )
        assert r.status_code == 200
        # No write happened, so updated_at is unchanged.
        assert r.json()["updated_at"] == created["updated_at"]

    def test_extra_field_in_body_returns_422(
        self, client: TestClient, bootstrapped_root: Path
    ):
        # SwapMainRequest uses extra="forbid" so a typo doesn't
        # silently no-op as a None ref.
        components = _seed_components(bootstrapped_root)
        created = client.post(
            "/api/v1/rigs", json=_create_body(components)
        ).json()
        r = client.post(
            f"/api/v1/rigs/{created['id']}/swap_main",
            json={
                "new_main_id": str(uuid4()),
                "bogus_field": "should reject",
            },
        )
        assert r.status_code == 422

    def test_x_request_id_present(
        self, client: TestClient, bootstrapped_root: Path
    ):
        components = _seed_components(bootstrapped_root)
        created = client.post(
            "/api/v1/rigs", json=_create_body(components)
        ).json()
        new_main_id = _seed_extra_main_id(bootstrapped_root)
        r = client.post(
            f"/api/v1/rigs/{created['id']}/swap_main",
            json={"new_main_id": new_main_id},
        )
        assert "x-request-id" in r.headers


# --------------------------------------------------------------------------- #
# PUT /api/v1/rigs/{id}/star — D58 transition 2
# --------------------------------------------------------------------------- #


class TestSetStar:
    """End-to-end coverage of the dedicated star endpoint (D58).

    Service-layer behaviour is covered in test_d58_starred_rig.py; here
    we verify the HTTP envelope (status codes, RFC 9457 problem body,
    OpenAPI response model match).
    """

    def test_create_first_rig_returns_starred_true(
        self, client: TestClient, bootstrapped_root: Path
    ):
        # Wire-level proof of D58 transition 1: a brand-new logbook's
        # first rig auto-stars and the API surfaces the flag.
        components = _seed_components(bootstrapped_root)
        r = client.post("/api/v1/rigs", json=_create_body(components))
        assert r.status_code == 201
        assert r.json()["starred"] is True

    def test_put_star_moves_flag_and_returns_200(
        self, client: TestClient, bootstrapped_root: Path
    ):
        components_a = _seed_components(bootstrapped_root)
        first = client.post(
            "/api/v1/rigs", json=_create_body(components_a)
        ).json()
        components_b = _seed_components(bootstrapped_root)
        second = client.post(
            "/api/v1/rigs",
            json=_create_body(components_b, nickname="Red Hawk"),
        ).json()

        # Pre: first is starred, second isn't.
        assert first["starred"] is True
        assert second["starred"] is False

        r = client.put(f"/api/v1/rigs/{second['id']}/star")
        assert r.status_code == 200
        assert r.json()["starred"] is True
        assert r.json()["id"] == second["id"]

        # Post: the prior star is cleared on the wire too.
        first_after = client.get(f"/api/v1/rigs/{first['id']}").json()
        assert first_after["starred"] is False

    def test_put_star_idempotent(
        self, client: TestClient, bootstrapped_root: Path
    ):
        components = _seed_components(bootstrapped_root)
        rig = client.post(
            "/api/v1/rigs", json=_create_body(components)
        ).json()
        # First call should be a no-op (the rig auto-starred on
        # create); the second call too. Both return 200 with the
        # same rig payload — D58 explicitly forbids treating
        # idempotent restars as conflicts.
        r1 = client.put(f"/api/v1/rigs/{rig['id']}/star")
        r2 = client.put(f"/api/v1/rigs/{rig['id']}/star")
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["starred"] is True
        assert r2.json()["starred"] is True

    def test_put_star_unknown_id_returns_404_problem_json(
        self, client: TestClient
    ):
        r = client.put(f"/api/v1/rigs/{uuid4()}/star")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith(
            PROBLEM_JSON_MEDIA_TYPE
        )
        body = r.json()
        # RFC 9457 envelope per D16.
        assert "type" in body and "title" in body
        # X-Request-Id correlation per D27 must ride along on the
        # error response.
        assert "x-request-id" in r.headers

    def test_no_delete_endpoint_on_star(
        self, client: TestClient, bootstrapped_root: Path
    ):
        # D58: there is no DELETE /rigs/{id}/star. Attempting one
        # must return 405 (method not allowed) — and definitely not
        # 200, because that would imply we silently allow unstar.
        components = _seed_components(bootstrapped_root)
        rig = client.post(
            "/api/v1/rigs", json=_create_body(components)
        ).json()
        r = client.delete(f"/api/v1/rigs/{rig['id']}/star")
        assert r.status_code == 405
