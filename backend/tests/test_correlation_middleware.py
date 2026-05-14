"""Integration tests for the D27 CorrelationIdMiddleware + app wiring.

The unit tests in ``test_observability_logging.py`` cover ``JsonFormatter``
and ``configure_logging`` in isolation. These tests verify that

  * the middleware is actually reachable from the ASGI stack,
  * it binds ``request_id_var`` for the duration of the request handler,
  * the ``X-Request-Id`` response header is set and matches the body's
    ``request_id`` (closing the D16↔D27 correlation loop),
  * isolation holds across requests (no contextvar leakage, no re-use).

They sit alongside ``test_errors.py`` which exercises the problem+json
envelope end-to-end; between the two, every public-facing consequence of
wiring the middleware into ``create_app`` is under test.
"""
from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from backend.api.rest import create_app
from backend.observability.logging import (
    CorrelationIdMiddleware,
    request_id_var,
)

# --------------------------------------------------------------------------- #
# Middleware unit-ish tests (no FastAPI)
# --------------------------------------------------------------------------- #

class TestMiddlewareDirect:
    """Exercise ``CorrelationIdMiddleware.__call__`` against a minimal ASGI app."""

    @pytest.fixture(autouse=True)
    def _reset_contextvar(self):
        token = request_id_var.set(None)
        try:
            yield
        finally:
            request_id_var.reset(token)

    async def test_non_http_scope_passes_through(self):
        # Lifespan and websocket scopes must not touch the contextvar —
        # those events legitimately emit with request_id: null per D27.
        calls = []

        async def inner(scope, receive, send):
            calls.append(scope["type"])
            # A lifespan app replies to startup/shutdown; we just record.

        mw = CorrelationIdMiddleware(inner)
        await mw({"type": "lifespan"}, None, None)

        assert calls == ["lifespan"]
        # Contextvar untouched.
        assert request_id_var.get() is None

    async def test_sets_contextvar_for_duration_of_http_request(self):
        # The inner app captures what request_id_var saw — proving the
        # middleware set it *before* delegating downstream.
        seen: list[UUID | None] = []

        async def inner(scope, receive, send):
            seen.append(request_id_var.get())
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        mw = CorrelationIdMiddleware(inner)

        async def noop_send(message):
            pass

        await mw({"type": "http"}, None, noop_send)

        assert len(seen) == 1
        assert isinstance(seen[0], UUID)
        assert seen[0].version == 4
        # And reset once the request finished.
        assert request_id_var.get() is None

    async def test_resets_contextvar_on_downstream_exception(self):
        # If the route raises, we must still reset — otherwise the next
        # request on the same task picks up a stale id.
        class Boom(RuntimeError):
            pass

        async def inner(scope, receive, send):
            raise Boom("route exploded")

        mw = CorrelationIdMiddleware(inner)

        with pytest.raises(Boom):
            await mw({"type": "http"}, None, lambda _m: None)

        assert request_id_var.get() is None

    async def test_appends_x_request_id_to_response_headers(self):
        # The outbound header is how a caller without problem+json parsing
        # (a curl user, a load balancer) can still correlate a response
        # to a log line. Must be present on every http.response.start.
        captured: list[dict] = []

        async def inner(scope, receive, send):
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            })
            await send({"type": "http.response.body", "body": b""})

        async def capture_send(message):
            captured.append(message)

        mw = CorrelationIdMiddleware(inner)
        await mw({"type": "http"}, None, capture_send)

        start = captured[0]
        assert start["type"] == "http.response.start"
        header_names = [name for name, _v in start["headers"]]
        assert b"x-request-id" in header_names
        # Original content-type preserved, not clobbered.
        assert b"content-type" in header_names

    async def test_each_request_gets_a_fresh_uuid(self):
        # No cross-request state: mint a new UUID per __call__.
        ids: list[bytes] = []

        async def inner(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        async def capture(message):
            if message["type"] == "http.response.start":
                for name, value in message["headers"]:
                    if name == b"x-request-id":
                        ids.append(value)

        mw = CorrelationIdMiddleware(inner)
        await mw({"type": "http"}, None, capture)
        await mw({"type": "http"}, None, capture)

        assert len(ids) == 2
        assert ids[0] != ids[1]


# --------------------------------------------------------------------------- #
# Through FastAPI
# --------------------------------------------------------------------------- #

class TestThroughFastAPI:
    @pytest.fixture
    def app_and_client(self):
        # mount_frontend=False so the SPA catch-all doesn't swallow the
        # /_test/... routes registered below — see create_app docstring.
        app = create_app(mount_frontend=False)

        # Capture what the handler sees in request_id_var so we can assert
        # that the response header carries the same id the handler would.
        seen: dict[str, UUID] = {}

        @app.get("/_test/contextvar")
        async def read_contextvar():
            rid = request_id_var.get()
            assert rid is not None, "middleware should have bound request_id_var"
            seen["request_id"] = rid
            return {"seen": str(rid)}

        return app, TestClient(app), seen

    def test_x_request_id_header_present_on_200(self, app_and_client):
        _, client, _ = app_and_client
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        assert "x-request-id" in r.headers
        # Valid UUIDv4 string.
        assert UUID(r.headers["x-request-id"]).version == 4

    def test_header_matches_contextvar_seen_by_handler(self, app_and_client):
        # The handler reads request_id_var; the header carries the id set
        # on the outbound response. They must agree — a mismatch would
        # break D16's claim that the body's request_id == X-Request-Id.
        _, client, seen = app_and_client
        r = client.get("/_test/contextvar")
        assert r.status_code == 200
        assert r.headers["x-request-id"] == str(seen["request_id"])
        assert r.json()["seen"] == r.headers["x-request-id"]

    def test_different_requests_get_different_ids(self, app_and_client):
        _, client, _ = app_and_client
        r1 = client.get("/api/v1/health")
        r2 = client.get("/api/v1/health")
        assert r1.headers["x-request-id"] != r2.headers["x-request-id"]

    def test_contextvar_unset_between_requests(self, app_and_client):
        # After a request completes, the contextvar should be None again
        # from the perspective of code outside any request. Running two
        # requests and asserting the handler's id is fresh covers the
        # reset path — if the token weren't reset, we'd still observe a
        # new UUID on each request (middleware sets before yielding), so
        # verify via the module-level var after the client call too.
        _, client, _ = app_and_client
        r = client.get("/_test/contextvar")
        assert r.status_code == 200
        # Back in the test task: no request in flight.
        assert request_id_var.get() is None
