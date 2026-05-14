"""Integration tests for the ``/api/v1/dropzones`` routes (R.D.2, D44).

Mirrors test_rest_jumps.py:

  * Happy-path HTTP shapes: POST returns 201 + Location, GET list
    returns an array of summaries, GET detail returns the full
    record, PUT returns the updated record, DELETE returns 204.
  * Error envelope matches D16: every error is
    ``application/problem+json`` with code/title/status/detail/
    request_id and the right HTTP status.
  * Service-layer errors bubble through ``on_service_error``:
    NotFoundError → 404, ValidationFailedError → 422.
  * D27 correlation: ``X-Request-Id`` on every response;
    body's ``request_id`` matches the header on errors.

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
    app = create_app()
    app.dependency_overrides[get_logbook_root] = lambda: bootstrapped_root
    app.dependency_overrides[get_user_id] = lambda: "default"
    return TestClient(app)


def _minimal_body(**overrides) -> dict:
    body = {
        "name": "Skydive Elsinore",
        "city": "Lake Elsinore",
        "country": "US",
        "environment": "dust_sand_salt",
    }
    body.update(overrides)
    return body


def _create(
    client: TestClient,
    body: dict | None = None,
) -> dict:
    """Helper: POST a dropzone and return the response JSON."""
    r = client.post("/api/v1/dropzones", json=body or _minimal_body())
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# POST /api/v1/dropzones
# --------------------------------------------------------------------------- #

class TestCreate:
    def test_returns_201_and_location_header(self, client: TestClient):
        r = client.post("/api/v1/dropzones", json=_minimal_body())
        assert r.status_code == 201
        assert "location" in {k.lower() for k in r.headers}
        body = r.json()
        assert r.headers["Location"] == f"/api/v1/dropzones/{body['id']}"

    def test_response_body_contains_server_fields(self, client: TestClient):
        body = _create(client)
        # Server-assigned id and timestamps.
        assert "id" in body
        assert "created_at" in body
        assert "updated_at" in body
        # Caller-supplied fields echoed back.
        assert body["name"] == "Skydive Elsinore"
        assert body["country"] == "US"
        assert body["environment"] == "dust_sand_salt"

    def test_lowercase_country_returns_422(self, client: TestClient):
        # FastAPI's default body validation handles Pydantic-level
        # rejections (pattern fail). Status is 422; we don't pin the
        # exact body shape because FastAPI's default 422 envelope is
        # different from our problem+json. D16 only mandates RFC 9457
        # for service-layer errors — see test_jumps for the same call.
        r = client.post(
            "/api/v1/dropzones",
            json=_minimal_body(country="ca"),
        )
        assert r.status_code == 422

    def test_unknown_environment_returns_422(self, client: TestClient):
        r = client.post(
            "/api/v1/dropzones",
            json=_minimal_body(environment="tropical"),
        )
        assert r.status_code == 422

    def test_extra_field_returns_422(self, client: TestClient):
        # extra="forbid" on DropzoneCreate.
        r = client.post(
            "/api/v1/dropzones",
            json=_minimal_body(bogus="value"),
        )
        assert r.status_code == 422

    def test_persists_to_disk(
        self, client: TestClient, bootstrapped_root: Path
    ):
        body = _create(client)
        path = bootstrapped_root / "dropzones" / f"{body['id']}.xml"
        assert path.is_file()


# --------------------------------------------------------------------------- #
# GET /api/v1/dropzones
# --------------------------------------------------------------------------- #

class TestList:
    def test_empty_returns_empty_array(self, client: TestClient):
        r = client.get("/api/v1/dropzones")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_summary_shape(self, client: TestClient):
        _create(client, _minimal_body(name="Test DZ"))
        r = client.get("/api/v1/dropzones")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        item = body[0]
        # Summary projection — narrower than the full Dropzone shape.
        # D60: ``starred`` is included on the summary so the
        # LogJumpModal can find the default DZ in one round-trip.
        assert set(item) == {
            "id", "name", "city", "country", "environment", "starred",
        }

    def test_alphabetical_sort(self, client: TestClient):
        for name in ("Zulu", "alpha", "Mike"):
            _create(client, _minimal_body(name=name))
        body = client.get("/api/v1/dropzones").json()
        assert [s["name"] for s in body] == ["alpha", "Mike", "Zulu"]

    def test_limit_and_offset(self, client: TestClient):
        for n in range(5):
            _create(
                client,
                _minimal_body(name=f"DZ {n}", city=f"City {n}"),
            )
        r = client.get("/api/v1/dropzones?limit=2&offset=1")
        body = r.json()
        assert len(body) == 2
        assert [s["name"] for s in body] == ["DZ 1", "DZ 2"]

    def test_invalid_limit_returns_422(self, client: TestClient):
        # Query validation: limit must be >= 1 and <= 1000.
        r = client.get("/api/v1/dropzones?limit=0")
        assert r.status_code == 422


# --------------------------------------------------------------------------- #
# GET /api/v1/dropzones/{id}
# --------------------------------------------------------------------------- #

class TestGet:
    def test_returns_full_record(self, client: TestClient):
        created = _create(client, _minimal_body(notes="round-trip me"))
        r = client.get(f"/api/v1/dropzones/{created['id']}")
        assert r.status_code == 200
        assert r.json() == created  # full equality — no fields lost

    def test_missing_returns_404_problem_json(self, client: TestClient):
        rogue = uuid4()
        r = client.get(f"/api/v1/dropzones/{rogue}")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)
        body = r.json()
        assert body["status"] == 404
        assert body["code"] == "not_found"
        assert body["title"] == "Not Found"
        # D27 correlation: request_id in body matches header.
        assert body["request_id"] == r.headers["X-Request-Id"]

    def test_invalid_uuid_returns_422(self, client: TestClient):
        # FastAPI path-param validation rejects non-UUID strings before
        # the route handler runs. Standard FastAPI 422 (not service
        # problem+json) — same shape as the jumps endpoint.
        r = client.get("/api/v1/dropzones/not-a-uuid")
        assert r.status_code == 422


# --------------------------------------------------------------------------- #
# PUT /api/v1/dropzones/{id}
# --------------------------------------------------------------------------- #

class TestUpdate:
    def test_full_replace(self, client: TestClient):
        created = _create(client, _minimal_body(name="Old"))
        r = client.put(
            f"/api/v1/dropzones/{created['id']}",
            json={
                "name": "New",
                "city": "New City",
                "province": "QC",
                "country": "CA",
                "environment": "desert",
                "notes": "renamed",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "New"
        assert body["country"] == "CA"
        assert body["environment"] == "desert"
        assert body["province"] == "QC"
        assert body["notes"] == "renamed"

    def test_preserves_id_and_created_at(self, client: TestClient):
        created = _create(client)
        r = client.put(
            f"/api/v1/dropzones/{created['id']}",
            json=_minimal_body(name="Renamed"),
        )
        body = r.json()
        assert body["id"] == created["id"]
        assert body["created_at"] == created["created_at"]

    def test_missing_returns_404(self, client: TestClient):
        r = client.put(
            f"/api/v1/dropzones/{uuid4()}",
            json=_minimal_body(),
        )
        assert r.status_code == 404
        assert r.json()["code"] == "not_found"

    def test_invalid_payload_returns_422(self, client: TestClient):
        created = _create(client)
        r = client.put(
            f"/api/v1/dropzones/{created['id']}",
            json=_minimal_body(country="ca"),  # lowercase
        )
        assert r.status_code == 422


# --------------------------------------------------------------------------- #
# DELETE /api/v1/dropzones/{id}
# --------------------------------------------------------------------------- #

class TestDelete:
    def test_returns_204(self, client: TestClient):
        created = _create(client)
        r = client.delete(f"/api/v1/dropzones/{created['id']}")
        assert r.status_code == 204
        assert r.content == b""  # no body on 204

    def test_get_after_delete_returns_404(self, client: TestClient):
        created = _create(client)
        client.delete(f"/api/v1/dropzones/{created['id']}")
        r = client.get(f"/api/v1/dropzones/{created['id']}")
        assert r.status_code == 404

    def test_list_excludes_deleted(self, client: TestClient):
        a = _create(client, _minimal_body(name="A"))
        _create(client, _minimal_body(name="B"))
        client.delete(f"/api/v1/dropzones/{a['id']}")
        body = client.get("/api/v1/dropzones").json()
        assert [s["name"] for s in body] == ["B"]

    def test_missing_returns_404(self, client: TestClient):
        r = client.delete(f"/api/v1/dropzones/{uuid4()}")
        assert r.status_code == 404
        assert r.json()["code"] == "not_found"

    def test_does_not_cascade_to_jumps(
        self, client: TestClient, bootstrapped_root: Path
    ):
        # D44: deleting a DZ leaves jumps that reference it untouched.
        # Plant a jump.xml on disk that carries the DZ's id, then
        # confirm it survives the delete byte-for-byte.
        from datetime import date
        from uuid import UUID

        from backend.models.jump import Jump
        from backend.xml.serialize import jump_to_bytes

        created = _create(client)
        jumps_dir = bootstrapped_root / "jumps" / "[1] Test"
        jumps_dir.mkdir(parents=True, exist_ok=True)
        jump = Jump(
            jump_number=1,
            date=date(2026, 4, 27),
            dropzone="Test",
            dropzone_id=UUID(created["id"]),
            exit_altitude_m=4000,
            deployment_altitude_m=900,
        )
        jump_xml = jumps_dir / "jump.xml"
        jump_xml.write_bytes(jump_to_bytes(jump))
        original_bytes = jump_xml.read_bytes()

        r = client.delete(f"/api/v1/dropzones/{created['id']}")
        assert r.status_code == 204
        assert jump_xml.read_bytes() == original_bytes


# --------------------------------------------------------------------------- #
# PUT /api/v1/dropzones/{id}/star  (D60)
# --------------------------------------------------------------------------- #

class TestStar:
    """The dedicated star endpoint is the only mutator for the
    ``starred`` flag. Idempotent, atomic transfer, RFC 9457 404 on
    missing target.
    """

    def test_returns_200_with_starred_dropzone(self, client: TestClient):
        a = _create(client, _minimal_body(name="A"))
        b = _create(client, _minimal_body(name="B"))
        # A was auto-starred (first DZ); star B.
        r = client.put(f"/api/v1/dropzones/{b['id']}/star")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == b["id"]
        assert body["starred"] is True
        # A is now unstarred in the list projection.
        listing = {s["id"]: s for s in client.get("/api/v1/dropzones").json()}
        assert listing[a["id"]]["starred"] is False
        assert listing[b["id"]]["starred"] is True

    def test_idempotent_on_already_starred(self, client: TestClient):
        a = _create(client, _minimal_body(name="A"))
        # A was auto-starred. Star it again — no-op.
        r = client.put(f"/api/v1/dropzones/{a['id']}/star")
        assert r.status_code == 200
        assert r.json()["starred"] is True

    def test_missing_target_returns_404_problem(self, client: TestClient):
        r = client.put(f"/api/v1/dropzones/{uuid4()}/star")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)
        body = r.json()
        assert body["code"] == "not_found"


# --------------------------------------------------------------------------- #
# Cross-cutting: error envelope contract
# --------------------------------------------------------------------------- #

class TestErrorEnvelope:
    def test_404_body_shape(self, client: TestClient):
        r = client.get(f"/api/v1/dropzones/{uuid4()}")
        body = r.json()
        # RFC 9457 standard members.
        assert "type" in body
        assert "title" in body
        assert "status" in body
        assert "detail" in body
        assert "instance" in body
        # Our extension members.
        assert "code" in body
        assert "request_id" in body
        # type defaults to about:blank per RFC 9457 §3.1.1.
        assert body["type"] == "about:blank"

    def test_request_id_header_on_success(self, client: TestClient):
        r = client.get("/api/v1/dropzones")
        assert "x-request-id" in {k.lower() for k in r.headers}

    def test_request_id_header_on_404(self, client: TestClient):
        r = client.get(f"/api/v1/dropzones/{uuid4()}")
        assert "x-request-id" in {k.lower() for k in r.headers}
