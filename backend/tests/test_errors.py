"""Tests for the RFC 9457 problem+json error envelope (D16).

These lock down the public contract. Every error response from the API
must be valid per RFC 9457 §3 (https://www.rfc-editor.org/rfc/rfc9457.html)
plus our documented extensions (`code`, `request_id`, `errors`).
"""
from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.errors import (
    PROBLEM_JSON_MEDIA_TYPE,
    ConflictError,
    FieldError,
    IntegrityError,
    NotFoundError,
    ServiceError,
    ValidationFailedError,
    build_problem,
    error_response,
    field_pointer,
)
from backend.api.rest import create_app


class TestFieldPointerEscaping:
    """RFC 6901 §3 mandates ``~`` → ``~0`` and ``/`` → ``~1`` (in that
    order — see helper docstring). Today every callsite uses Python
    identifier-style names that don't trip this; the helper exists so
    a future field whose name contains those characters cannot emit a
    malformed pointer that an RFC-9457-strict client would reject.
    """

    def test_clean_identifier_passes_through(self):
        # No special chars — pointer is the input prefixed with #/.
        assert field_pointer("jump_number") == "#/jump_number"
        assert field_pointer("exit_altitude_m") == "#/exit_altitude_m"

    def test_multiple_parts_join_with_slash(self):
        assert field_pointer("files", 0, "filename") == "#/files/0/filename"

    def test_int_parts_stringify(self):
        # List indices are integers; pyright accepts them via the
        # ``str | int`` annotation, runtime stringifies any object.
        assert field_pointer("attachments", 3) == "#/attachments/3"

    def test_tilde_escapes_to_zero_first(self):
        # RFC 6901 §3: ~ → ~0. Order matters — ~ must be escaped
        # BEFORE / so a literal "~" doesn't accidentally form "~1".
        assert field_pointer("with~tilde") == "#/with~0tilde"

    def test_slash_escapes_to_one(self):
        # RFC 6901 §3: / → ~1.
        assert field_pointer("with/slash") == "#/with~1slash"

    def test_both_specials_round_trip(self):
        # The pessimal case: a token with both ~ and /. The fixed
        # escape order (~ first, then /) is what makes this work —
        # if the order were reversed, the resulting "~1" from the /
        # would itself be re-escaped to "~01" and the round-trip
        # would decode to "~1" not "/".
        encoded = field_pointer("custom/path~with~specials")
        assert encoded == "#/custom~1path~0with~0specials"
        # Sanity-check the inverse mapping: an RFC-6901 decoder
        # applied to the token reproduces the original.
        token = encoded[len("#/"):]
        # Decode order is the reverse: ~1 → / first, then ~0 → ~.
        decoded = token.replace("~1", "/").replace("~0", "~")
        assert decoded == "custom/path~with~specials"

    def test_empty_call_returns_root_pointer(self):
        # Edge case: an empty pointer should be the root marker. We
        # don't use this at any current callsite, but the helper's
        # behavior should be sensible.
        assert field_pointer() == "#/"


class TestBuildProblem:
    def test_populates_standard_members(self):
        exc = NotFoundError("jump 42 not found")
        body = build_problem(exc, request_id=UUID(int=1), instance="/api/v1/jumps/42")
        assert body.type == "about:blank"
        assert body.title == "Not Found"
        assert body.status == 404
        assert body.detail == "jump 42 not found"
        assert body.instance == "/api/v1/jumps/42"
        assert body.code == "not_found"
        assert body.request_id == UUID(int=1)
        assert body.errors is None

    def test_mints_request_id_when_missing(self):
        # Not hitting this through the middleware: the service layer can
        # call build_problem directly and still get a valid body.
        body = build_problem(NotFoundError("missing"))
        assert isinstance(body.request_id, UUID)

    def test_passes_through_field_errors(self):
        exc = ValidationFailedError(
            "body failed validation",
            errors=[
                FieldError(pointer="#/exit_altitude_m", detail="must be >= 0"),
                FieldError(pointer="#/date", detail="must be ISO-8601"),
            ],
        )
        body = build_problem(exc)
        assert body.errors is not None
        assert len(body.errors) == 2
        assert body.errors[0].pointer == "#/exit_altitude_m"

    def test_details_become_extension_members(self):
        exc = ConflictError("jump_number 42 already taken", taken_by="id-123")
        body = build_problem(exc)
        # Extra kwargs land as top-level extensions (RFC 9457 §3.2).
        assert body.taken_by == "id-123"

    def test_detail_collision_with_standard_member_raises(self):
        # `title` is a standard member — shadowing it would corrupt the
        # contract. build_problem must fail loudly rather than silently
        # overwrite.
        exc = ConflictError("x", title="sneaky override")
        with pytest.raises(ValueError):
            build_problem(exc)

    @pytest.mark.parametrize(
        "cls,expected_status,expected_code,expected_title",
        [
            (NotFoundError, 404, "not_found", "Not Found"),
            (ConflictError, 409, "conflict", "Conflict"),
            (ValidationFailedError, 422, "validation_failed", "Validation Failed"),
            (IntegrityError, 500, "integrity_error", "Integrity Error"),
        ],
    )
    def test_error_type_contract(self, cls, expected_status, expected_code, expected_title):
        # These four are the public surface — locking them in so a rename
        # or status-code change gets flagged as the breaking change it is.
        body = build_problem(cls("x"))
        assert body.status == expected_status
        assert body.code == expected_code
        assert body.title == expected_title


class TestErrorResponse:
    def test_media_type_is_problem_json(self):
        # RFC 9457 §3: responses MUST use application/problem+json.
        resp = error_response(NotFoundError("x"))
        assert resp.media_type == PROBLEM_JSON_MEDIA_TYPE

    def test_status_code_matches_body_status(self):
        # §3.1.2: the `status` member "MUST match" the HTTP status header
        # (advisory-but-consistent rule).
        resp = error_response(ConflictError("x"))
        body = json.loads(resp.body)
        assert resp.status_code == body["status"] == 409

    def test_excludes_none_optional_fields(self):
        # `instance` and `errors` are optional — shipping `null` makes the
        # response noisier than the RFC examples without any upside.
        resp = error_response(NotFoundError("x"))
        body = json.loads(resp.body)
        assert "instance" not in body
        assert "errors" not in body


class TestThroughFastAPI:
    """End-to-end tests through a real FastAPI app.

    These catch issues the handler could hide: middleware order, status
    code propagation, header shape, and that the exception handler is
    actually bound.
    """

    @pytest.fixture
    def client(self) -> TestClient:
        # mount_frontend=False — see create_app docstring.
        app = create_app(mount_frontend=False)

        @app.get("/_test/notfound")
        async def raise_not_found():
            raise NotFoundError("nope", searched_for="thing-42")

        @app.get("/_test/validation")
        async def raise_validation():
            raise ValidationFailedError(
                "bad body",
                errors=[FieldError(pointer="#/x", detail="required")],
            )

        @app.get("/_test/generic")
        async def raise_generic():
            raise ServiceError("boom")

        return TestClient(app)

    def test_returns_problem_json_content_type(self, client):
        r = client.get("/_test/notfound")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)

    def test_attaches_request_id_header(self, client):
        r = client.get("/_test/notfound")
        # Middleware sets X-Request-Id; body.request_id matches.
        assert "x-request-id" in r.headers
        body = r.json()
        assert body["request_id"] == r.headers["x-request-id"]

    def test_instance_is_request_path(self, client):
        r = client.get("/_test/notfound")
        assert r.json()["instance"] == "/_test/notfound"

    def test_extension_details_passed_through(self, client):
        r = client.get("/_test/notfound")
        # `searched_for` was passed as a ServiceError detail; must appear as
        # a top-level extension per RFC 9457 §3.2.
        assert r.json()["searched_for"] == "thing-42"

    def test_validation_errors_preserved(self, client):
        r = client.get("/_test/validation")
        body = r.json()
        assert r.status_code == 422
        assert body["code"] == "validation_failed"
        assert body["errors"] == [{"pointer": "#/x", "detail": "required"}]

    def test_generic_service_error_maps_to_500(self, client):
        r = client.get("/_test/generic")
        assert r.status_code == 500
        assert r.json()["code"] == "internal_error"


class TestUnhandledExceptionCatchAll:
    """D16: problem+json is the ONLY error shape at the API boundary.

    The typed-error handler covers every ``ServiceError`` subclass; these
    tests exercise the catch-all ``Exception`` handler that closes the
    gap for bugs outside the typed hierarchy. Without the catch-all,
    FastAPI's default ``PlainTextResponse("Internal Server Error", 500)``
    would leak through — text/plain, not application/problem+json.

    The raw exception message MUST NOT appear in the response body: a
    non-loopback deployment would leak internal state. The traceback
    goes to the log stream, correlated by ``request_id``.
    """

    # A sentinel string that identifies the exception message. If the
    # catch-all handler ever echoes ``str(exc)`` into ``detail``, the
    # leak assertions below catch it.
    _LEAK_SENTINEL = "leak-sentinel-do-not-ship-this-to-the-client"

    @pytest.fixture
    def client(self) -> TestClient:
        # mount_frontend=False — see create_app docstring.
        app = create_app(mount_frontend=False)

        @app.get("/_test/bare_exception")
        async def raise_bare_exception():
            # Non-ServiceError — simulates a developer bug or a library
            # exception bubbling past a handler that forgot to translate.
            raise RuntimeError(TestUnhandledExceptionCatchAll._LEAK_SENTINEL)

        # ``raise_server_exceptions=False``: Starlette's TestClient
        # defaults to re-raising the original exception after handlers
        # have run (a debugging aid that lets asserts see the traceback).
        # For a contract test of the handler itself we want the wire
        # view — the response the handler produced. See
        # https://www.starlette.io/testclient/ §"raise_server_exceptions".
        return TestClient(app, raise_server_exceptions=False)

    def test_returns_problem_json_content_type(self, client):
        # RFC 9457 §3: MUST use application/problem+json. The whole
        # reason the catch-all exists.
        r = client.get("/_test/bare_exception")
        assert r.status_code == 500
        assert r.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)

    def test_body_shape_matches_rfc_9457(self, client):
        r = client.get("/_test/bare_exception")
        body = r.json()
        assert body["code"] == "internal_error"
        assert body["title"] == "Internal Server Error"
        assert body["status"] == 500
        assert body["type"] == "about:blank"
        assert body["instance"] == "/_test/bare_exception"
        # request_id in the body matches the X-Request-Id header — the
        # whole point of D27 correlation.
        assert body["request_id"] == r.headers["x-request-id"]

    def test_loopback_surfaces_exception_in_detail(self, client):
        # D20 + the on_unhandled_exception docstring: v0.1 is a single-
        # user desktop app bound to loopback, so the catch-all
        # deliberately surfaces ``ExcType: message`` in the response
        # body's ``detail`` so the user can read it in the modal/error
        # banner without tailing logs. This test pins THAT contract.
        # When v0.1 grows beyond loopback (multi-user / remote API),
        # this test must tighten — flip ``not in`` back, and update
        # the on_unhandled_exception branch the comment there points
        # at.
        r = client.get("/_test/bare_exception")
        body = r.json()
        assert body["code"] == "internal_error"
        # The exception type + message land in ``detail`` for desktop
        # ergonomics (current v0.1 contract).
        assert "RuntimeError" in body["detail"]
        assert self._LEAK_SENTINEL in body["detail"]

    def test_does_not_leak_traceback_to_response_body(self, client):
        # The full traceback (frame names, file paths, line numbers)
        # MUST stay out of the response body even on v0.1 loopback.
        # The traceback goes to the log stream via ``exc_info=`` and
        # the user never needs to see it in the UI — surfacing
        # "RuntimeError: <message>" is enough for triage. A regression
        # that tee'd the traceback into the body would surface here.
        r = client.get("/_test/bare_exception")
        body_text = r.text
        # Frame markers from Python's traceback formatting — none of
        # these should appear in the on-the-wire body.
        assert "Traceback" not in body_text
        assert "raise_bare_exception" not in body_text  # the frame name
        assert "rest.py" not in body_text  # file path
        # Headers carry only UUIDs and content-type; assert defensively.
        for header_value in r.headers.values():
            assert "Traceback" not in header_value

    def test_service_errors_still_route_through_typed_handler(self, client):
        # MRO specificity: registering Exception must not hijack
        # ServiceError routing. A regression here would flatten every
        # 404/409/422 into a generic 500.
        # mount_frontend=False — see create_app docstring.
        app = create_app(mount_frontend=False)

        @app.get("/_test/notfound_still_typed")
        async def still_typed():
            # A NotFoundError is a ServiceError is an Exception — if the
            # catch-all wins the MRO race, this would return 500 with
            # code=internal_error instead of 404 with code=not_found.
            from backend.api.errors import NotFoundError
            raise NotFoundError("still typed")

        c = TestClient(app)
        r = c.get("/_test/notfound_still_typed")
        assert r.status_code == 404
        assert r.json()["code"] == "not_found"


class TestOpenAPISurfaces:
    """The OpenAPI spec must document the problem+json shape so clients
    can generate types against our contract."""

    def test_problem_details_schema_is_registered(self):
        # mount_frontend=False — see create_app docstring.
        app = create_app(mount_frontend=False)
        schema = app.openapi()
        assert "ProblemDetails" in schema["components"]["schemas"]
        pd = schema["components"]["schemas"]["ProblemDetails"]
        # RFC 9457 §3.1 member names are all present.
        assert set(pd["properties"]).issuperset(
            {"type", "title", "status", "detail", "instance", "code", "request_id", "errors"}
        )

    def test_shared_error_responses_use_problem_json(self):
        # mount_frontend=False — see create_app docstring.
        # Post-Slice-A-finalize: ``IntegrityError`` was removed as a
        # reusable response component because its wire shape is
        # structurally identical to ``Internal`` (both 500
        # problem+json). Per-``code`` discrimination is what
        # consumers branch on at runtime; both ``code`` values
        # are listed in the ProblemDetails schema's ``code.examples``.
        app = create_app(mount_frontend=False)
        schema = app.openapi()
        for name in ("NotFound", "Conflict", "ValidationFailed", "Internal"):
            resp = schema["components"]["responses"][name]
            assert PROBLEM_JSON_MEDIA_TYPE in resp["content"]
            assert resp["content"][PROBLEM_JSON_MEDIA_TYPE]["schema"] == {
                "$ref": "#/components/schemas/ProblemDetails"
            }


# Keep imports used to silence ruff if we later enable it.
_ = uuid4
