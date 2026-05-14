"""Integration tests for the ``/api/v1/people`` routes (D54, Phase 2c).

Mirrors test_rest_dropzones.py:

  * Happy-path HTTP shapes: POST returns 201 + Location, GET list
    returns an array of summaries, GET detail returns the full
    record, PUT returns the updated record, DELETE returns 204.
  * Error envelope matches D16: every service-layer error is
    ``application/problem+json`` with code/title/status/detail/
    request_id and the right HTTP status.
  * NotFoundError → 404, ValidationFailedError → 422.
  * D27 correlation: ``X-Request-Id`` on every response;
    body's ``request_id`` matches the header on errors.

Service-layer behaviour (NFC normalization, soft-delete, index
upsert) is covered exhaustively in test_people_service.py — these
tests pin only the HTTP surface.
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

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def bootstrapped_root(tmp_path: Path) -> Path:
    root = tmp_path / "logbook"
    bootstrap_logbook(root)
    return root


@pytest.fixture
def client(bootstrapped_root: Path) -> TestClient:
    app = create_app(mount_frontend=False)
    app.dependency_overrides[get_logbook_root] = lambda: bootstrapped_root
    app.dependency_overrides[get_user_id] = lambda: "default"
    return TestClient(app)


def _minimal_body(**overrides) -> dict:
    body: dict = {"name": "Alice"}
    body.update(overrides)
    return body


def _create(client: TestClient, body: dict | None = None) -> dict:
    """Helper: POST a person and return the response JSON."""
    r = client.post("/api/v1/people", json=body or _minimal_body())
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# POST /api/v1/people
# --------------------------------------------------------------------------- #

class TestCreate:
    def test_returns_201_and_location_header(self, client: TestClient):
        r = client.post("/api/v1/people", json=_minimal_body())
        assert r.status_code == 201
        assert "location" in {k.lower() for k in r.headers}
        body = r.json()
        assert r.headers["Location"] == f"/api/v1/people/{body['id']}"

    def test_response_body_contains_server_fields(self, client: TestClient):
        body = _create(client)
        assert "id" in body
        assert "created_at" in body
        assert "updated_at" in body
        # Caller-supplied field echoed back.
        assert body["name"] == "Alice"
        # notes defaults to None when not supplied.
        assert body["notes"] is None

    def test_with_notes(self, client: TestClient):
        body = _create(client, _minimal_body(name="Bob", notes="rigger"))
        assert body["notes"] == "rigger"

    def test_empty_name_returns_422(self, client: TestClient):
        # Pydantic min_length=1 — a 0-length name is rejected before
        # the service is reached.
        r = client.post("/api/v1/people", json=_minimal_body(name=""))
        assert r.status_code == 422

    def test_oversize_name_returns_422(self, client: TestClient):
        r = client.post(
            "/api/v1/people", json=_minimal_body(name="A" * 121)
        )
        assert r.status_code == 422

    def test_extra_field_returns_422(self, client: TestClient):
        # extra="forbid" on PersonCreate.
        r = client.post(
            "/api/v1/people", json=_minimal_body(role="packer")
        )
        assert r.status_code == 422

    def test_request_id_header_present(self, client: TestClient):
        r = client.post("/api/v1/people", json=_minimal_body())
        # D27: every response carries X-Request-Id.
        assert "x-request-id" in {k.lower() for k in r.headers}


# --------------------------------------------------------------------------- #
# GET /api/v1/people (list)
# --------------------------------------------------------------------------- #

class TestList:
    def test_empty_returns_empty_array(self, client: TestClient):
        r = client.get("/api/v1/people")
        assert r.status_code == 200
        assert r.json() == []

    def test_summary_shape(self, client: TestClient):
        # Create one — the listing returns the compact summary
        # (id + name only), not the full record.
        created = _create(client, _minimal_body(name="Charlie"))
        r = client.get("/api/v1/people")
        assert r.status_code == 200
        items = r.json()
        assert len(items) == 1
        item = items[0]
        assert item["id"] == created["id"]
        assert item["name"] == "Charlie"
        # Summary fields only — notes, created_at not in summary.
        assert "notes" not in item
        assert "created_at" not in item

    def test_ordering_is_case_insensitive(self, client: TestClient):
        # Create out-of-order to exercise the NOCASE collation.
        for name in ("bob", "Alice", "charlie"):
            _create(client, _minimal_body(name=name))
        r = client.get("/api/v1/people")
        names = [item["name"] for item in r.json()]
        assert names == ["Alice", "bob", "charlie"]

    def test_limit_and_offset(self, client: TestClient):
        for name in ("Alice", "Bob", "Charlie", "Diane"):
            _create(client, _minimal_body(name=name))
        r = client.get("/api/v1/people", params={"limit": 2, "offset": 1})
        assert r.status_code == 200
        names = [item["name"] for item in r.json()]
        assert names == ["Bob", "Charlie"]

    def test_invalid_limit_returns_422(self, client: TestClient):
        # ge=1 on the Query — 0 is rejected by FastAPI validation.
        r = client.get("/api/v1/people", params={"limit": 0})
        assert r.status_code == 422


# --------------------------------------------------------------------------- #
# GET /api/v1/people/{person_id}
# --------------------------------------------------------------------------- #

class TestGetDetail:
    def test_full_record(self, client: TestClient):
        created = _create(client, _minimal_body(name="Eve", notes="rigger"))
        r = client.get(f"/api/v1/people/{created['id']}")
        assert r.status_code == 200
        body = r.json()
        # Detail carries every field, not just the summary subset.
        assert body["id"] == created["id"]
        assert body["name"] == "Eve"
        assert body["notes"] == "rigger"
        assert body["created_at"] == created["created_at"]
        assert body["updated_at"] == created["updated_at"]

    def test_unknown_uuid_returns_404_problem_json(self, client: TestClient):
        ghost = uuid4()
        r = client.get(f"/api/v1/people/{ghost}")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)
        body = r.json()
        # D16 envelope — code is the load-bearing identifier for clients.
        assert body["status"] == 404
        assert body["code"] == "not_found"
        assert "request_id" in body
        # request_id in the body matches the header.
        assert body["request_id"] == r.headers["x-request-id"]

    def test_invalid_uuid_returns_422(self, client: TestClient):
        # Path-parameter type validation by FastAPI (not the service).
        r = client.get("/api/v1/people/not-a-uuid")
        assert r.status_code == 422


# --------------------------------------------------------------------------- #
# PUT /api/v1/people/{person_id}
# --------------------------------------------------------------------------- #

class TestUpdate:
    def test_full_replace(self, client: TestClient):
        created = _create(client, _minimal_body(name="Original"))
        r = client.put(
            f"/api/v1/people/{created['id']}",
            json={"name": "Updated", "notes": "new"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "Updated"
        assert body["notes"] == "new"
        # id and created_at preserved server-side.
        assert body["id"] == created["id"]
        assert body["created_at"] == created["created_at"]
        # updated_at refreshed (a service-side time call may yield the
        # same value at sub-ms; just assert the field is present and a
        # string. Service-layer test pins the inequality with a sleep).
        assert body["updated_at"]

    def test_unknown_uuid_returns_404(self, client: TestClient):
        ghost = uuid4()
        r = client.put(
            f"/api/v1/people/{ghost}", json={"name": "ghost"}
        )
        assert r.status_code == 404
        assert r.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)
        assert r.json()["code"] == "not_found"

    def test_empty_name_returns_422(self, client: TestClient):
        created = _create(client)
        r = client.put(
            f"/api/v1/people/{created['id']}", json={"name": ""}
        )
        assert r.status_code == 422

    def test_can_clear_notes_to_none(self, client: TestClient):
        # Full-replace semantics: omitting notes (or sending null)
        # clears any previous value.
        created = _create(client, _minimal_body(name="F", notes="old"))
        r = client.put(
            f"/api/v1/people/{created['id']}",
            json={"name": "F", "notes": None},
        )
        assert r.status_code == 200
        assert r.json()["notes"] is None


# --------------------------------------------------------------------------- #
# DELETE /api/v1/people/{person_id}
# --------------------------------------------------------------------------- #

class TestDelete:
    def test_returns_204(self, client: TestClient):
        created = _create(client)
        r = client.delete(f"/api/v1/people/{created['id']}")
        assert r.status_code == 204
        # 204 has no body.
        assert r.content == b""

    def test_subsequent_get_returns_404(self, client: TestClient):
        created = _create(client)
        client.delete(f"/api/v1/people/{created['id']}")
        r = client.get(f"/api/v1/people/{created['id']}")
        assert r.status_code == 404

    def test_subsequent_list_excludes_deleted(self, client: TestClient):
        kept = _create(client, _minimal_body(name="kept"))
        gone = _create(client, _minimal_body(name="gone"))
        client.delete(f"/api/v1/people/{gone['id']}")
        r = client.get("/api/v1/people")
        ids = {item["id"] for item in r.json()}
        assert kept["id"] in ids
        assert gone["id"] not in ids

    def test_unknown_uuid_returns_404(self, client: TestClient):
        ghost = uuid4()
        r = client.delete(f"/api/v1/people/{ghost}")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)


# --------------------------------------------------------------------------- #
# OpenAPI surface — the new routes are registered
# --------------------------------------------------------------------------- #

class TestOpenAPI:
    def test_people_routes_appear_in_schema(self, client: TestClient):
        r = client.get("/openapi.json")
        assert r.status_code == 200
        paths = r.json()["paths"]
        assert "/api/v1/people" in paths
        assert "/api/v1/people/{person_id}" in paths
        # Key methods on the collection endpoint.
        coll_methods = set(paths["/api/v1/people"].keys())
        assert {"post", "get"} <= coll_methods
        # Key methods on the item endpoint.
        item_methods = set(paths["/api/v1/people/{person_id}"].keys())
        assert {"get", "put", "delete"} <= item_methods
