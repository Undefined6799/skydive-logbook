"""Integration tests for the ``/api/v1/aads`` routes (R.1b).

Mirrors test_rest_containers.py with AAD-specific shape (manufacturer,
mode, is_changeable_mode, jump_count_initial, fire_count_initial).
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
        "manufacturer": "Airtec",
        "model": "Cypres 2",
        "serial": "C2-987654",
        "date_of_manufacture": "2017-03-12",
        "mode": "Pro",
        "is_changeable_mode": True,
        "jump_count_initial": 420,
    }
    body.update(overrides)
    return body


def _update_body(**overrides) -> dict:
    body = {
        "status": "retired",
        "manufacturer": "Airtec",
        "model": "Cypres 2",
        "serial": "C2-987654",
        "date_of_manufacture": "2017-03-12",
        "mode": "Pro",
        "is_changeable_mode": True,
        "jump_count_initial": 420,
    }
    body.update(overrides)
    return body


class TestCreate:
    def test_returns_201_and_location(self, client: TestClient):
        r = client.post("/api/v1/aads", json=_create_body())
        assert r.status_code == 201
        assert r.headers["Location"].startswith("/api/v1/aads/")

    def test_manufacturer_field_used_not_brand(self, client: TestClient):
        # Pin D34's 2026-04-28 amendment: AAD's maker field is
        # ``manufacturer``, not ``brand``. A regression that renamed
        # back to ``brand`` would surface as a 422 here (Pydantic
        # extra="forbid" rejects unknown fields).
        body = {**_create_body()}
        r = client.post("/api/v1/aads", json=body)
        assert r.status_code == 201
        assert r.json()["manufacturer"] == "Airtec"

    def test_brand_field_rejected(self, client: TestClient):
        # Defensive: sending the old field name "brand" must not
        # silently accept-and-drop. extra="forbid" catches it.
        body = {**_create_body(), "brand": "Airtec"}
        r = client.post("/api/v1/aads", json=body)
        assert r.status_code == 422


class TestList:
    def test_empty_returns_empty_array(self, client: TestClient):
        r = client.get("/api/v1/aads")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_every_aad(self, client: TestClient):
        ids = set()
        for n in range(3):
            r = client.post(
                "/api/v1/aads", json=_create_body(serial=f"C2-{n}")
            )
            ids.add(r.json()["id"])
        listed = client.get("/api/v1/aads").json()
        assert {item["id"] for item in listed} == ids


class TestGet:
    def test_returns_full_aad(self, client: TestClient):
        created = client.post("/api/v1/aads", json=_create_body()).json()
        r = client.get(f"/api/v1/aads/{created['id']}")
        assert r.status_code == 200
        assert r.json() == created

    def test_unknown_id_returns_404_problem_json(self, client: TestClient):
        r = client.get(f"/api/v1/aads/{uuid4()}")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)
        assert r.json()["code"] == "not_found"


class TestUpdate:
    def test_full_replace(self, client: TestClient):
        created = client.post("/api/v1/aads", json=_create_body()).json()
        r = client.put(
            f"/api/v1/aads/{created['id']}",
            json=_update_body(mode="Expert"),
        )
        assert r.status_code == 200
        assert r.json()["mode"] == "Expert"
        assert r.json()["status"] == "retired"

    def test_preserves_id_and_created_at(self, client: TestClient):
        created = client.post("/api/v1/aads", json=_create_body()).json()
        updated = client.put(
            f"/api/v1/aads/{created['id']}", json=_update_body()
        ).json()
        assert updated["id"] == created["id"]
        assert updated["created_at"] == created["created_at"]

    def test_unknown_id_returns_404(self, client: TestClient):
        r = client.put(f"/api/v1/aads/{uuid4()}", json=_update_body())
        assert r.status_code == 404


class TestDelete:
    def test_returns_204(self, client: TestClient):
        created = client.post("/api/v1/aads", json=_create_body()).json()
        r = client.delete(f"/api/v1/aads/{created['id']}")
        assert r.status_code == 204
        assert r.text == ""

    def test_subsequent_get_returns_404(self, client: TestClient):
        created = client.post("/api/v1/aads", json=_create_body()).json()
        client.delete(f"/api/v1/aads/{created['id']}")
        r = client.get(f"/api/v1/aads/{created['id']}")
        assert r.status_code == 404

    def test_unknown_id_returns_404(self, client: TestClient):
        r = client.delete(f"/api/v1/aads/{uuid4()}")
        assert r.status_code == 404
