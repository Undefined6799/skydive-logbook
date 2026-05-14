"""Integration tests for the ``/api/v1/mains`` routes (R.1d).

Mirrors test_rest_containers.py with main-specific shape: size_sqft,
default_environment, and the nested current_lineset / lineset_history.
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_logbook_root, get_user_id
from backend.api.errors import PROBLEM_JSON_MEDIA_TYPE
from backend.api.rest import create_app
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


def _create_body(**overrides) -> dict:
    body = {
        "manufacturer": "Performance Designs",
        "model": "Sabre 2",
        "serial": "S2-987654",
        "size_sqft": 170.0,
        "date_of_manufacture": "2018-06-15",
        "default_environment": "dust_sand_salt",
    }
    body.update(overrides)
    return body


def _update_body(**overrides) -> dict:
    body = {
        "status": "retired",
        "manufacturer": "Performance Designs",
        "model": "Sabre 2",
        "serial": "S2-987654",
        "size_sqft": 170.0,
        "date_of_manufacture": "2018-06-15",
        "default_environment": "dust_sand_salt",
    }
    body.update(overrides)
    return body


class TestCreate:
    def test_returns_201_and_location(self, client: TestClient):
        r = client.post("/api/v1/mains", json=_create_body())
        assert r.status_code == 201
        assert r.headers["Location"].startswith("/api/v1/mains/")

    def test_default_environment_field_used(self, client: TestClient):
        # Pin the rename from default_environment_flags to
        # default_environment (2026-04-28). A regression that brought
        # back the old name would surface as a 422 here.
        r = client.post("/api/v1/mains", json=_create_body())
        assert r.status_code == 201
        assert r.json()["default_environment"] == "dust_sand_salt"

    def test_default_environment_flags_field_rejected(
        self, client: TestClient
    ):
        # Defensive: the old field name must not be silently accepted.
        body = {**_create_body(), "default_environment_flags": "desert"}
        r = client.post("/api/v1/mains", json=body)
        assert r.status_code == 422

    def test_has_rds_default_false_in_response(self, client: TestClient):
        # D45: has_rds is service-controlled (no DELETE counterpart
        # like the rig star) but reachable via PUT/POST since the
        # field rides directly on Main/MainCreate. New mains default
        # to False, and the wire response surfaces the flag for
        # the inventory UI's chip and the future R.4 wear math.
        r = client.post("/api/v1/mains", json=_create_body())
        assert r.status_code == 201
        assert r.json()["has_rds"] is False

    def test_has_rds_true_round_trips_through_create(
        self, client: TestClient
    ):
        body = {**_create_body(), "has_rds": True}
        r = client.post("/api/v1/mains", json=body)
        assert r.status_code == 201
        assert r.json()["has_rds"] is True
        # Confirm GET returns the same flag (no transient state).
        got = client.get(f"/api/v1/mains/{r.json()['id']}")
        assert got.json()["has_rds"] is True


class TestList:
    def test_empty_returns_empty(self, client: TestClient):
        r = client.get("/api/v1/mains")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_every_main(self, client: TestClient):
        ids = set()
        for n in range(3):
            r = client.post(
                "/api/v1/mains", json=_create_body(serial=f"S2-{n}")
            )
            ids.add(r.json()["id"])
        listed = client.get("/api/v1/mains").json()
        assert {item["id"] for item in listed} == ids


class TestGet:
    def test_returns_full_main(self, client: TestClient):
        created = client.post("/api/v1/mains", json=_create_body()).json()
        r = client.get(f"/api/v1/mains/{created['id']}")
        assert r.status_code == 200
        assert r.json() == created

    def test_unknown_id_returns_404(self, client: TestClient):
        r = client.get(f"/api/v1/mains/{uuid4()}")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)


class TestUpdate:
    def test_full_replace(self, client: TestClient):
        created = client.post("/api/v1/mains", json=_create_body()).json()
        r = client.put(
            f"/api/v1/mains/{created['id']}",
            json=_update_body(model="Sabre 3"),
        )
        assert r.status_code == 200
        assert r.json()["model"] == "Sabre 3"
        assert r.json()["status"] == "retired"

    def test_lineset_round_trip_through_update(self, client: TestClient):
        # Manual reline via PUT — client builds a Main with the
        # previous lineset moved into history and a new one as
        # current. Pins the contract that the PUT writes whatever
        # the client sends; the dedicated reline workflow lands later.
        new_ls = {
            "line_type": "HMA 500",
            "breaking_strength_lb": 500.0,
            "install_date": "2026-04-28",
            "installed_by": "Master Rigger A. Smith",
            "jumps_on_lineset_initial": 0,
        }
        old_ls = {
            "line_type": "Vectran V750",
            "breaking_strength_lb": 750.0,
            "install_date": "2020-03-01",
            "installed_by": "Master Rigger A. Smith",
            "jumps_on_lineset_initial": 600,
        }
        created = client.post("/api/v1/mains", json=_create_body()).json()
        body = _update_body(
            current_lineset=new_ls,
            lineset_history=[old_ls],
        )
        r = client.put(f"/api/v1/mains/{created['id']}", json=body)
        assert r.status_code == 200
        assert r.json()["current_lineset"]["line_type"] == "HMA 500"
        assert len(r.json()["lineset_history"]) == 1
        assert r.json()["lineset_history"][0]["line_type"] == "Vectran V750"

    def test_unknown_id_returns_404(self, client: TestClient):
        r = client.put(f"/api/v1/mains/{uuid4()}", json=_update_body())
        assert r.status_code == 404

    def test_has_rds_can_be_toggled_via_put(self, client: TestClient):
        # D45 reification path: a user who didn't set the flag at
        # create time should be able to flip it later through the
        # standard update endpoint (e.g. when they install an RDS
        # mod on an existing canopy). The flag rides on MainUpdate
        # so this is just full-replace semantics.
        created = client.post("/api/v1/mains", json=_create_body()).json()
        assert created["has_rds"] is False

        r = client.put(
            f"/api/v1/mains/{created['id']}",
            json=_update_body(has_rds=True),
        )
        assert r.status_code == 200
        assert r.json()["has_rds"] is True

        # And toggle back off.
        r = client.put(
            f"/api/v1/mains/{created['id']}",
            json=_update_body(has_rds=False),
        )
        assert r.status_code == 200
        assert r.json()["has_rds"] is False


class TestDelete:
    def test_returns_204(self, client: TestClient):
        created = client.post("/api/v1/mains", json=_create_body()).json()
        r = client.delete(f"/api/v1/mains/{created['id']}")
        assert r.status_code == 204

    def test_subsequent_get_returns_404(self, client: TestClient):
        created = client.post("/api/v1/mains", json=_create_body()).json()
        client.delete(f"/api/v1/mains/{created['id']}")
        r = client.get(f"/api/v1/mains/{created['id']}")
        assert r.status_code == 404

    def test_unknown_id_returns_404(self, client: TestClient):
        r = client.delete(f"/api/v1/mains/{uuid4()}")
        assert r.status_code == 404
