"""Integration tests for ``/api/v1/onboarding`` (D64).

Covers HTTP shape only — service-layer details live in
test_onboarding_service. The dependency-override pattern matches
the other ``test_rest_*`` files.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_logbook_root, get_user_id
from backend.api.rest import create_app
from backend.storage.bootstrap import bootstrap_logbook


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


# --------------------------------------------------------------------------- #
# GET /api/v1/onboarding
# --------------------------------------------------------------------------- #

class TestGet:
    def test_fresh_logbook_returns_all_false(self, client: TestClient) -> None:
        r = client.get("/api/v1/onboarding")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["completed"] is False
        assert body["completed_at"] is None
        assert body["status"] is None
        assert body["has_jumper"] is False
        assert body["has_dropzones"] is False
        assert body["has_rigs"] is False

    def test_response_shape_matches_model(self, client: TestClient) -> None:
        r = client.get("/api/v1/onboarding")
        body = r.json()
        # All six declared fields are present (the OpenAPI contract
        # third-party tooling reads — keep this stable).
        assert set(body.keys()) == {
            "completed",
            "completed_at",
            "status",
            "has_jumper",
            "has_dropzones",
            "has_rigs",
        }


# --------------------------------------------------------------------------- #
# POST /api/v1/onboarding/complete
# --------------------------------------------------------------------------- #

class TestComplete:
    def test_finished_stamps_sentinel(
        self, client: TestClient, bootstrapped_root: Path,
    ) -> None:
        r = client.post(
            "/api/v1/onboarding/complete", json={"status": "finished"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["completed"] is True
        assert body["status"] == "finished"
        # Sentinel actually written.
        assert (bootstrapped_root / ".onboarding_completed").is_file()

    def test_skipped_also_stamps_sentinel(
        self, client: TestClient, bootstrapped_root: Path,
    ) -> None:
        r = client.post(
            "/api/v1/onboarding/complete", json={"status": "skipped"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "skipped"
        assert (bootstrapped_root / ".onboarding_completed").is_file()

    def test_invalid_status_returns_422(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/onboarding/complete", json={"status": "wat"},
        )
        assert r.status_code == 422

    def test_missing_body_returns_422(self, client: TestClient) -> None:
        r = client.post("/api/v1/onboarding/complete", json={})
        assert r.status_code == 422

    def test_get_after_complete_reflects_state(
        self, client: TestClient,
    ) -> None:
        client.post(
            "/api/v1/onboarding/complete", json={"status": "finished"},
        )
        r = client.get("/api/v1/onboarding")
        body = r.json()
        assert body["completed"] is True
        assert body["status"] == "finished"
        assert body["completed_at"] is not None
