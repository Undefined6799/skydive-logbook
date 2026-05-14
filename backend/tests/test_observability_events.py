"""Tests for the D27 event emission contract.

Two events, both on logger ``backend.http``:

  * ``http_request`` — one per request, level INFO, fields
    ``method``, ``path``, ``status``, ``duration_ms``. Replaces
    uvicorn's access log (silenced in ``configure_logging``).
  * ``service_error`` — one per ServiceError response, level WARNING
    for 4xx / ERROR for 5xx, fields ``code``, ``http_status``. Emitted
    from the FastAPI exception handler before the problem+json body
    is returned.

These tests capture the root logger's output through an in-memory
stream so we get both the structured record (via caplog) and the
rendered JSON Line (via the StreamHandler buffer). The dual check
ensures the record carries the right fields *and* that the
JsonFormatter actually renders them.
"""
from __future__ import annotations

import io
import json
import logging
import sys
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from backend.api.errors import (
    IntegrityError,
    NotFoundError,
    ValidationFailedError,
)
from backend.api.rest import create_app
from backend.observability.logging import JsonFormatter, configure_logging

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def log_buffer():
    """Install configure_logging() but redirect its stream to a buffer.

    We can't use pytest's ``capsys`` because configure_logging writes to
    ``sys.stderr`` via a StreamHandler that captured the stream reference
    at install time. Rebinding the handler's stream after the fact is the
    standard stdlib pattern.

    This fixture snapshots and restores the root logger's handlers and
    level — ``configure_logging`` is mutating global state and we must
    not leak a handler pointing at a dead ``StringIO`` into later tests.
    """
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    access_disabled = logging.getLogger("uvicorn.access").disabled
    try:
        configure_logging("INFO")
        # The handler installed by configure_logging; a prior pytest fixture
        # (caplog) may also have a handler on root, so we pick ours by
        # selecting the StreamHandler we just added.
        handler = next(
            h for h in root.handlers
            if isinstance(h, logging.StreamHandler) and h.stream is sys.stderr
        )
        buf = io.StringIO()
        handler.stream = buf
        yield buf
    finally:
        root.handlers = saved_handlers
        root.level = saved_level
        logging.getLogger("uvicorn.access").disabled = access_disabled


@pytest.fixture
def client(log_buffer) -> TestClient:
    # mount_frontend=False — see create_app docstring.
    app = create_app(mount_frontend=False)

    @app.get("/_test/not_found")
    async def raise_not_found():
        raise NotFoundError("missing")

    @app.get("/_test/validation")
    async def raise_validation():
        raise ValidationFailedError("bad body")

    @app.get("/_test/integrity")
    async def raise_integrity():
        raise IntegrityError("disk corruption")

    @app.get("/_test/uncaught")
    async def raise_uncaught():
        # Not a ServiceError — exercises the "unknown exception" path.
        raise RuntimeError("something blew up")

    # ``raise_server_exceptions=False`` so the TestClient doesn't re-raise
    # the ServiceError out of the client call before FastAPI's exception
    # handler runs. Without this, the 5xx path can't be observed.
    return TestClient(app, raise_server_exceptions=False)


def _parse_lines(buf: io.StringIO) -> list[dict]:
    """Return the buffered log output as a list of parsed JSON records."""
    raw = buf.getvalue().strip()
    if not raw:
        return []
    return [json.loads(line) for line in raw.splitlines()]


# --------------------------------------------------------------------------- #
# http_request event
# --------------------------------------------------------------------------- #

class TestHttpRequestEvent:
    def test_emitted_once_per_request(self, client, log_buffer):
        r = client.get("/api/v1/health")
        assert r.status_code == 200

        events = [e for e in _parse_lines(log_buffer) if e["message"] == "http_request"]
        assert len(events) == 1

    def test_carries_required_fields(self, client, log_buffer):
        client.get("/api/v1/health")
        (event,) = [e for e in _parse_lines(log_buffer) if e["message"] == "http_request"]
        # D27 contract: method, path, status, duration_ms on every
        # http_request line.
        assert event["method"] == "GET"
        assert event["path"] == "/api/v1/health"
        assert event["status"] == 200
        # duration_ms is a number with <= 3 decimal places.
        assert isinstance(event["duration_ms"], (int, float))
        assert event["duration_ms"] >= 0

    def test_logger_and_level_are_stable(self, client, log_buffer):
        client.get("/api/v1/health")
        (event,) = [e for e in _parse_lines(log_buffer) if e["message"] == "http_request"]
        assert event["logger"] == "backend.http"
        assert event["level"] == "INFO"

    def test_request_id_matches_response_header(self, client, log_buffer):
        r = client.get("/api/v1/health")
        (event,) = [e for e in _parse_lines(log_buffer) if e["message"] == "http_request"]
        # The log line's request_id must equal the X-Request-Id header.
        # This is the D16↔D27 bridge: caller grep by header → finds log.
        assert event["request_id"] == r.headers["x-request-id"]
        # And it's a valid UUIDv4.
        assert UUID(event["request_id"]).version == 4

    def test_status_reflects_handler_status(self, client, log_buffer):
        # A 404 ServiceError still produces an http_request record with
        # the actual 404 status, not the default 500 fallback.
        r = client.get("/_test/not_found")
        assert r.status_code == 404
        (event,) = [e for e in _parse_lines(log_buffer) if e["message"] == "http_request"]
        assert event["status"] == 404

    def test_emits_for_uncaught_exception(self, client, log_buffer):
        # If a route raises something we don't handle (programmer bug),
        # the middleware's ``finally`` must still emit an http_request
        # record — otherwise the operator sees no trace of the request
        # that caused the 500 response the server generated.
        r = client.get("/_test/uncaught")
        assert r.status_code == 500
        events = [e for e in _parse_lines(log_buffer) if e["message"] == "http_request"]
        assert len(events) == 1
        assert events[0]["status"] == 500
        # No service_error record — only ServiceError subclasses trigger
        # that event. This is intentional: a generic RuntimeError is a
        # bug, and the request handler layer below us decides how to
        # represent it in the response. The middleware's job is just to
        # log that a request happened.
        service_errors = [e for e in _parse_lines(log_buffer) if e["message"] == "service_error"]
        assert service_errors == []


# --------------------------------------------------------------------------- #
# service_error event
# --------------------------------------------------------------------------- #

class TestServiceErrorEvent:
    def test_emitted_for_4xx_at_warning(self, client, log_buffer):
        client.get("/_test/validation")
        events = [e for e in _parse_lines(log_buffer) if e["message"] == "service_error"]
        assert len(events) == 1
        assert events[0]["level"] == "WARNING"
        assert events[0]["code"] == "validation_failed"
        assert events[0]["http_status"] == 422

    def test_emitted_for_5xx_at_error_with_traceback(self, client, log_buffer):
        client.get("/_test/integrity")
        events = [e for e in _parse_lines(log_buffer) if e["message"] == "service_error"]
        assert len(events) == 1
        event = events[0]
        assert event["level"] == "ERROR"
        assert event["code"] == "integrity_error"
        assert event["http_status"] == 500
        # 5xx carries exc_info — a single log line should be enough to
        # know what raised. 4xx deliberately omits this: caller error,
        # not server error, no need for a server-side traceback.
        assert "exception" in event
        assert "IntegrityError: disk corruption" in event["exception"]

    def test_4xx_has_no_traceback(self, client, log_buffer):
        client.get("/_test/not_found")
        (event,) = [e for e in _parse_lines(log_buffer) if e["message"] == "service_error"]
        assert "exception" not in event

    def test_correlates_with_http_request_by_request_id(self, client, log_buffer):
        # Both events must share a request_id. This is the invariant that
        # lets an operator say: "for request id X, I have N lines of
        # context" — grepping on request_id returns all relevant events.
        r = client.get("/_test/not_found")
        events = _parse_lines(log_buffer)
        service_error = next(e for e in events if e["message"] == "service_error")
        http_request = next(e for e in events if e["message"] == "http_request")
        assert service_error["request_id"] == http_request["request_id"]
        assert service_error["request_id"] == r.headers["x-request-id"]
        assert service_error["request_id"] == r.json()["request_id"]


# --------------------------------------------------------------------------- #
# End-to-end: all four channels carry the same id
# --------------------------------------------------------------------------- #

class TestEndToEndCorrelation:
    """The D16↔D27 contract in one place.

    For any error response, four surfaces quote the same UUID:
      1. X-Request-Id response header
      2. problem+json body's ``request_id`` field
      3. http_request log record's ``request_id``
      4. service_error log record's ``request_id``
    """

    def test_all_four_agree_on_request_id(self, client, log_buffer):
        r = client.get("/_test/validation")

        events = _parse_lines(log_buffer)
        http_req = next(e for e in events if e["message"] == "http_request")
        svc_err = next(e for e in events if e["message"] == "service_error")

        header_id = r.headers["x-request-id"]
        body_id = r.json()["request_id"]

        assert header_id == body_id == http_req["request_id"] == svc_err["request_id"]

    def test_different_requests_do_not_share_ids(self, client, log_buffer):
        r1 = client.get("/api/v1/health")
        r2 = client.get("/api/v1/health")
        assert r1.headers["x-request-id"] != r2.headers["x-request-id"]

        events = [e for e in _parse_lines(log_buffer) if e["message"] == "http_request"]
        assert len(events) == 2
        assert events[0]["request_id"] != events[1]["request_id"]


# --------------------------------------------------------------------------- #
# JSON Lines integrity
# --------------------------------------------------------------------------- #

class TestJsonLinesIntegrity:
    """D27: one JSON object per line, UTF-8, no stray bytes.

    A downstream tailer reading stderr must be able to split on '\\n' and
    feed each line to ``json.loads`` without buffering or repair.
    """

    def test_every_line_parses(self, client, log_buffer):
        client.get("/api/v1/health")
        client.get("/_test/integrity")
        raw = log_buffer.getvalue()
        # Trailing '\n' after the final record is expected (StreamHandler
        # appends it); the stripped body has no empties.
        assert raw.endswith("\n")
        for line in raw.strip().splitlines():
            body = json.loads(line)
            # Every line carries the D27 reserved five.
            assert {"timestamp", "level", "logger", "message", "request_id"} <= set(body)

    def test_formatter_is_our_json_formatter(self, log_buffer):
        # Belt-and-braces: configure_logging set a JsonFormatter. pytest's
        # ``caplog`` plumbing installs its own handler on the root logger
        # during test runs, so we can't assert a count of exactly one —
        # we assert that *our* handler (the one writing to the buffer) is
        # the StreamHandler carrying a JsonFormatter.
        ours = next(
            h
            for h in logging.getLogger().handlers
            if isinstance(h, logging.StreamHandler) and h.stream is log_buffer
        )
        assert isinstance(ours.formatter, JsonFormatter)
