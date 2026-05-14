"""Integration tests for the D14 ``/api/v1/jumps`` routes (Phase 3.2–3.5).

What these tests pin down:

  * Happy-path HTTP shapes: ``POST`` (multipart, per D30) returns
    201 + ``Location``; ``GET`` list returns an array; ``GET`` detail
    returns the full jump; ``PUT`` returns the updated jump;
    ``DELETE`` returns 204.
  * Error envelope matches D16: every error is
    ``application/problem+json`` with ``code``, ``title``, ``status``,
    ``detail``, ``request_id``, and the right ``http_status``.
  * Service-layer errors bubble through the ``on_service_error``
    handler in ``rest.py`` with the right HTTP status:
    ``JumpNumberConflict`` → 409, ``NotFoundError`` → 404,
    ``ValidationFailedError`` → 422.
  * D27 correlation: ``X-Request-Id`` header is on every response;
    body's ``request_id`` on error responses matches the header.
  * D30 multipart contract: the ``jump`` field carries JSON metadata;
    ``files`` parts land on disk with server-computed SHA-256 and
    size; attachment filenames are NFC-sanitized; duplicates and
    bad filenames are 422 with pointers under ``#/files/<i>/filename``.
  * D31 PUT: metadata-only update via JSON body; attachments
    preserved; folder rename on title / jump_number change;
    ``DELETE`` moves to ``.trash/`` and subsequent GET returns 404.

``app.dependency_overrides[get_logbook_root]`` redirects each test's
logbook to a pristine ``tmp_path``; Settings is never touched, so these
tests don't care what the developer's ``config.toml`` looks like.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_logbook_root, get_user_id
from backend.api.rest import create_app
from backend.storage.bootstrap import bootstrap_logbook
from backend.storage.index import open_index

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def bootstrapped_root(tmp_path: Path) -> Path:
    root = tmp_path / "logbook"
    bootstrap_logbook(root)
    result = open_index(root)
    result.conn.close()
    return root


@pytest.fixture
def client(bootstrapped_root: Path) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_logbook_root] = lambda: bootstrapped_root
    app.dependency_overrides[get_user_id] = lambda: "default"
    return TestClient(app)


def _minimal_body(**overrides) -> dict:
    body = {
        "jump_number": 1,
        "date": "2026-04-22",
        "dropzone": "Skydive Elsinore",
        "exit_altitude_m": 4000,
        "deployment_altitude_m": 900,
    }
    body.update(overrides)
    return body


def _post(
    client: TestClient,
    body: dict | None = None,
    *,
    files: list[tuple[str, tuple[str, bytes, str | None]]] | None = None,
    jump_raw: str | None = None,
):
    """Helper: multipart POST per D30.

    ``body`` → JSON-encoded into the ``jump`` form field.
    ``jump_raw`` → overrides ``body`` with a raw string (for the
    malformed-JSON test cases).
    ``files`` → list of ``(fieldname, (filename, bytes, content_type))``
    tuples. The fieldname is always ``"files"`` for our route; the
    tuple shape is what httpx/requests expect for multipart parts.
    """
    data = {"jump": jump_raw if jump_raw is not None else json.dumps(body or {})}
    return client.post("/api/v1/jumps", data=data, files=files or [])


# --------------------------------------------------------------------------- #
# POST /api/v1/jumps — metadata-only (no files)
# --------------------------------------------------------------------------- #

class TestPost:
    def test_returns_201_and_location_header(self, client: TestClient):
        r = _post(client, _minimal_body(title="Glacier"))
        assert r.status_code == 201
        body = r.json()
        assert body["jump_number"] == 1
        assert body["title"] == "Glacier"
        assert "id" in body
        # Location header points at the new resource (REST convention).
        assert r.headers["location"] == f"/api/v1/jumps/{body['id']}"

    def test_body_is_full_jump(self, client: TestClient):
        r = _post(client, _minimal_body(jump_number=42))
        body = r.json()
        assert body["jump_number"] == 42
        assert body["dropzone"] == "Skydive Elsinore"
        assert body["exit_altitude_m"] == 4000
        # attachments defaults to empty list when no files part is sent.
        assert body["attachments"] == []

    def test_duplicate_jump_number_returns_409_problem_json(
        self, client: TestClient
    ):
        _post(client, _minimal_body(jump_number=1))
        r = _post(client, _minimal_body(jump_number=1))
        assert r.status_code == 409
        assert r.headers["content-type"].startswith(
            "application/problem+json"
        )
        body = r.json()
        assert body["code"] == "jump_number_conflict"
        assert body["status"] == 409
        assert body["errors"][0]["pointer"] == "#/jump_number"

    def test_missing_required_field_returns_422_problem_json(
        self, client: TestClient
    ):
        # D30: Pydantic validation of the inner jump field now routes
        # through our ValidationFailedError, so the body is RFC 9457
        # problem+json (not FastAPI's default envelope).
        r = _post(client, {"jump_number": 1})  # missing date, dropzone, altitudes
        assert r.status_code == 422
        assert r.headers["content-type"].startswith(
            "application/problem+json"
        )
        body = r.json()
        assert body["code"] == "validation_failed"
        # Every missing field surfaces as a pointer under #/jump/...
        pointers = {e["pointer"] for e in body["errors"]}
        assert "#/jump/date" in pointers
        assert "#/jump/dropzone" in pointers

    def test_malformed_json_returns_422_problem_json(self, client: TestClient):
        # Broken JSON in the ``jump`` field is a client error. D30
        # routes it through the same ValidationFailedError envelope
        # so clients see one error shape, not two.
        r = _post(client, jump_raw="{not-json")
        assert r.status_code == 422
        body = r.json()
        assert body["code"] == "validation_failed"
        assert body["errors"][0]["pointer"] == "#/jump"

    def test_missing_jump_field_returns_422(self, client: TestClient):
        # Omitting the required ``jump`` form field is handled by
        # FastAPI's own form-parsing layer before our route body
        # runs. That path still returns 422 (FastAPI default
        # envelope), which is acceptable for v0.1 — D16 only
        # mandates the RFC 9457 envelope for service-layer errors.
        r = client.post("/api/v1/jumps", data={}, files=[])
        assert r.status_code == 422

    def test_response_has_x_request_id_header(self, client: TestClient):
        r = _post(client, _minimal_body())
        assert "x-request-id" in r.headers

    def test_error_body_request_id_matches_header(self, client: TestClient):
        _post(client, _minimal_body(jump_number=1))
        r = _post(client, _minimal_body(jump_number=1))
        assert r.json()["request_id"] == r.headers["x-request-id"]


# --------------------------------------------------------------------------- #
# POST /api/v1/jumps — with attachments (D30)
# --------------------------------------------------------------------------- #

class TestPostWithAttachments:
    def test_single_file_happy_path(
        self, client: TestClient, bootstrapped_root: Path
    ):
        data = b"lat,lon,alt\n34.1,-117.2,4000\n"
        r = _post(
            client,
            _minimal_body(jump_number=1, title="FS"),
            files=[("files", ("track.csv", data, "text/csv"))],
        )
        assert r.status_code == 201
        body = r.json()
        assert len(body["attachments"]) == 1
        att = body["attachments"][0]
        # Server-computed hash matches the bytes client sent — D25
        # step 2 "agreement by construction".
        assert att["filename"] == "track.csv"
        assert att["sha256"] == hashlib.sha256(data).hexdigest()
        assert att["size"] == len(data)
        assert att["content_type"] == "text/csv"
        # File actually landed on disk.
        on_disk = bootstrapped_root / "jumps" / "[1] FS" / "track.csv"
        assert on_disk.read_bytes() == data

    def test_multiple_files(
        self, client: TestClient, bootstrapped_root: Path
    ):
        r = _post(
            client,
            _minimal_body(jump_number=1, title="Multi"),
            files=[
                ("files", ("a.csv", b"alpha", "text/csv")),
                ("files", ("b.csv", b"bravo", "text/csv")),
                ("files", ("c.csv", b"charlie", "text/csv")),
            ],
        )
        assert r.status_code == 201
        names = [a["filename"] for a in r.json()["attachments"]]
        assert names == ["a.csv", "b.csv", "c.csv"]
        folder = bootstrapped_root / "jumps" / "[1] Multi"
        for name, content in [("a.csv", b"alpha"), ("b.csv", b"bravo"), ("c.csv", b"charlie")]:
            assert (folder / name).read_bytes() == content

    def test_empty_files_list_is_no_attachments(self, client: TestClient):
        # Explicit empty — semantically identical to omitting the part.
        r = _post(client, _minimal_body(jump_number=1), files=[])
        assert r.status_code == 201
        assert r.json()["attachments"] == []

    def test_large_file_streaming(
        self, client: TestClient, bootstrapped_root: Path
    ):
        # 1 MiB — enough to cross starlette's default 1 MiB spool
        # threshold so the UploadFile actually spills to disk. If the
        # route buffered the bytes into memory, memory would spike but
        # correctness would still hold; we're locking down correctness
        # here.
        data = b"z" * (1 * 1024 * 1024)
        r = _post(
            client,
            _minimal_body(jump_number=1),
            files=[("files", ("big.bin", data, "application/octet-stream"))],
        )
        assert r.status_code == 201
        att = r.json()["attachments"][0]
        assert att["size"] == len(data)
        assert att["sha256"] == hashlib.sha256(data).hexdigest()
        on_disk = bootstrapped_root / "jumps" / "[1]" / "big.bin"
        assert on_disk.stat().st_size == len(data)

    def test_empty_file_valid(
        self, client: TestClient, bootstrapped_root: Path
    ):
        r = _post(
            client,
            _minimal_body(jump_number=1),
            files=[("files", ("empty.csv", b"", "text/csv"))],
        )
        assert r.status_code == 201
        att = r.json()["attachments"][0]
        assert att["size"] == 0
        assert att["sha256"] == hashlib.sha256(b"").hexdigest()
        assert (bootstrapped_root / "jumps" / "[1]" / "empty.csv").read_bytes() == b""

    def test_bad_filename_returns_422_with_pointer(self, client: TestClient):
        r = _post(
            client,
            _minimal_body(jump_number=1),
            files=[("files", ("bad/name.csv", b"x", "text/csv"))],
        )
        assert r.status_code == 422
        body = r.json()
        assert body["code"] == "validation_failed"
        assert body["errors"][0]["pointer"] == "#/files/0/filename"

    def test_windows_reserved_filename_returns_422(self, client: TestClient):
        r = _post(
            client,
            _minimal_body(jump_number=1),
            files=[("files", ("NUL.txt", b"x", "text/plain"))],
        )
        assert r.status_code == 422
        assert r.json()["errors"][0]["pointer"] == "#/files/0/filename"

    def test_duplicate_filename_returns_422(
        self, client: TestClient, bootstrapped_root: Path
    ):
        r = _post(
            client,
            _minimal_body(jump_number=1),
            files=[
                ("files", ("dup.csv", b"first", "text/csv")),
                ("files", ("dup.csv", b"second", "text/csv")),
            ],
        )
        assert r.status_code == 422
        body = r.json()
        pointers = [e["pointer"] for e in body["errors"]]
        # The second occurrence (index 1) is flagged as the duplicate.
        assert "#/files/1/filename" in pointers
        # And no half-written folder lies around from the failed call.
        assert not (bootstrapped_root / "jumps" / "[1]").exists()

    def test_get_returns_jump_with_attachments(
        self, client: TestClient, bootstrapped_root: Path
    ):
        data = b"hello"
        created = _post(
            client,
            _minimal_body(jump_number=1, title="Hi"),
            files=[("files", ("hi.txt", data, "text/plain"))],
        ).json()
        r = client.get(f"/api/v1/jumps/{created['id']}")
        assert r.status_code == 200
        fetched = r.json()
        assert fetched == created
        assert len(fetched["attachments"]) == 1
        assert fetched["attachments"][0]["sha256"] == hashlib.sha256(data).hexdigest()

    def test_filename_without_content_type(
        self, client: TestClient, bootstrapped_root: Path
    ):
        # Some clients (curl without --form "file=@x;type=y") don't
        # set a Content-Type on file parts. The attachment should
        # still persist; content_type ends up either None or a
        # default mime set by the parser. Locked as "the route
        # accepts it"; the exact resolved mime varies by client.
        r = _post(
            client,
            _minimal_body(jump_number=1),
            files=[("files", ("note.txt", b"hi"))],  # no content_type tuple element
        )
        assert r.status_code == 201
        att = r.json()["attachments"][0]
        assert att["filename"] == "note.txt"
        assert att["size"] == 2


# --------------------------------------------------------------------------- #
# GET /api/v1/jumps
# --------------------------------------------------------------------------- #

class TestList:
    def test_empty_returns_empty_list(self, client: TestClient):
        r = client.get("/api/v1/jumps")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_jump_summary_shape(self, client: TestClient):
        # Field set must match ``JumpSummary`` exactly (Pydantic
        # ``extra="forbid"`` forbids drift on the inbound side; this
        # test pins drift on the outbound side too). Updated:
        # - 2026-04-28 for R.D.3's addition of aircraft / discipline
        #   / freefall_time_s cached on the index for the JumpsLog.
        # - 2026-04-28 for R.2.2-light.d.1's addition of rig_id so
        #   the JumpsLog list view can render the main canopy per
        #   row by resolving rig_id on the client.
        _post(client, _minimal_body(jump_number=1, title="A"))
        r = client.get("/api/v1/jumps")
        body = r.json()
        assert len(body) == 1
        item = body[0]
        assert set(item) == {
            "id",
            "jump_number",
            "title",
            "date",
            "dropzone",
            "aircraft",
            "discipline",
            "freefall_time_s",
            "rig_id",
        }

    def test_ordering_reverse_chronological(self, client: TestClient):
        for n, d in [(1, "2026-01-01"), (2, "2026-03-01"), (3, "2026-02-01")]:
            _post(client, _minimal_body(jump_number=n, date=d))
        body = client.get("/api/v1/jumps").json()
        assert [i["jump_number"] for i in body] == [2, 3, 1]

    def test_limit_and_offset_query_params(self, client: TestClient):
        for n in range(1, 6):
            _post(client, _minimal_body(jump_number=n, date=f"2026-01-0{n}"))
        r = client.get("/api/v1/jumps?limit=2&offset=1")
        body = r.json()
        assert len(body) == 2
        assert [i["jump_number"] for i in body] == [4, 3]

    def test_limit_out_of_range_returns_422(self, client: TestClient):
        r = client.get("/api/v1/jumps?limit=0")
        assert r.status_code == 422

    def test_rig_id_in_summary_when_set(self, client: TestClient):
        # R.2.2-light.d.1: when a jump is created with rig_id set,
        # the list view returns it on the JumpSummary so the client
        # can resolve to a main canopy without a per-row XML read.
        from uuid import uuid4
        rig_id = str(uuid4())
        _post(client, _minimal_body(jump_number=1, rig_id=rig_id))
        body = client.get("/api/v1/jumps").json()
        assert body[0]["rig_id"] == rig_id

    def test_rig_id_null_when_unset(self, client: TestClient):
        # Quick-log path: no rig picked → JumpSummary's rig_id is null.
        _post(client, _minimal_body(jump_number=1))
        body = client.get("/api/v1/jumps").json()
        assert body[0]["rig_id"] is None


# --------------------------------------------------------------------------- #
# GET /api/v1/jumps/{id}
# --------------------------------------------------------------------------- #

class TestGet:
    def test_returns_full_jump(self, client: TestClient):
        created = _post(client, _minimal_body(jump_number=1, title="Glacier")).json()
        r = client.get(f"/api/v1/jumps/{created['id']}")
        assert r.status_code == 200
        assert r.json() == created

    def test_optional_fields_round_trip(self, client: TestClient):
        body_in = _minimal_body(
            jump_number=851,
            title="4-way FS",
            time="14:30:00",
            timezone="America/Los_Angeles",
            aircraft="Twin Otter",
            discipline="FS-4",
            freefall_time_s=55,
            notes="Funnel on exit, recovered.",
        )
        created = _post(client, body_in).json()
        fetched = client.get(f"/api/v1/jumps/{created['id']}").json()
        assert fetched == created

    def test_unknown_id_returns_404_problem_json(self, client: TestClient):
        r = client.get("/api/v1/jumps/00000000-0000-4000-8000-000000000000")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("application/problem+json")
        body = r.json()
        assert body["code"] == "not_found"
        assert body["status"] == 404

    def test_malformed_uuid_returns_422(self, client: TestClient):
        r = client.get("/api/v1/jumps/not-a-uuid")
        assert r.status_code == 422

    def test_wrong_user_sees_404(self, client: TestClient, bootstrapped_root: Path):
        created = _post(client, _minimal_body(jump_number=1)).json()
        alice_app = create_app()
        alice_app.dependency_overrides[get_logbook_root] = lambda: bootstrapped_root
        alice_app.dependency_overrides[get_user_id] = lambda: "alice"
        alice_client = TestClient(alice_app)
        r = alice_client.get(f"/api/v1/jumps/{created['id']}")
        assert r.status_code == 404


# --------------------------------------------------------------------------- #
# OpenAPI surfaces the new routes
# --------------------------------------------------------------------------- #

class TestOpenAPI:
    def test_routes_registered_in_openapi(self, client: TestClient):
        spec = client.get("/openapi.json").json()
        paths = spec["paths"]
        assert "/api/v1/jumps" in paths
        assert "/api/v1/jumps/{jump_id}" in paths
        assert "post" in paths["/api/v1/jumps"]
        assert "get" in paths["/api/v1/jumps"]
        assert "get" in paths["/api/v1/jumps/{jump_id}"]

    def test_post_documents_201_response(self, client: TestClient):
        spec = client.get("/openapi.json").json()
        responses = spec["paths"]["/api/v1/jumps"]["post"]["responses"]
        assert "201" in responses

    def test_post_advertises_multipart_request_body(self, client: TestClient):
        # D30: POST accepts multipart/form-data, not application/json.
        # A regression that switched back to json= would flip this bit.
        spec = client.get("/openapi.json").json()
        op = spec["paths"]["/api/v1/jumps"]["post"]
        content = op["requestBody"]["content"]
        assert "multipart/form-data" in content
        assert "application/json" not in content

    def test_put_delete_registered(self, client: TestClient):
        # Phase 3.5: PUT + DELETE on /{jump_id}.
        spec = client.get("/openapi.json").json()
        ops = spec["paths"]["/api/v1/jumps/{jump_id}"]
        assert "put" in ops
        assert "delete" in ops
        # PUT body is JSON per D31, not multipart.
        put_content = ops["put"]["requestBody"]["content"]
        assert "application/json" in put_content
        # DELETE documents a 204 response.
        assert "204" in ops["delete"]["responses"]


# --------------------------------------------------------------------------- #
# PUT /api/v1/jumps/{id} — D31 metadata-only update
# --------------------------------------------------------------------------- #

class TestPut:
    def test_returns_200_and_updated_jump(self, client: TestClient):
        created = _post(client, _minimal_body(jump_number=1, title="Old")).json()
        body = _minimal_body(jump_number=1, title="New", notes="edited")
        r = client.put(f"/api/v1/jumps/{created['id']}", json=body)
        assert r.status_code == 200
        updated = r.json()
        assert updated["id"] == created["id"]  # id is stable (D4)
        assert updated["title"] == "New"
        assert updated["notes"] == "edited"

    def test_persisted_via_subsequent_get(self, client: TestClient):
        created = _post(client, _minimal_body(jump_number=1, title="A")).json()
        client.put(
            f"/api/v1/jumps/{created['id']}",
            json=_minimal_body(jump_number=1, title="B"),
        )
        r = client.get(f"/api/v1/jumps/{created['id']}")
        assert r.json()["title"] == "B"

    def test_preserves_attachments(
        self, client: TestClient, bootstrapped_root: Path
    ):
        # D31 is explicit: PUT doesn't touch attachments. They must
        # survive a metadata-only edit unchanged.
        data = b"keep me"
        created = _post(
            client,
            _minimal_body(jump_number=1, title="With attachment"),
            files=[("files", ("save.txt", data, "text/plain"))],
        ).json()

        r = client.put(
            f"/api/v1/jumps/{created['id']}",
            json=_minimal_body(
                jump_number=1, title="Renamed", notes="metadata tweak"
            ),
        )
        assert r.status_code == 200
        updated = r.json()
        assert updated["attachments"] == created["attachments"]
        # File still on disk at the renamed folder.
        on_disk = bootstrapped_root / "jumps" / "[1] Renamed" / "save.txt"
        assert on_disk.read_bytes() == data

    def test_duplicate_jump_number_returns_409(self, client: TestClient):
        # Edit jump 1 to take jump 2's number — D23 conflict.
        j1 = _post(client, _minimal_body(jump_number=1, title="A")).json()
        _post(client, _minimal_body(jump_number=2, title="B"))
        r = client.put(
            f"/api/v1/jumps/{j1['id']}",
            json=_minimal_body(jump_number=2, title="A"),
        )
        assert r.status_code == 409
        assert r.headers["content-type"].startswith("application/problem+json")
        body = r.json()
        assert body["code"] == "jump_number_conflict"
        assert body["errors"][0]["pointer"] == "#/jump_number"

    def test_unknown_id_returns_404(self, client: TestClient):
        r = client.put(
            "/api/v1/jumps/00000000-0000-4000-8000-000000000000",
            json=_minimal_body(),
        )
        assert r.status_code == 404
        assert r.json()["code"] == "not_found"

    def test_malformed_uuid_returns_422(self, client: TestClient):
        r = client.put(
            "/api/v1/jumps/not-a-uuid",
            json=_minimal_body(),
        )
        assert r.status_code == 422

    def test_missing_required_field_returns_422(self, client: TestClient):
        created = _post(client, _minimal_body(jump_number=1)).json()
        # Omit `dropzone` — required.
        r = client.put(
            f"/api/v1/jumps/{created['id']}",
            json={
                "jump_number": 1,
                "date": "2026-04-22",
                "exit_altitude_m": 4000,
                "deployment_altitude_m": 900,
            },
        )
        # FastAPI's own body-validation path (JumpUpdate on a JSON
        # body). Returns 422 with FastAPI's default envelope —
        # acceptable for v0.1 since the service layer's 422 contract
        # is the RFC 9457 shape.
        assert r.status_code == 422

    def test_response_has_x_request_id(self, client: TestClient):
        created = _post(client, _minimal_body(jump_number=1)).json()
        r = client.put(
            f"/api/v1/jumps/{created['id']}",
            json=_minimal_body(jump_number=1, notes="x"),
        )
        assert "x-request-id" in r.headers


# --------------------------------------------------------------------------- #
# DELETE /api/v1/jumps/{id} — D19 soft delete
# --------------------------------------------------------------------------- #

class TestDelete:
    def test_returns_204_no_content(self, client: TestClient):
        created = _post(client, _minimal_body(jump_number=1)).json()
        r = client.delete(f"/api/v1/jumps/{created['id']}")
        assert r.status_code == 204
        # 204 means no body.
        assert r.content == b""

    def test_get_after_delete_returns_404(self, client: TestClient):
        created = _post(client, _minimal_body(jump_number=1)).json()
        client.delete(f"/api/v1/jumps/{created['id']}")
        r = client.get(f"/api/v1/jumps/{created['id']}")
        assert r.status_code == 404
        assert r.json()["code"] == "not_found"

    def test_list_after_delete_excludes(self, client: TestClient):
        c1 = _post(client, _minimal_body(jump_number=1, title="keep")).json()
        c2 = _post(client, _minimal_body(jump_number=2, title="drop")).json()
        client.delete(f"/api/v1/jumps/{c2['id']}")
        listed = client.get("/api/v1/jumps").json()
        ids = {row["id"] for row in listed}
        assert c1["id"] in ids
        assert c2["id"] not in ids

    def test_jump_number_reusable_after_delete(self, client: TestClient):
        # D23: active uniqueness namespace excludes .trash/.
        c1 = _post(client, _minimal_body(jump_number=1, title="old")).json()
        client.delete(f"/api/v1/jumps/{c1['id']}")
        r = _post(client, _minimal_body(jump_number=1, title="new"))
        assert r.status_code == 201

    def test_delete_unknown_id_returns_404(self, client: TestClient):
        r = client.delete("/api/v1/jumps/00000000-0000-4000-8000-000000000000")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("application/problem+json")

    def test_folder_moved_to_trash(
        self, client: TestClient, bootstrapped_root: Path
    ):
        created = _post(client, _minimal_body(jump_number=1, title="Bye")).json()
        client.delete(f"/api/v1/jumps/{created['id']}")
        # Original gone, trash entry present.
        assert not (bootstrapped_root / "jumps" / "[1] Bye").exists()
        trash = list((bootstrapped_root / ".trash").iterdir())
        assert len(trash) == 1
        assert trash[0].name.endswith("_[1] Bye")
