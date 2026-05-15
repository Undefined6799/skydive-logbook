"""Pin RFC 9457 envelope uniformity for routing-level failures (Slice 5).

Before Slice 5, FastAPI's ``RequestValidationError`` and Starlette's
``HTTPException`` retained their default handlers — non-RFC-9457
JSON envelopes with shape ``{"detail": ...}``. The catch-all
``@app.exception_handler(Exception)`` only fired for unhandled
exceptions outside the typed hierarchy, so a 404 on an unknown URL
or a 422 on a bad path-param produced a different wire body than a
404 raised by ``jump_service.get_jump``.

This test suite pins the post-Slice-5 contract: every 4xx and 5xx
returns ``application/problem+json`` with the documented extension
members (``code``, ``request_id``, ``instance``).
"""
from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.errors import PROBLEM_JSON_MEDIA_TYPE
from backend.api.rest import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A live app with the real /api/v1 routers mounted.

    ``mount_frontend=False`` keeps the catch-all StaticFiles mount
    from swallowing the /api/v1/unknown-path probe used by the
    404 test below — without it the SPA's html=True mount returns
    the index page on miss, not a 404.
    """
    app = create_app(mount_frontend=False)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _assert_problem_envelope(resp, *, expected_code: str, expected_status: int) -> None:
    """Common assertions for an RFC 9457 problem+json response."""
    assert resp.status_code == expected_status
    # Content type — RFC 9457 §3 explicitly requires
    # application/problem+json (no charset parameter per RFC 8259).
    assert resp.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE), (
        f"expected problem+json, got {resp.headers['content-type']!r}"
    )
    body = resp.json()
    # Standard members.
    assert body["type"] == "about:blank"
    assert body["status"] == expected_status
    assert isinstance(body["title"], str) and body["title"]
    assert isinstance(body["detail"], str) and body["detail"]
    assert body["instance"]
    # Extensions per D16.
    assert body["code"] == expected_code
    assert body["request_id"]
    # request_id correlates to the header.
    assert resp.headers["X-Request-Id"] == body["request_id"]


# --------------------------------------------------------------------------- #
# RequestValidationError → problem+json
# --------------------------------------------------------------------------- #


def test_invalid_uuid_in_path_returns_problem_json(client: TestClient) -> None:
    """Hitting a UUID path-param route with a non-UUID string used to
    yield FastAPI's default ``{"detail": [...]}`` envelope (422).
    Slice 5 normalises it to RFC 9457 with ``code=validation_failed``.
    """
    resp = client.get("/api/v1/jumps/not-a-uuid")
    _assert_problem_envelope(resp, expected_code="validation_failed", expected_status=422)
    # ``errors`` array carries the per-field detail with an RFC 6901
    # pointer; the loc tuple for a path param is ("path", "jump_id").
    errors = resp.json().get("errors")
    assert errors and isinstance(errors, list)
    pointers = [e["pointer"] for e in errors]
    assert any("jump_id" in p for p in pointers), pointers


def test_invalid_query_param_returns_problem_json(client: TestClient) -> None:
    """``limit`` is constrained ``ge=1, le=10000`` on /jumps. A negative
    value fails Pydantic at the query-layer and used to return the
    default 422 envelope; now problem+json.
    """
    resp = client.get("/api/v1/jumps?limit=-1")
    _assert_problem_envelope(resp, expected_code="validation_failed", expected_status=422)


def test_missing_required_multipart_field_returns_problem_json(
    client: TestClient,
) -> None:
    """``POST /api/v1/jumps`` requires a ``jump`` form field. Omitting
    it fails FastAPI's body validation before the handler runs.
    """
    # Multipart with no parts — both ``jump`` and ``files`` are
    # absent. ``jump`` is required; ``files`` is optional.
    resp = client.post("/api/v1/jumps", files={})
    _assert_problem_envelope(resp, expected_code="validation_failed", expected_status=422)


# --------------------------------------------------------------------------- #
# StarletteHTTPException → problem+json
# --------------------------------------------------------------------------- #


def test_unknown_path_returns_problem_json_404(client: TestClient) -> None:
    """Starlette's default ``HTTPException(404)`` for an unknown URL
    used to return ``{"detail": "Not Found"}``. Now problem+json
    with ``code=not_found`` (the same constant the service layer
    raises for "this id doesn't exist" — consumers branch on one
    value, not two).
    """
    resp = client.get("/api/v1/nonexistent-endpoint")
    _assert_problem_envelope(resp, expected_code="not_found", expected_status=404)


def test_method_not_allowed_returns_problem_json_405(client: TestClient) -> None:
    """A known path with a wrong method (Starlette returns 405).
    /api/v1/health only accepts GET — POST to it.
    """
    resp = client.post("/api/v1/health")
    _assert_problem_envelope(
        resp, expected_code="method_not_allowed", expected_status=405
    )


# --------------------------------------------------------------------------- #
# ServiceError path unchanged
# --------------------------------------------------------------------------- #


def test_service_error_still_returns_problem_json(client: TestClient) -> None:
    """A 404 from the service layer (existing path) must produce the
    same wire shape as the StarletteHTTPException 404 above —
    same code, same envelope, same extensions.

    Regression guard: the new HTTPException handler must not break
    the existing typed-error path.
    """
    nonexistent = uuid4()
    resp = client.get(f"/api/v1/jumps/{nonexistent}")
    _assert_problem_envelope(resp, expected_code="not_found", expected_status=404)
