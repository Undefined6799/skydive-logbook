"""Integration tests for the ``/api/v1/containers`` routes (R.1a).

Mirrors test_rest_dropzones.py:

  * Happy-path HTTP shapes: POST returns 201 + Location, GET list
    returns an array of full Container records, GET detail returns
    the full record, PUT returns the updated record, DELETE returns
    204.
  * Error envelope matches D16: every error is
    ``application/problem+json`` with code/title/status/detail/
    request_id and the right HTTP status.
  * Service-layer errors bubble through ``on_service_error``:
    NotFoundError → 404, ValidationFailedError → 422.
  * D27 correlation: ``X-Request-Id`` on every response.

``app.dependency_overrides[get_logbook_root]`` redirects each test's
logbook to a pristine ``tmp_path``.
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
        "manufacturer": "Sun Path",
        "model": "Javelin Odyssey",
        "serial": "OD-12345",
        "size": "M22",
        "jump_count_initial": 750,
    }
    body.update(overrides)
    return body


def _update_body(**overrides) -> dict:
    body = {
        "status": "retired",
        "manufacturer": "Sun Path",
        "model": "Javelin Odyssey",
        "size": "M22",
        "jump_count_initial": 750,
    }
    body.update(overrides)
    return body


# --------------------------------------------------------------------------- #
# POST /api/v1/containers
# --------------------------------------------------------------------------- #

class TestCreate:
    def test_returns_201_and_location_header(self, client: TestClient):
        r = client.post("/api/v1/containers", json=_create_body())
        assert r.status_code == 201
        assert r.headers["Location"].startswith("/api/v1/containers/")

    def test_returns_full_container(self, client: TestClient):
        r = client.post("/api/v1/containers", json=_create_body())
        body = r.json()
        # Server-assigned id and timestamps land on the response.
        assert "id" in body
        assert "created_at" in body
        assert "updated_at" in body
        # Submitted fields round-trip.
        assert body["manufacturer"] == "Sun Path"
        assert body["jump_count_initial"] == 750
        # Default status is active when not specified.
        assert body["status"] == "active"

    def test_invalid_status_value_returns_422(self, client: TestClient):
        # Pydantic-level validation: "halfway_retired" isn't in the
        # ComponentStatus enum. FastAPI's default 422 handler kicks in.
        r = client.post(
            "/api/v1/containers",
            json={**_create_body(), "status": "halfway_retired"},
        )
        assert r.status_code == 422

    def test_negative_jump_count_initial_returns_422(self, client: TestClient):
        r = client.post(
            "/api/v1/containers",
            json={**_create_body(), "jump_count_initial": -1},
        )
        assert r.status_code == 422


# --------------------------------------------------------------------------- #
# GET /api/v1/containers (list)
# --------------------------------------------------------------------------- #

class TestList:
    def test_empty_returns_empty_array(self, client: TestClient):
        r = client.get("/api/v1/containers")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_every_container(self, client: TestClient):
        ids = set()
        for n in range(3):
            r = client.post(
                "/api/v1/containers",
                json=_create_body(serial=f"OD-{n}"),
            )
            ids.add(r.json()["id"])
        listed = client.get("/api/v1/containers").json()
        assert {item["id"] for item in listed} == ids

    def test_response_items_have_full_container_shape(self, client: TestClient):
        client.post("/api/v1/containers", json=_create_body())
        item = client.get("/api/v1/containers").json()[0]
        # Full Container shape — pin the contract: all fields the
        # picker UI might need are already present, no follow-up GET
        # required for v0.1.
        for key in (
            "id",
            "status",
            "manufacturer",
            "model",
            "serial",
            "size",
            "jump_count_initial",
            "created_at",
            "updated_at",
        ):
            assert key in item

    def test_limit_and_offset_query_params(self, client: TestClient):
        for n in range(5):
            client.post(
                "/api/v1/containers", json=_create_body(serial=f"S-{n}")
            )
        r = client.get("/api/v1/containers?limit=2&offset=1")
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_limit_validation(self, client: TestClient):
        # ``Query(ge=1, le=1000)`` — out-of-range returns 422.
        r = client.get("/api/v1/containers?limit=0")
        assert r.status_code == 422


# --------------------------------------------------------------------------- #
# GET /api/v1/containers/{id}
# --------------------------------------------------------------------------- #

class TestGet:
    def test_returns_full_container(self, client: TestClient):
        created = client.post(
            "/api/v1/containers", json=_create_body()
        ).json()
        r = client.get(f"/api/v1/containers/{created['id']}")
        assert r.status_code == 200
        assert r.json() == created

    def test_unknown_id_returns_404_problem_json(self, client: TestClient):
        r = client.get(f"/api/v1/containers/{uuid4()}")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)
        body = r.json()
        assert body["code"] == "not_found"
        # D27: request_id in body matches the X-Request-Id header.
        assert body["request_id"] == r.headers["x-request-id"]

    def test_malformed_uuid_returns_422(self, client: TestClient):
        # FastAPI path-param validation — not RFC 9457, but accepted
        # under the narrow reading of D16 (the *service-layer* error
        # envelope is RFC 9457; FastAPI's own body/path validation
        # still uses its default 422 shape).
        r = client.get("/api/v1/containers/not-a-uuid")
        assert r.status_code == 422


# --------------------------------------------------------------------------- #
# PUT /api/v1/containers/{id}
# --------------------------------------------------------------------------- #

class TestUpdate:
    def test_full_replace_returns_updated(self, client: TestClient):
        created = client.post(
            "/api/v1/containers", json=_create_body()
        ).json()
        r = client.put(
            f"/api/v1/containers/{created['id']}",
            json=_update_body(model="Vector V3", size="L"),
        )
        assert r.status_code == 200
        body = r.json()
        assert body["model"] == "Vector V3"
        assert body["size"] == "L"
        assert body["status"] == "retired"

    def test_preserves_id_and_created_at(self, client: TestClient):
        created = client.post(
            "/api/v1/containers", json=_create_body()
        ).json()
        updated = client.put(
            f"/api/v1/containers/{created['id']}", json=_update_body()
        ).json()
        assert updated["id"] == created["id"]
        assert updated["created_at"] == created["created_at"]

    def test_unknown_id_returns_404(self, client: TestClient):
        r = client.put(f"/api/v1/containers/{uuid4()}", json=_update_body())
        assert r.status_code == 404
        assert r.json()["code"] == "not_found"

    def test_invalid_status_returns_422(self, client: TestClient):
        created = client.post(
            "/api/v1/containers", json=_create_body()
        ).json()
        r = client.put(
            f"/api/v1/containers/{created['id']}",
            json={**_update_body(), "status": "halfway_retired"},
        )
        assert r.status_code == 422


# --------------------------------------------------------------------------- #
# DELETE /api/v1/containers/{id}
# --------------------------------------------------------------------------- #

class TestDelete:
    def test_returns_204(self, client: TestClient):
        created = client.post(
            "/api/v1/containers", json=_create_body()
        ).json()
        r = client.delete(f"/api/v1/containers/{created['id']}")
        assert r.status_code == 204
        assert r.text == ""

    def test_subsequent_get_returns_404(self, client: TestClient):
        created = client.post(
            "/api/v1/containers", json=_create_body()
        ).json()
        client.delete(f"/api/v1/containers/{created['id']}")
        r = client.get(f"/api/v1/containers/{created['id']}")
        assert r.status_code == 404

    def test_subsequent_list_omits_trashed(self, client: TestClient):
        a = client.post(
            "/api/v1/containers", json=_create_body(serial="A")
        ).json()
        b = client.post(
            "/api/v1/containers", json=_create_body(serial="B")
        ).json()
        client.delete(f"/api/v1/containers/{a['id']}")
        listed = client.get("/api/v1/containers").json()
        assert {item["id"] for item in listed} == {b["id"]}

    def test_unknown_id_returns_404(self, client: TestClient):
        r = client.delete(f"/api/v1/containers/{uuid4()}")
        assert r.status_code == 404
        assert r.json()["code"] == "not_found"


# --------------------------------------------------------------------------- #
# Correlation: X-Request-Id present on every response
# --------------------------------------------------------------------------- #

class TestCorrelation:
    def test_x_request_id_on_201(self, client: TestClient):
        r = client.post("/api/v1/containers", json=_create_body())
        assert "x-request-id" in r.headers

    def test_x_request_id_on_404(self, client: TestClient):
        r = client.get(f"/api/v1/containers/{uuid4()}")
        assert "x-request-id" in r.headers

    def test_x_request_id_on_204(self, client: TestClient):
        created = client.post(
            "/api/v1/containers", json=_create_body()
        ).json()
        r = client.delete(f"/api/v1/containers/{created['id']}")
        assert "x-request-id" in r.headers
