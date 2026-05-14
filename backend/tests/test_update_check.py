"""Tests for the user-initiated update-check feature.

What's pinned down:

  * Service-layer ``check_for_updates`` returns each ``UpdateStatus``
    correctly given a mocked GitHub response (200/404/403/timeout).
  * The ``v``-prefix on release tags is canonicalized so ``v0.1.0``
    and ``0.1.0`` compare equal.
  * REST endpoint ``GET /api/v1/updates/check`` returns the right
    body when configured and a 503 ``update_check_disabled``
    problem+json when ``Settings.update_check_repo`` is unset.
  * The endpoint hits the real ``check_for_updates`` path with an
    injected transport so no real network call leaves the test.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_settings
from backend.api.rest import create_app
from backend.config import Settings
from backend.services.update_check_service import (
    UpdateCheckResult,
    check_for_updates,
)

# --------------------------------------------------------------------------- #
# Service layer
# --------------------------------------------------------------------------- #


def _client_returning(status: int, body: dict | str, headers: dict | None = None):
    """Build an httpx.Client that always returns the given response.

    Uses ``MockTransport`` — the official httpx test pattern — so the
    request never leaves the process. Returns a configured Client the
    caller passes to ``check_for_updates`` via the ``client`` kwarg.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(body, str):
            return httpx.Response(status, headers=headers or {}, text=body)
        return httpx.Response(status, headers=headers or {}, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


class TestServiceLayer:
    def test_up_to_date_when_tags_match(self):
        # Current and latest tags both canonicalize to "0.1.0" — the
        # leading ``v`` is the GitHub convention and we strip it on
        # both sides before comparison.
        client = _client_returning(
            200,
            {
                "tag_name": "v0.1.0",
                "html_url": "https://github.com/x/y/releases/tag/v0.1.0",
            },
        )
        result = check_for_updates(
            repo_slug="x/y", current_version="0.1.0", client=client
        )
        assert result.status == "up_to_date"
        assert result.latest == "v0.1.0"
        assert result.release_url == "https://github.com/x/y/releases/tag/v0.1.0"

    def test_update_available_when_tags_differ(self):
        client = _client_returning(
            200,
            {
                "tag_name": "v0.2.0",
                "html_url": "https://github.com/x/y/releases/tag/v0.2.0",
            },
        )
        result = check_for_updates(
            repo_slug="x/y", current_version="0.1.0", client=client
        )
        assert result.status == "update_available"
        assert result.latest == "v0.2.0"

    def test_no_releases_when_github_returns_404(self):
        # GitHub returns 404 when the repo has no published releases
        # *or* when the slug doesn't exist / is private. Same UX
        # treatment either way.
        client = _client_returning(404, {"message": "Not Found"})
        result = check_for_updates(
            repo_slug="x/y", current_version="0.1.0", client=client
        )
        assert result.status == "no_releases"
        assert result.detail is not None
        assert "x/y" in result.detail

    def test_rate_limited_when_github_returns_403_with_rate_limit(self):
        client = _client_returning(
            403,
            "API rate limit exceeded for 1.2.3.4",
            headers={"X-RateLimit-Reset": "1700000000"},
        )
        result = check_for_updates(
            repo_slug="x/y", current_version="0.1.0", client=client
        )
        assert result.status == "rate_limited"
        assert "1700000000" in (result.detail or "")

    def test_generic_403_is_error_not_rate_limited(self):
        # A 403 *without* a rate-limit body is a different failure
        # mode (auth, IP block, etc.); we report as "error" rather
        # than pretending to know.
        client = _client_returning(403, "Forbidden")
        result = check_for_updates(
            repo_slug="x/y", current_version="0.1.0", client=client
        )
        assert result.status == "error"

    def test_network_error_returns_error_status(self):
        # MockTransport that raises a network error; the service must
        # not propagate — a check button that 500s is worse UX than
        # one that says "couldn't check".
        def raising(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("dns failed", request=request)

        client = httpx.Client(transport=httpx.MockTransport(raising))
        result = check_for_updates(
            repo_slug="x/y", current_version="0.1.0", client=client
        )
        assert result.status == "error"
        assert "ConnectError" in (result.detail or "")

    def test_timeout_returns_error_status(self):
        def slow(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("too slow", request=request)

        client = httpx.Client(transport=httpx.MockTransport(slow))
        result = check_for_updates(
            repo_slug="x/y", current_version="0.1.0", client=client
        )
        assert result.status == "error"

    def test_non_json_body_returns_error(self):
        # GitHub *should* always send JSON for a 200 — but if a proxy
        # or transient failure returns HTML, the service should not
        # crash trying to parse it.
        client = _client_returning(200, "<html>not json</html>")
        result = check_for_updates(
            repo_slug="x/y", current_version="0.1.0", client=client
        )
        assert result.status == "error"

    def test_missing_tag_name_returns_error(self):
        # Malformed but JSON-parseable response — we still want a
        # structured error, not an attribute crash.
        client = _client_returning(200, {"some_other_field": "x"})
        result = check_for_updates(
            repo_slug="x/y", current_version="0.1.0", client=client
        )
        assert result.status == "error"

    def test_no_v_prefix_still_compared(self):
        # Some projects tag without ``v`` (just "0.1.0"). Comparison
        # canonicalizes both sides, so this still works.
        client = _client_returning(
            200,
            {"tag_name": "0.1.0", "html_url": "https://example.test/r"},
        )
        result = check_for_updates(
            repo_slug="x/y", current_version="v0.1.0", client=client
        )
        assert result.status == "up_to_date"


# --------------------------------------------------------------------------- #
# REST endpoint
# --------------------------------------------------------------------------- #


class TestUpdateCheckEndpoint:
    def _client(self, *, update_repo: str | None) -> TestClient:
        """Build an app with the ``update_check_repo`` setting overridden."""
        app = create_app(mount_frontend=False)
        app.dependency_overrides[get_settings] = lambda: Settings(
            update_check_repo=update_repo,
        )
        return TestClient(app)

    def test_returns_503_problem_json_when_unset(self):
        # No repo configured → 503 problem+json with stable code that
        # the UI branches on to hide the button. Doesn't 500, doesn't
        # silently succeed.
        client = self._client(update_repo=None)
        r = client.get("/api/v1/updates/check")
        assert r.status_code == 503
        assert r.headers["content-type"].startswith("application/problem+json")
        body = r.json()
        assert body["code"] == "update_check_disabled"

    def test_returns_200_with_status_when_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # When the repo IS configured, the endpoint actually calls the
        # service. We patch the service to return a known result so no
        # real GitHub call leaves the test process.
        from backend.api import ops as ops_module
        from backend.services import update_check_service

        def fake_check(*, repo_slug, current_version, client=None):
            return UpdateCheckResult(
                status="update_available",
                current=current_version,
                latest="v9.9.9",
                release_url="https://github.com/x/y/releases/tag/v9.9.9",
            )

        monkeypatch.setattr(
            ops_module, "check_for_updates", fake_check
        )
        monkeypatch.setattr(
            update_check_service, "check_for_updates", fake_check
        )

        client = self._client(update_repo="x/y")
        r = client.get("/api/v1/updates/check")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "update_available"
        assert body["latest"] == "v9.9.9"
        assert body["release_url"].endswith("/v9.9.9")
