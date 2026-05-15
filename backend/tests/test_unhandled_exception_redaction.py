"""Settings-gated redaction of unhandled-exception detail (D67).

The catch-all ``@app.exception_handler(Exception)`` in ``rest.py`` used
to include ``f"{type(exc).__name__}: {exc}"`` in every 500 response
body unconditionally. That's useful in a loopback desktop session
where the user is the only client and the path inside the message is
their own machine — but it leaks file paths, library internals, and
data field names onto the wire as soon as the API is exposed beyond
loopback.

The fix added ``Settings.expose_internal_errors``: True auto-defaults
on loopback, False otherwise; explicit values override. This test
suite pins both halves of the contract: redacted body on False,
visible detail on True, full traceback always reaches the log via
``exc_info`` (covered indirectly by the existing logging tests; we
just verify the wire here).
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_settings
from backend.api.rest import create_app
from backend.config import Settings


def _build_client(expose: bool) -> Iterator[TestClient]:
    """Build an app whose ``get_settings`` dependency returns a Settings
    with the given ``expose_internal_errors`` value.

    Registers a deliberately-failing ``/_test/explode`` route on the
    returned app (mount_frontend=False keeps the static catch-all
    from swallowing the path before the route can match).
    """
    app = create_app(mount_frontend=False)

    # Build a Settings whose flag is explicit. The auto-resolver only
    # fires when expose_internal_errors is None.
    overridden = Settings(expose_internal_errors=expose)

    app.dependency_overrides[get_settings] = lambda: overridden

    @app.get("/_test/explode")
    async def _explode() -> None:  # pyright: ignore[reportUnusedFunction]
        # The path string is the bait we'll look for in the body.
        raise FileNotFoundError(
            "/home/alice/MySkydiveLogbook/jumps/[42]/jump.xml"
        )

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def client_redacted() -> Iterator[TestClient]:
    """expose_internal_errors=False — production-safe posture."""
    yield from _build_client(expose=False)


@pytest.fixture
def client_verbose() -> Iterator[TestClient]:
    """expose_internal_errors=True — loopback desktop posture."""
    yield from _build_client(expose=True)


def test_redacted_body_contains_no_path_or_user_or_exception_type(
    client_redacted: TestClient,
) -> None:
    """With the flag off, the wire body must not leak the FileNotFoundError
    message contents — neither the path, nor the home-dir username,
    nor the exception class name.
    """
    resp = client_redacted.get("/_test/explode")
    assert resp.status_code == 500
    # Content type stays problem+json.
    assert resp.headers["content-type"].startswith(
        "application/problem+json"
    )
    body = resp.json()
    detail = body["detail"]

    # The message must not name the user's home directory.
    assert "alice" not in detail
    assert "/home/" not in detail
    # And must not name the file path inside the exception.
    assert "jump.xml" not in detail
    assert "MySkydiveLogbook" not in detail
    # And must not name the exception class.
    assert "FileNotFoundError" not in detail
    # The generic body still includes the request_id so an operator
    # can grep the log.
    assert "request_id" in detail.lower()
    assert body["request_id"]
    # request_id matching the body should also be on the response
    # header per D27.
    assert resp.headers["X-Request-Id"] == body["request_id"]


def test_verbose_body_contains_exception_type_and_message(
    client_verbose: TestClient,
) -> None:
    """With the flag on, the wire body contains the bait.

    Verifies the contract on the other side: a loopback dev user
    sees the same diagnostic information they did before the gating
    landed.
    """
    resp = client_verbose.get("/_test/explode")
    assert resp.status_code == 500
    body = resp.json()
    detail = body["detail"]
    # The wire body now carries both halves of ``f"{type(exc).__name__}: {exc}"``.
    assert "FileNotFoundError" in detail
    assert "/home/alice/MySkydiveLogbook/jumps/[42]/jump.xml" in detail


def test_problem_json_envelope_is_unchanged_either_way(
    client_redacted: TestClient,
    client_verbose: TestClient,
) -> None:
    """Either flag value still produces a complete RFC 9457 envelope.

    Guards against an accidental break where the redaction path drops
    a required field (title / status / code / request_id).
    """
    for client in (client_redacted, client_verbose):
        body = client.get("/_test/explode").json()
        assert body["type"] == "about:blank"
        assert body["title"] == "Internal Server Error"
        assert body["status"] == 500
        assert body["code"] == "internal_error"
        # request_id is a UUID string.
        assert body["request_id"]
        # detail is present and non-empty (just different content).
        assert body["detail"]
