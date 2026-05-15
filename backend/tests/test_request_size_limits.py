"""Per-request + per-file size cap enforcement (Slice 10).

Two layers:

1. ``RequestSizeLimitMiddleware`` enforces the per-request total via
   a Content-Length pre-check and a streaming byte-count fallback.
   Routes never see the body when the cap fires; the 413 response
   is constructed inline in the middleware (it lives outside the
   FastAPI exception-handler scope, per the existing rest.py
   ``ServerErrorMiddleware`` comment).
2. The ``_upload_chunks`` generator in ``backend/api/jumps.py`` and
   ``backend/api/jumpers.py`` enforces the per-file cap. Overrun
   raises :class:`PayloadTooLargeError` which routes through
   ``on_service_error`` and produces 413 problem+json. The
   partial tmp file is rolled back by
   ``atomic_write_stream``'s context manager.

The two layers cover different attack surfaces: per-request stops
"lots of small files totalling too much", per-file stops "one
giant file under a small request budget". Both produce the same
wire shape (``code=payload_too_large``).
"""
from __future__ import annotations

from collections.abc import Iterator
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_logbook_root, get_settings, get_user_id
from backend.api.errors import PROBLEM_JSON_MEDIA_TYPE
from backend.api.rest import create_app
from backend.config import Settings
from backend.storage.bootstrap import bootstrap_logbook
from backend.storage.index import open_index


@pytest.fixture
def bootstrapped_root(tmp_path: Path) -> Path:
    """Per-test logbook root with bootstrap + index already initialised.

    Mirrors the same-named fixture in test_rest_jumps.py — every test
    that builds a TestClient over /api/v1 routes needs an isolated
    logbook so writes from one test don't leak into another's
    jump-number-uniqueness check.
    """
    root = tmp_path / "logbook"
    bootstrap_logbook(root)
    result = open_index(root)
    result.conn.close()
    return root


def _build_client(
    *,
    bootstrapped_root: Path,
    max_request_bytes: int,
    max_file_bytes: int,
) -> Iterator[TestClient]:
    """Build an app with overridden Settings caps + isolated logbook.

    ``create_app`` reads ``get_settings()`` once at build time to
    fix the middleware cap, so the override must be applied via
    monkey-patching ``get_settings`` before ``create_app`` is
    called. ``app.dependency_overrides`` only fires for handler
    dependencies, not for build-time reads.
    """
    overridden = Settings(
        max_request_bytes=max_request_bytes,
        max_file_bytes=max_file_bytes,
    )
    # ``get_settings`` is ``@lru_cache``'d. We bypass the cache by
    # patching the module-level reference for the duration of this
    # client. The bound middleware in create_app reads via that
    # module's get_settings symbol.
    import backend.api.rest as rest_module
    original = rest_module.get_settings
    rest_module.get_settings = lambda: overridden
    try:
        app = create_app(mount_frontend=False)
        # The handler-level dependencies need overrides so per-file
        # enforcement reads the same cap and the per-test logbook
        # root is honoured (without this every test would write to
        # the developer's real ``~/SkydiveLogbook`` and pollute the
        # next test's jump-number uniqueness check).
        app.dependency_overrides[get_settings] = lambda: overridden
        app.dependency_overrides[get_logbook_root] = lambda: bootstrapped_root
        app.dependency_overrides[get_user_id] = lambda: "default"
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    finally:
        rest_module.get_settings = original


@pytest.fixture
def tiny_client(bootstrapped_root: Path) -> Iterator[TestClient]:
    """Caps tuned tiny so tests don't have to push real GiB.

    1 MiB total request, 256 KiB per file.
    """
    yield from _build_client(
        bootstrapped_root=bootstrapped_root,
        max_request_bytes=1 * 1024 * 1024,
        max_file_bytes=256 * 1024,
    )


def _assert_413(resp, *, label: str = "") -> dict:
    assert resp.status_code == 413, f"{label}: expected 413, got {resp.status_code}: {resp.text}"
    ct = resp.headers["content-type"]
    assert ct.startswith(PROBLEM_JSON_MEDIA_TYPE), (
        f"{label}: expected problem+json, got {ct!r}"
    )
    body = resp.json()
    assert body["status"] == 413
    assert body["code"] == "payload_too_large"
    assert body["request_id"]
    assert resp.headers["X-Request-Id"] == body["request_id"]
    return body


# --------------------------------------------------------------------------- #
# Middleware: per-request total
# --------------------------------------------------------------------------- #


def test_content_length_precheck_rejects_oversize_request(
    tiny_client: TestClient,
) -> None:
    """Client declares a Content-Length above the cap; the middleware
    short-circuits with 413 before any body is read.

    Realistic case: browser-issued multipart upload where the
    browser knows the total before the request begins.
    """
    # The middleware caps total at 1 MiB; declare 2 MiB.
    big_body = b"x" * (2 * 1024 * 1024)
    resp = tiny_client.post(
        "/api/v1/jumps",
        content=big_body,
        headers={
            "content-type": "application/octet-stream",
            "content-length": str(len(big_body)),
        },
    )
    _assert_413(resp, label="content-length pre-check")


def test_request_under_cap_passes_middleware(tiny_client: TestClient) -> None:
    """A small request body sails through the middleware. It might
    fail downstream validation (missing required form fields) but
    must NOT be 413.

    Regression guard: the middleware shouldn't false-positive on
    requests under the cap.
    """
    # 512 KiB total, well under the 1 MiB cap.
    body = b"x" * (512 * 1024)
    resp = tiny_client.post(
        "/api/v1/jumps",
        content=body,
        headers={
            "content-type": "application/octet-stream",
            "content-length": str(len(body)),
        },
    )
    # Not 413 — the request reached the handler. The handler
    # rejects the body because it isn't valid multipart, but the
    # status will be 422 or 415, not 413.
    assert resp.status_code != 413


# --------------------------------------------------------------------------- #
# Per-file: PayloadTooLargeError from the chunk loop
# --------------------------------------------------------------------------- #


def _make_jump_payload() -> str:
    """Minimum-valid JSON for the ``jump`` multipart field."""
    return (
        '{"jump_number": 1, "date": "2026-05-15", '
        '"dropzone": "Test DZ", "exit_altitude_m": 4000, '
        '"deployment_altitude_m": 900}'
    )


def test_per_file_cap_rejects_oversize_single_file(
    tiny_client: TestClient,
) -> None:
    """One attachment over ``max_file_bytes`` (but the whole request
    under ``max_request_bytes``) → 413 from the
    ``PayloadTooLargeError`` raise site inside ``_upload_chunks``.

    ``raise_server_exceptions=False`` on the TestClient lets the
    ServiceError propagate to the handler instead of erroring out.
    """
    # 512 KiB file is under the 1 MiB request cap, but over the
    # 256 KiB per-file cap.
    over_file = b"y" * (512 * 1024)
    resp = tiny_client.post(
        "/api/v1/jumps",
        data={"jump": _make_jump_payload()},
        files={"files": ("video.mp4", BytesIO(over_file), "video/mp4")},
    )
    body = _assert_413(resp, label="per-file cap")
    # The ServiceError extras (filename, consumed_bytes, max_bytes)
    # ride on the problem+json body as extension members per
    # ``build_problem``.
    assert body.get("filename") == "video.mp4"
    assert body.get("max_bytes") == 256 * 1024
    assert body.get("consumed_bytes", 0) > 256 * 1024


def test_per_request_cap_rejects_many_files_summing_over(
    tiny_client: TestClient,
) -> None:
    """Several files individually under the per-file cap but
    together over the per-request cap → 413 from the middleware
    (Content-Length pre-check, since TestClient sets the header).

    The middleware fires before the handler dispatches, so the
    request never reaches ``_upload_chunks`` — same wire shape
    either way per the unified 413 envelope.
    """
    # Five 250-KiB files (1.25 MiB total) — each under the 256 KiB
    # per-file cap individually IS WRONG: 250 KiB is *under* 256
    # KiB so per-file lets them through. The multipart framing
    # overhead pushes the total past 1 MiB → middleware fires.
    f_bytes = b"z" * (250 * 1024)
    files = [
        ("files", (f"f{i}.bin", BytesIO(f_bytes), "application/octet-stream"))
        for i in range(5)
    ]
    resp = tiny_client.post(
        "/api/v1/jumps",
        data={"jump": _make_jump_payload()},
        files=files,
    )
    _assert_413(resp, label="per-request cap across many files")


# --------------------------------------------------------------------------- #
# OpenAPI spec advertises 413
# --------------------------------------------------------------------------- #


def test_openapi_spec_advertises_413_on_multipart_routes() -> None:
    """Slice 10 added 413 to ERR_CREATE and ERR_UPDATE. Sample-check
    that the spec advertises a 413 response on the routes that
    actually accept bodies.
    """
    app = create_app(mount_frontend=False)
    spec = app.openapi()
    # Routes with bodies that the user actually uploads to.
    for method, path in [
        ("post", "/api/v1/jumps"),
        ("post", "/api/v1/jumps/{jump_id}/attachments"),
        ("post", "/api/v1/jumpers/{jumper_id}/attachments"),
    ]:
        op = spec["paths"][path][method]
        responses = op.get("responses", {})
        assert "413" in responses, (
            f"{method.upper()} {path} does not declare 413"
        )
        body = responses["413"]
        assert body.get("$ref") == "#/components/responses/PayloadTooLarge"
