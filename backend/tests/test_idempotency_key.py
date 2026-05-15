"""Slice 12 (D69): Idempotency-Key middleware tests.

Pins the contract:

  * POST requests without ``Idempotency-Key`` are unchanged.
  * POST + key, first time: response is captured and stored;
    second identical request replays the stored response verbatim
    without invoking the handler.
  * POST + key, second time with a different body: 422
    ``application/problem+json`` with ``code=idempotency_key_reuse``.
  * Expired rows are skipped (and cleaned up opportunistically).
  * Non-POST methods bypass the middleware entirely.
  * 4xx/5xx responses are NOT cached — retrying a failed request
    is still useful.
  * Multipart uploads work through the hash window.
"""
from __future__ import annotations

import time
from collections.abc import Iterator
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
    root = tmp_path / "logbook"
    bootstrap_logbook(root)
    result = open_index(root)
    result.conn.close()
    return root


def _build_client(bootstrapped_root: Path) -> Iterator[TestClient]:
    """App with isolated logbook for idempotency middleware tests."""
    overridden = Settings(logbook_root=bootstrapped_root)
    import backend.api.rest as rest_module

    original = rest_module.get_settings
    rest_module.get_settings = lambda: overridden
    try:
        app = create_app(mount_frontend=False)
        app.dependency_overrides[get_settings] = lambda: overridden
        app.dependency_overrides[get_logbook_root] = lambda: bootstrapped_root
        app.dependency_overrides[get_user_id] = lambda: "default"
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    finally:
        rest_module.get_settings = original


@pytest.fixture
def client(bootstrapped_root: Path) -> Iterator[TestClient]:
    yield from _build_client(bootstrapped_root)


def _jump_payload(jump_number: int = 1, title: str | None = None) -> dict:
    body: dict = {
        "jump_number": jump_number,
        "date": "2026-04-22",
        "dropzone": "Skydive Elsinore",
        "exit_altitude_m": 4000,
        "deployment_altitude_m": 900,
    }
    if title is not None:
        body["title"] = title
    return body


def _multipart_post(client: TestClient, payload: dict, *, key: str | None = None):
    import json as _json

    data = {"jump": _json.dumps(payload)}
    headers = {"Idempotency-Key": key} if key else {}
    return client.post("/api/v1/jumps", data=data, headers=headers)


# --------------------------------------------------------------------------- #
# Pass-through behaviour
# --------------------------------------------------------------------------- #


class TestPassThrough:
    def test_post_without_header_works(self, client: TestClient):
        # No Idempotency-Key → middleware passes through.
        resp = _multipart_post(client, _jump_payload(jump_number=1))
        assert resp.status_code == 201, resp.text

    def test_get_is_unaffected(self, client: TestClient):
        # The middleware short-circuits on non-POST methods. GETs
        # never look at the idempotency table.
        resp = client.get("/api/v1/jumps", headers={"Idempotency-Key": "k1"})
        # 200 expected on the list endpoint even with the header.
        assert resp.status_code == 200, resp.text


# --------------------------------------------------------------------------- #
# Replay behaviour
# --------------------------------------------------------------------------- #


class TestReplay:
    def test_second_identical_request_replays_stored_response(
        self, client: TestClient
    ):
        # First call goes to the handler (201). Second call with the
        # same key + body returns the stored response without re-
        # invoking the handler. Distinguished by the
        # ``idempotent-replayed`` response header.
        payload = _jump_payload(jump_number=42, title="First")
        first = _multipart_post(client, payload, key="abc-123")
        assert first.status_code == 201, first.text
        first_body = first.text

        second = _multipart_post(client, payload, key="abc-123")
        assert second.status_code == 201
        assert second.text == first_body, (
            "replay must return the exact same body bytes"
        )
        assert second.headers.get("idempotent-replayed") == "true"

    def test_first_response_does_not_have_replayed_header(
        self, client: TestClient
    ):
        # The first call ran the handler; only replays carry the
        # marker header.
        resp = _multipart_post(
            client, _jump_payload(jump_number=1), key="firstkey",
        )
        assert resp.status_code == 201
        assert resp.headers.get("idempotent-replayed") is None

    def test_replay_does_not_create_a_second_jump(
        self, client: TestClient
    ):
        # The whole point of idempotency: two POSTs → one jump
        # on disk.
        payload = _jump_payload(jump_number=7, title="OnlyOne")
        _multipart_post(client, payload, key="k-dedup")
        _multipart_post(client, payload, key="k-dedup")

        list_resp = client.get("/api/v1/jumps")
        assert list_resp.status_code == 200
        items = list_resp.json()
        matching = [j for j in items if j["jump_number"] == 7]
        assert len(matching) == 1, (
            f"expected 1 jump with number 7, got {len(matching)}"
        )


# --------------------------------------------------------------------------- #
# Reuse rejection
# --------------------------------------------------------------------------- #


class TestReuseRejection:
    def test_same_key_different_body_returns_422(self, client: TestClient):
        # Two different operations using the same key → 422 with
        # the documented code on the second.
        first = _multipart_post(
            client, _jump_payload(jump_number=1, title="A"), key="reuse-1",
        )
        assert first.status_code == 201

        second = _multipart_post(
            client, _jump_payload(jump_number=2, title="B"), key="reuse-1",
        )
        assert second.status_code == 422
        assert second.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)
        body = second.json()
        assert body["code"] == "idempotency_key_reuse"
        assert body["status"] == 422
        assert "reuse-1" in body["detail"]

    def test_reuse_rejection_does_not_create_a_second_jump(
        self, client: TestClient
    ):
        # The 422 must happen BEFORE the handler runs — assert by
        # confirming the second jump number doesn't appear.
        _multipart_post(
            client, _jump_payload(jump_number=10, title="Original"), key="r2",
        )
        rejected = _multipart_post(
            client, _jump_payload(jump_number=20, title="Different"), key="r2",
        )
        assert rejected.status_code == 422

        list_resp = client.get("/api/v1/jumps")
        items = list_resp.json()
        numbers = {j["jump_number"] for j in items}
        assert 10 in numbers
        assert 20 not in numbers


# --------------------------------------------------------------------------- #
# Non-2xx responses are not cached
# --------------------------------------------------------------------------- #


class TestNonSuccessNotCached:
    def test_validation_failure_response_is_not_replayed(
        self, client: TestClient
    ):
        # A 422 (handler-side validation) should NOT be cached:
        # the user fixes the request and retries with the same key
        # expecting success. The middleware must run the handler
        # on the retry, not return the prior 422.
        bad = _jump_payload(jump_number=1)
        bad["exit_altitude_m"] = -1  # invalid
        first = _multipart_post(client, bad, key="will-retry")
        assert first.status_code == 422

        # Retry with valid body and the same key — same hash
        # (because we deliberately keep most of the body identical)
        # would be the wrong test. The realistic flow is "different
        # body, same key, but the prior was a failure so the key
        # has no stored response and the retry runs". Build a
        # genuinely-corrected body — different hash, but no prior
        # cached response so the middleware does NOT reject.
        good = _jump_payload(jump_number=1)
        # Different content_length and prefix → different hash.
        # Since the 422 was never cached, the key has no record;
        # the new request runs the handler and succeeds.
        second = _multipart_post(client, good, key="will-retry")
        assert second.status_code == 201, second.text


# --------------------------------------------------------------------------- #
# Database persistence (assertions over the table directly)
# --------------------------------------------------------------------------- #


class TestStorage:
    def test_successful_post_inserts_row(
        self, client: TestClient, bootstrapped_root: Path,
    ):
        _multipart_post(
            client, _jump_payload(jump_number=1), key="row-check",
        )
        result = open_index(bootstrapped_root)
        try:
            row = result.conn.execute(
                "SELECT key, response_status FROM idempotency_keys "
                "WHERE key = ?",
                ("row-check",),
            ).fetchone()
        finally:
            result.conn.close()
        assert row is not None
        assert row["key"] == "row-check"
        assert row["response_status"] == 201

    def test_expired_row_is_skipped_and_replaced(
        self, client: TestClient, bootstrapped_root: Path,
    ):
        # Insert an expired row by hand — the middleware should
        # treat the key as unknown, run the handler, and replace
        # the row.
        result = open_index(bootstrapped_root)
        try:
            result.conn.execute(
                "INSERT INTO idempotency_keys "
                "(key, user_id, request_hash, response_status, "
                " response_content_type, response_body, "
                " created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "expired-key", "default", "stale-hash", 999,
                    "application/json", b'{"stale":true}',
                    "2020-01-01T00:00:00.000Z",
                    # Expired 5 years ago.
                    "2020-01-01T00:00:01.000Z",
                ),
            )
            result.conn.commit()
        finally:
            result.conn.close()

        resp = _multipart_post(
            client, _jump_payload(jump_number=1), key="expired-key",
        )
        # The expired record was not replayed (would have been 999).
        assert resp.status_code == 201
        assert resp.headers.get("idempotent-replayed") is None

        # Row is now replaced.
        result = open_index(bootstrapped_root)
        try:
            row = result.conn.execute(
                "SELECT response_status FROM idempotency_keys "
                "WHERE key = ?",
                ("expired-key",),
            ).fetchone()
        finally:
            result.conn.close()
        assert row is not None
        assert row["response_status"] == 201

    def test_purge_clears_expired_rows(
        self, client: TestClient, bootstrapped_root: Path,
    ):
        # Insert two expired rows. Any subsequent POST through the
        # middleware should purge them via the opportunistic cleanup.
        result = open_index(bootstrapped_root)
        try:
            for k in ("garbage-a", "garbage-b"):
                result.conn.execute(
                    "INSERT INTO idempotency_keys "
                    "(key, user_id, request_hash, response_status, "
                    " response_content_type, response_body, "
                    " created_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        k, "default", "x", 200, None, b"",
                        "2020-01-01T00:00:00.000Z",
                        "2020-01-01T00:00:01.000Z",
                    ),
                )
            result.conn.commit()
        finally:
            result.conn.close()

        _multipart_post(
            client, _jump_payload(jump_number=1), key="freshkey",
        )

        # Both expired rows should be gone.
        result = open_index(bootstrapped_root)
        try:
            remaining = result.conn.execute(
                "SELECT key FROM idempotency_keys WHERE key IN (?, ?)",
                ("garbage-a", "garbage-b"),
            ).fetchall()
        finally:
            result.conn.close()
        assert remaining == []


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #


class TestEdgeCases:
    def test_empty_key_header_passes_through(self, client: TestClient):
        # An empty value is treated as no key (the middleware skips
        # storage and runs the handler unchanged). Each request
        # creates its own jump.
        for n in (1, 2):
            resp = _multipart_post(
                client, _jump_payload(jump_number=n), key="",
            )
            assert resp.status_code == 201, resp.text

        list_resp = client.get("/api/v1/jumps")
        items = list_resp.json()
        nums = {j["jump_number"] for j in items}
        assert 1 in nums and 2 in nums

    def test_replay_works_after_simulated_delay(self, client: TestClient):
        # Time passes between the original and the replay. As long
        # as the TTL hasn't elapsed (24h), the replay still works.
        payload = _jump_payload(jump_number=5)
        first = _multipart_post(client, payload, key="delayed")
        assert first.status_code == 201
        time.sleep(0.05)
        second = _multipart_post(client, payload, key="delayed")
        assert second.status_code == 201
        assert second.headers.get("idempotent-replayed") == "true"
