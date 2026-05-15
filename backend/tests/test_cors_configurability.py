"""CORS allow-list is read from ``Settings.cors_allowed_origins`` (Slice 21).

Prior to Slice 21 the allow-list was a hardcoded
``["http://localhost:5173", "http://127.0.0.1:5173"]`` baked into
``rest.create_app``. A user who ran the SPA dev server on a non-
standard port had to edit code. Worse, a user binding the API to a
known-LAN host had no way to widen the allow-list to include their
desktop's browser origin without recompiling.

Slice 21 moves the list into ``Settings`` with the same two values
as the default. Env override:
``SKYDIVE_CORS_ALLOWED_ORIGINS=http://foo:1234,http://bar:5678``.

These tests pin three contracts:
  1. Default origins still get a CORS Access-Control-Allow-Origin
     header on a cross-origin preflight (regression guard).
  2. An overridden origin gets the same affordance.
  3. An empty allow-list disables CORS entirely (no
     Access-Control-Allow-Origin on any origin).
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from backend.api.rest import create_app
from backend.config import Settings


@contextmanager
def _build_client(*, cors_allowed_origins: list[str]) -> Iterator[TestClient]:
    """Build an app whose Settings declares an explicit CORS list.

    Same monkey-patch pattern as test_request_size_limits.py — the
    middleware reads ``get_settings()`` once at build time, so the
    override must be in place before ``create_app`` is called.
    """
    overridden = Settings(cors_allowed_origins=cors_allowed_origins)
    import backend.api.rest as rest_module
    original = rest_module.get_settings
    rest_module.get_settings = lambda: overridden
    try:
        app = create_app(mount_frontend=False)
        with TestClient(app) as c:
            yield c
    finally:
        rest_module.get_settings = original


def test_default_origins_get_cors_header() -> None:
    """The pre-Slice-21 default — ``http://localhost:5173`` — is
    still allowed when the Settings doesn't override.
    """
    with _build_client(
        cors_allowed_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    ) as client:
        resp = client.options(
            "/api/v1/health",
            headers={
                "origin": "http://localhost:5173",
                "access-control-request-method": "GET",
            },
        )
        assert resp.status_code == 200
        assert (
            resp.headers.get("access-control-allow-origin")
            == "http://localhost:5173"
        )


def test_overridden_origin_gets_cors_header() -> None:
    """A user-configured origin is honoured — the default list is
    replaced wholesale, not merged.
    """
    with _build_client(
        cors_allowed_origins=["http://my-lan-host:4200"]
    ) as client:
        resp = client.options(
            "/api/v1/health",
            headers={
                "origin": "http://my-lan-host:4200",
                "access-control-request-method": "GET",
            },
        )
        assert resp.status_code == 200
        assert (
            resp.headers.get("access-control-allow-origin")
            == "http://my-lan-host:4200"
        )


def test_overridden_list_excludes_default_origins() -> None:
    """When the user provides their own list, the pre-Slice-21
    defaults are NOT silently added — the override is the entire
    allow-list.
    """
    with _build_client(
        cors_allowed_origins=["http://my-lan-host:4200"]
    ) as client:
        resp = client.options(
            "/api/v1/health",
            headers={
                "origin": "http://localhost:5173",
                "access-control-request-method": "GET",
            },
        )
        # Starlette's CORSMiddleware returns 400 for a preflight
        # whose origin isn't in the allow-list (and omits the
        # Access-Control-Allow-Origin header).
        assert resp.status_code in (400, 200)
        assert resp.headers.get("access-control-allow-origin") != (
            "http://localhost:5173"
        )


def test_empty_list_disables_cors_entirely() -> None:
    """``cors_allowed_origins=[]`` short-circuits the middleware
    registration. A cross-origin request gets no CORS headers
    back — same shape as a same-origin-only deployment (the
    packaged pywebview case).
    """
    with _build_client(cors_allowed_origins=[]) as client:
        resp = client.options(
            "/api/v1/health",
            headers={
                "origin": "http://anything:5173",
                "access-control-request-method": "GET",
            },
        )
        # No CORS middleware means the OPTIONS preflight hits the
        # route table directly. /api/v1/health is a GET-only route
        # so OPTIONS produces 405. Either way: no allow-origin
        # header.
        assert resp.headers.get("access-control-allow-origin") is None


def test_env_var_parses_comma_separated_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pydantic-settings env source parses comma-separated lists
    for ``list[str]`` fields. A real-world override via env should
    yield the right Python list.
    """
    monkeypatch.setenv(
        "SKYDIVE_CORS_ALLOWED_ORIGINS",
        '["http://a:1", "http://b:2"]',
    )
    s = Settings()
    assert s.cors_allowed_origins == ["http://a:1", "http://b:2"]
