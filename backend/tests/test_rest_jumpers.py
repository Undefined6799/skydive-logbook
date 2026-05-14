"""Integration tests for the ``/api/v1/jumpers`` routes (R.2.0c.i).

Mirrors test_rest_containers.py / test_rest_dropzones.py:

  * Happy-path HTTP shapes: POST returns 201 + Location, GET list
    returns an array of full Jumper records, GET detail returns
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

from datetime import UTC, datetime
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
        "name": "Alex Tester",
        "exit_weight_lb": 200.0,
    }
    body.update(overrides)
    return body


def _update_body(**overrides) -> dict:
    body = {
        "name": "Alex Tester",
        "exit_weight_lb": 200.0,
    }
    body.update(overrides)
    return body


# --------------------------------------------------------------------------- #
# POST /api/v1/jumpers
# --------------------------------------------------------------------------- #


class TestCreate:
    def test_returns_201_and_location_header(self, client: TestClient):
        r = client.post("/api/v1/jumpers", json=_create_body())
        assert r.status_code == 201
        assert r.headers["Location"].startswith("/api/v1/jumpers/")

    def test_assigns_server_id_and_timestamps(self, client: TestClient):
        r = client.post("/api/v1/jumpers", json=_create_body())
        body = r.json()
        assert "id" in body
        assert "created_at" in body and "updated_at" in body

    def test_auto_stamps_exit_weight_updated_at(self, client: TestClient):
        # No explicit date → server stamps to today's UTC date.
        r = client.post("/api/v1/jumpers", json=_create_body())
        body = r.json()
        today = datetime.now(UTC).date().isoformat()
        assert body["exit_weight_updated_at"] == today

    def test_minimal_body_only_exit_weight(self, client: TestClient):
        # name is optional; exit_weight_lb is required.
        r = client.post("/api/v1/jumpers", json={"exit_weight_lb": 180.0})
        assert r.status_code == 201
        assert r.json()["name"] is None

    def test_zero_exit_weight_returns_422(self, client: TestClient):
        # Pydantic gt=0 rejects zero before the route ever runs.
        r = client.post(
            "/api/v1/jumpers", json={"exit_weight_lb": 0}
        )
        assert r.status_code == 422

    def test_rejects_unknown_field(self, client: TestClient):
        r = client.post(
            "/api/v1/jumpers", json={**_create_body(), "height_in": 72}
        )
        assert r.status_code == 422


# --------------------------------------------------------------------------- #
# GET /api/v1/jumpers (list)
# --------------------------------------------------------------------------- #


class TestList:
    def test_empty_list_returns_array(self, client: TestClient):
        r = client.get("/api/v1/jumpers")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_full_records(self, client: TestClient):
        client.post("/api/v1/jumpers", json=_create_body())
        r = client.get("/api/v1/jumpers")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        # Full Jumper shape, not a summary.
        first = body[0]
        for key in (
            "id",
            "name",
            "exit_weight_lb",
            "exit_weight_updated_at",
            "created_at",
            "updated_at",
        ):
            assert key in first

    def test_limit_query_param_validated(self, client: TestClient):
        r = client.get("/api/v1/jumpers?limit=0")
        assert r.status_code == 422  # ge=1


# --------------------------------------------------------------------------- #
# GET /api/v1/jumpers/{id}
# --------------------------------------------------------------------------- #


class TestGet:
    def test_returns_full_record(self, client: TestClient):
        created = client.post(
            "/api/v1/jumpers", json=_create_body()
        ).json()
        r = client.get(f"/api/v1/jumpers/{created['id']}")
        assert r.status_code == 200
        assert r.json()["id"] == created["id"]

    def test_unknown_id_returns_404_problem_json(
        self, client: TestClient
    ):
        r = client.get(f"/api/v1/jumpers/{uuid4()}")
        assert r.status_code == 404
        # RFC 9457 envelope.
        assert r.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)
        body = r.json()
        assert body["code"] == "not_found"
        assert body["status"] == 404
        assert "request_id" in body


# --------------------------------------------------------------------------- #
# PUT /api/v1/jumpers/{id}
# --------------------------------------------------------------------------- #


class TestUpdate:
    def test_full_replace_returns_updated(self, client: TestClient):
        created = client.post(
            "/api/v1/jumpers", json=_create_body()
        ).json()
        r = client.put(
            f"/api/v1/jumpers/{created['id']}",
            json=_update_body(name="Renamed", exit_weight_lb=205.0),
        )
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "Renamed"
        assert body["exit_weight_lb"] == 205.0

    def test_preserves_id_and_created_at(self, client: TestClient):
        created = client.post(
            "/api/v1/jumpers", json=_create_body()
        ).json()
        updated = client.put(
            f"/api/v1/jumpers/{created['id']}", json=_update_body()
        ).json()
        assert updated["id"] == created["id"]
        assert updated["created_at"] == created["created_at"]

    def test_weight_change_auto_bumps_date(self, client: TestClient):
        # Created with explicit historic date → update changes weight
        # without supplying a date → server bumps to today.
        created = client.post(
            "/api/v1/jumpers",
            json={**_create_body(), "exit_weight_updated_at": "2025-01-01"},
        ).json()
        assert created["exit_weight_updated_at"] == "2025-01-01"
        r = client.put(
            f"/api/v1/jumpers/{created['id']}",
            json=_update_body(exit_weight_lb=210.0),
        )
        body = r.json()
        today = datetime.now(UTC).date().isoformat()
        assert body["exit_weight_updated_at"] == today

    def test_metadata_only_edit_preserves_date(self, client: TestClient):
        # Same weight + no explicit date → on-disk date is preserved.
        created = client.post(
            "/api/v1/jumpers",
            json={**_create_body(), "exit_weight_updated_at": "2025-01-01"},
        ).json()
        r = client.put(
            f"/api/v1/jumpers/{created['id']}",
            json=_update_body(name="Renamed"),
        )
        body = r.json()
        assert body["exit_weight_updated_at"] == "2025-01-01"

    def test_explicit_date_wins(self, client: TestClient):
        created = client.post(
            "/api/v1/jumpers",
            json={**_create_body(), "exit_weight_updated_at": "2025-01-01"},
        ).json()
        r = client.put(
            f"/api/v1/jumpers/{created['id']}",
            json={
                **_update_body(exit_weight_lb=210.0),
                "exit_weight_updated_at": "2025-06-15",
            },
        )
        body = r.json()
        assert body["exit_weight_updated_at"] == "2025-06-15"

    def test_unknown_id_returns_404(self, client: TestClient):
        r = client.put(
            f"/api/v1/jumpers/{uuid4()}", json=_update_body()
        )
        assert r.status_code == 404
        assert r.json()["code"] == "not_found"

    def test_zero_weight_returns_422(self, client: TestClient):
        created = client.post(
            "/api/v1/jumpers", json=_create_body()
        ).json()
        r = client.put(
            f"/api/v1/jumpers/{created['id']}",
            json={**_update_body(), "exit_weight_lb": 0},
        )
        assert r.status_code == 422


# --------------------------------------------------------------------------- #
# DELETE /api/v1/jumpers/{id}
# --------------------------------------------------------------------------- #


class TestDelete:
    def test_returns_204(self, client: TestClient):
        created = client.post(
            "/api/v1/jumpers", json=_create_body()
        ).json()
        r = client.delete(f"/api/v1/jumpers/{created['id']}")
        assert r.status_code == 204
        assert r.text == ""

    def test_subsequent_get_returns_404(self, client: TestClient):
        created = client.post(
            "/api/v1/jumpers", json=_create_body()
        ).json()
        client.delete(f"/api/v1/jumpers/{created['id']}")
        r = client.get(f"/api/v1/jumpers/{created['id']}")
        assert r.status_code == 404

    def test_subsequent_list_omits_trashed(self, client: TestClient):
        a = client.post(
            "/api/v1/jumpers", json=_create_body(name="A")
        ).json()
        b = client.post(
            "/api/v1/jumpers", json=_create_body(name="B")
        ).json()
        client.delete(f"/api/v1/jumpers/{a['id']}")
        listed = client.get("/api/v1/jumpers").json()
        assert {item["id"] for item in listed} == {b["id"]}

    def test_unknown_id_returns_404(self, client: TestClient):
        r = client.delete(f"/api/v1/jumpers/{uuid4()}")
        assert r.status_code == 404
        assert r.json()["code"] == "not_found"


# --------------------------------------------------------------------------- #
# Correlation: X-Request-Id present on every response (D27)
# --------------------------------------------------------------------------- #


class TestCorrelation:
    def test_x_request_id_on_201(self, client: TestClient):
        r = client.post("/api/v1/jumpers", json=_create_body())
        assert "x-request-id" in r.headers

    def test_x_request_id_on_404(self, client: TestClient):
        r = client.get(f"/api/v1/jumpers/{uuid4()}")
        assert "x-request-id" in r.headers

    def test_x_request_id_on_204(self, client: TestClient):
        created = client.post(
            "/api/v1/jumpers", json=_create_body()
        ).json()
        r = client.delete(f"/api/v1/jumpers/{created['id']}")
        assert "x-request-id" in r.headers
