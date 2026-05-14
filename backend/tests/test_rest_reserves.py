"""Integration tests for the ``/api/v1/reserves`` routes (R.1c)."""
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
        "model": "Optimum",
        "serial": "OP-987654",
        "size_sqft": 143.0,
        "date_of_manufacture": "2019-08-01",
        "repack_limit": 40,
        "ride_limit": 25,
    }
    body.update(overrides)
    return body


def _update_body(**overrides) -> dict:
    body = {
        "status": "retired",
        "manufacturer": "Performance Designs",
        "model": "Optimum",
        "serial": "OP-987654",
        "size_sqft": 143.0,
        "date_of_manufacture": "2019-08-01",
        "repack_limit": 40,
        "ride_limit": 25,
    }
    body.update(overrides)
    return body


class TestCreate:
    def test_returns_201_and_location(self, client: TestClient):
        r = client.post("/api/v1/reserves", json=_create_body())
        assert r.status_code == 201
        assert r.headers["Location"].startswith("/api/v1/reserves/")

    def test_no_jump_count_initial_field(self, client: TestClient):
        # D35 §2553: reserves have no jump_count_initial. extra="forbid"
        # rejects sending it.
        r = client.post(
            "/api/v1/reserves",
            json={**_create_body(), "jump_count_initial": 100},
        )
        assert r.status_code == 422


class TestList:
    def test_empty_returns_empty(self, client: TestClient):
        r = client.get("/api/v1/reserves")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_every_reserve(self, client: TestClient):
        ids = set()
        for n in range(3):
            r = client.post(
                "/api/v1/reserves", json=_create_body(serial=f"OP-{n}")
            )
            ids.add(r.json()["id"])
        listed = client.get("/api/v1/reserves").json()
        assert {item["id"] for item in listed} == ids


class TestGet:
    def test_returns_full_reserve(self, client: TestClient):
        created = client.post(
            "/api/v1/reserves", json=_create_body()
        ).json()
        r = client.get(f"/api/v1/reserves/{created['id']}")
        assert r.status_code == 200
        assert r.json() == created

    def test_unknown_id_returns_404(self, client: TestClient):
        r = client.get(f"/api/v1/reserves/{uuid4()}")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)


class TestUpdate:
    def test_full_replace(self, client: TestClient):
        created = client.post(
            "/api/v1/reserves", json=_create_body()
        ).json()
        r = client.put(
            f"/api/v1/reserves/{created['id']}",
            json=_update_body(model="PD Reserve"),
        )
        assert r.status_code == 200
        assert r.json()["model"] == "PD Reserve"

    def test_append_recert_extension(self, client: TestClient):
        # PUT with the existing list plus a new entry — the natural
        # append flow for the recert log.
        created = client.post(
            "/api/v1/reserves", json=_create_body()
        ).json()
        new_extension = {
            "granted_at": "2025-06-01T09:00:00.000Z",
            "extends_until": "2030-06-01",
            "granted_by": "Master Rigger A. Smith",
            "reason": "Annual factory recert",
        }
        body = _update_body(recert_extensions=[new_extension])
        r = client.put(f"/api/v1/reserves/{created['id']}", json=body)
        assert r.status_code == 200
        ext = r.json()["recert_extensions"]
        assert len(ext) == 1
        assert ext[0]["granted_by"] == "Master Rigger A. Smith"

    def test_unknown_id_returns_404(self, client: TestClient):
        r = client.put(f"/api/v1/reserves/{uuid4()}", json=_update_body())
        assert r.status_code == 404


class TestDelete:
    def test_returns_204(self, client: TestClient):
        created = client.post(
            "/api/v1/reserves", json=_create_body()
        ).json()
        r = client.delete(f"/api/v1/reserves/{created['id']}")
        assert r.status_code == 204

    def test_subsequent_get_returns_404(self, client: TestClient):
        created = client.post(
            "/api/v1/reserves", json=_create_body()
        ).json()
        client.delete(f"/api/v1/reserves/{created['id']}")
        r = client.get(f"/api/v1/reserves/{created['id']}")
        assert r.status_code == 404

    def test_unknown_id_returns_404(self, client: TestClient):
        r = client.delete(f"/api/v1/reserves/{uuid4()}")
        assert r.status_code == 404
