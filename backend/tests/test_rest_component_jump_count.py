"""End-to-end HTTP coverage for D35 derived jump counts.

The service-layer test
:mod:`backend.tests.test_component_jump_count_derived` covers the
``jump_count_derived`` / ``jump_count_total`` enrichment math at the
service boundary. This file pins the contract one layer up — that the
JSON wire shape carries the fields a client renders against — so a
serializer / response-model misconfiguration that drops the fields on
the floor blows up CI rather than silently regressing the user-visible
"jumps on my rig" counter.

The fixture flow mirrors the maintainer's real-world failure: create
the four components, assemble a rig, log a single jump with rig_id,
GET each component, expect the count incremented by one.
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


def _seed_components_and_rig(client: TestClient) -> dict:
    """Create main + reserve + AAD + container, then assemble them
    into a rig. Returns ids + the rig payload."""
    main_resp = client.post(
        "/api/v1/mains",
        json={"jump_count_initial": 100},
    )
    assert main_resp.status_code == 201, main_resp.text
    main = main_resp.json()

    reserve_resp = client.post("/api/v1/reserves", json={})
    assert reserve_resp.status_code == 201, reserve_resp.text
    reserve = reserve_resp.json()

    aad_resp = client.post(
        "/api/v1/aads",
        json={"jump_count_initial": 200},
    )
    assert aad_resp.status_code == 201, aad_resp.text
    aad = aad_resp.json()

    container_resp = client.post(
        "/api/v1/containers",
        json={"jump_count_initial": 50},
    )
    assert container_resp.status_code == 201, container_resp.text
    container = container_resp.json()

    rig_resp = client.post(
        "/api/v1/rigs",
        json={
            "nickname": "Black Cobra",
            "jurisdiction": "USPA",
            "current_main_id": main["id"],
            "current_reserve_id": reserve["id"],
            "current_aad_id": aad["id"],
            "current_container_id": container["id"],
        },
    )
    assert rig_resp.status_code == 201, rig_resp.text
    rig = rig_resp.json()

    return {
        "main_id": main["id"],
        "reserve_id": reserve["id"],
        "aad_id": aad["id"],
        "container_id": container["id"],
        "rig_id": rig["id"],
    }


def _log_jump(client: TestClient, *, jump_number: int, rig_id: str) -> None:
    """Multipart POST to /jumps with rig_id set. The endpoint accepts
    a multipart body with a single ``jump`` JSON field (D30); no
    attachments are needed for this test."""
    import json

    resp = client.post(
        "/api/v1/jumps",
        files={
            "jump": (
                None,
                json.dumps(
                    {
                        "jump_number": jump_number,
                        "date": "2026-04-22",
                        "dropzone": "Skydive Elsinore",
                        "exit_altitude_m": 4000,
                        "deployment_altitude_m": 900,
                        "rig_id": rig_id,
                    }
                ),
                "application/json",
            )
        },
    )
    assert resp.status_code == 201, resp.text


def test_get_main_returns_derived_and_total(
    client: TestClient,
):
    ids = _seed_components_and_rig(client)
    _log_jump(client, jump_number=1, rig_id=ids["rig_id"])
    _log_jump(client, jump_number=2, rig_id=ids["rig_id"])

    resp = client.get(f"/api/v1/mains/{ids['main_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["jump_count_initial"] == 100
    assert body["jump_count_derived"] == 2
    assert body["jump_count_total"] == 102


def test_get_aad_returns_derived_and_total(
    client: TestClient,
):
    ids = _seed_components_and_rig(client)
    _log_jump(client, jump_number=1, rig_id=ids["rig_id"])

    resp = client.get(f"/api/v1/aads/{ids['aad_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["jump_count_initial"] == 200
    assert body["jump_count_derived"] == 1
    assert body["jump_count_total"] == 201


def test_get_container_returns_derived_and_total(
    client: TestClient,
):
    ids = _seed_components_and_rig(client)
    _log_jump(client, jump_number=1, rig_id=ids["rig_id"])

    resp = client.get(f"/api/v1/containers/{ids['container_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["jump_count_initial"] == 50
    assert body["jump_count_derived"] == 1
    assert body["jump_count_total"] == 51


def test_list_mains_carries_derived(client: TestClient):
    ids = _seed_components_and_rig(client)
    _log_jump(client, jump_number=1, rig_id=ids["rig_id"])

    resp = client.get("/api/v1/mains")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["id"] == ids["main_id"]
    assert items[0]["jump_count_derived"] == 1
    assert items[0]["jump_count_total"] == 101


def test_main_lineset_total_in_response(client: TestClient):
    """The nested ``current_lineset`` must also carry the projection."""
    # Create a main WITH a current_lineset, then a rig + jump.
    main_resp = client.post(
        "/api/v1/mains",
        json={
            "jump_count_initial": 10,
            "current_lineset": {
                "line_type": "Vectran V750",
                "breaking_strength_lb": 750.0,
                "install_date": "2025-01-15",
                "jumps_on_lineset_initial": 75,
            },
        },
    )
    assert main_resp.status_code == 201, main_resp.text
    main = main_resp.json()

    reserve = client.post("/api/v1/reserves", json={}).json()
    aad = client.post("/api/v1/aads", json={}).json()
    container = client.post("/api/v1/containers", json={}).json()
    rig = client.post(
        "/api/v1/rigs",
        json={
            "nickname": "Test",
            "jurisdiction": "USPA",
            "current_main_id": main["id"],
            "current_reserve_id": reserve["id"],
            "current_aad_id": aad["id"],
            "current_container_id": container["id"],
        },
    ).json()
    _log_jump(client, jump_number=1, rig_id=rig["id"])

    resp = client.get(f"/api/v1/mains/{main['id']}")
    body = resp.json()
    assert body["jump_count_derived"] == 1
    assert body["jump_count_total"] == 11
    ls = body["current_lineset"]
    assert ls["jumps_on_lineset_initial"] == 75
    assert ls["jumps_on_lineset_derived"] == 1
    assert ls["jumps_on_lineset_total"] == 76
