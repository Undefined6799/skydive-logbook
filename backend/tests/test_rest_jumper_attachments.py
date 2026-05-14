"""Phase C.4 — Integration tests for jumper attachment REST endpoints.

  * ``POST /api/v1/jumpers/{id}/attachments`` — multipart upload of one
    credential card / medical certificate file.
  * ``DELETE /api/v1/jumpers/{id}/attachments/{attachment_id}`` — hard-
    delete with the cross-reference 409 from C.3 surfaced through HTTP.

Tests run against a TestClient with ``get_logbook_root`` overridden
to point at a fresh ``tmp_path`` per test, mirroring the existing
``test_rest_jumpers.py`` setup.

D16 invariants checked here:
  * Errors return ``application/problem+json`` with full envelope
    (code, title, status, detail, request_id, optional errors).
  * 404 / 422 / 409 each surface with the right HTTP status and
    structured payload.

D27 invariant checked here:
  * Every response carries an ``X-Request-Id`` header.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_logbook_root, get_user_id
from backend.api.errors import PROBLEM_JSON_MEDIA_TYPE
from backend.api.rest import create_app
from backend.models.jumper import (
    Medical,
    MedicalKind,
    Membership,
    OrgEnum,
)
from backend.services import jumper_service
from backend.services.jumper_service import _write_jumper
from backend.storage.bootstrap import bootstrap_logbook
from backend.storage.jumper_migration import (
    ATTACHMENTS_DIRNAME,
    JUMPERS_DIRNAME,
)


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


def _create_jumper_via_api(client: TestClient) -> dict:
    r = client.post("/api/v1/jumpers", json={"exit_weight_lb": 180})
    assert r.status_code == 201
    return r.json()


def _post_attachment(
    client: TestClient,
    jumper_id: str,
    filename: str,
    data: bytes,
    content_type: str = "application/pdf",
):
    return client.post(
        f"/api/v1/jumpers/{jumper_id}/attachments",
        files={"file": (filename, data, content_type)},
    )


# --------------------------------------------------------------------- #
# POST — happy path
# --------------------------------------------------------------------- #

class TestPostAttachment:
    def test_returns_200_and_full_jumper(self, client: TestClient) -> None:
        jumper = _create_jumper_via_api(client)
        r = _post_attachment(
            client, jumper["id"], "cspa-card.pdf", b"PDF bytes"
        )
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == jumper["id"]
        assert len(body["attachments"]) == 1
        assert body["attachments"][0]["filename"] == "cspa-card.pdf"
        assert body["attachments"][0]["size"] == len(b"PDF bytes")
        assert body["attachments"][0]["content_type"] == "application/pdf"

    def test_attachment_id_is_server_minted(self, client: TestClient) -> None:
        jumper = _create_jumper_via_api(client)
        r = _post_attachment(client, jumper["id"], "card.pdf", b"data")
        att_id = r.json()["attachments"][0]["id"]
        # UUIDv4 shape (the XSD pattern enforces v4 + variant bits).
        assert len(att_id) == 36
        assert att_id.count("-") == 4

    def test_file_lands_on_disk(
        self, client: TestClient, bootstrapped_root: Path
    ) -> None:
        jumper = _create_jumper_via_api(client)
        r = _post_attachment(
            client, jumper["id"], "card.pdf", b"hello world"
        )
        att = r.json()["attachments"][0]
        disk_path = (
            bootstrapped_root
            / JUMPERS_DIRNAME
            / jumper["id"]
            / ATTACHMENTS_DIRNAME
            / f"{att['id']}__card.pdf"
        )
        assert disk_path.is_file()
        assert disk_path.read_bytes() == b"hello world"

    def test_two_attachments_accumulate(self, client: TestClient) -> None:
        jumper = _create_jumper_via_api(client)
        _post_attachment(client, jumper["id"], "a.pdf", b"AAA")
        r2 = _post_attachment(client, jumper["id"], "b.pdf", b"BBB")
        attachments = r2.json()["attachments"]
        assert len(attachments) == 2
        filenames = {a["filename"] for a in attachments}
        assert filenames == {"a.pdf", "b.pdf"}

    def test_get_jumper_returns_attachment_after_post(
        self, client: TestClient
    ) -> None:
        jumper = _create_jumper_via_api(client)
        post_r = _post_attachment(client, jumper["id"], "card.pdf", b"data")
        get_r = client.get(f"/api/v1/jumpers/{jumper['id']}")
        assert get_r.status_code == 200
        assert get_r.json()["attachments"] == post_r.json()["attachments"]

    def test_response_has_request_id_header(self, client: TestClient) -> None:
        jumper = _create_jumper_via_api(client)
        r = _post_attachment(client, jumper["id"], "card.pdf", b"data")
        assert "x-request-id" in {k.lower() for k in r.headers}


# --------------------------------------------------------------------- #
# POST — error paths
# --------------------------------------------------------------------- #

class TestPostAttachmentErrors:
    def test_unknown_jumper_returns_404(self, client: TestClient) -> None:
        r = client.post(
            f"/api/v1/jumpers/{uuid4()}/attachments",
            files={"file": ("card.pdf", b"data", "application/pdf")},
        )
        assert r.status_code == 404
        assert r.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)
        body = r.json()
        assert body["status"] == 404
        assert body["code"] == "not_found"

    def test_invalid_filename_returns_422(self, client: TestClient) -> None:
        jumper = _create_jumper_via_api(client)
        r = _post_attachment(
            client, jumper["id"], "../escape.pdf", b"data"
        )
        assert r.status_code == 422
        assert r.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)
        body = r.json()
        # FieldError list points at #/filename.
        assert any(
            e.get("pointer") == "#/filename"
            for e in body.get("errors", [])
        )

    def test_invalid_uuid_in_path_returns_422(self, client: TestClient) -> None:
        # FastAPI's path-parameter parsing rejects non-UUID before the
        # service ever runs. The generic 422 envelope still applies.
        r = client.post(
            "/api/v1/jumpers/not-a-uuid/attachments",
            files={"file": ("card.pdf", b"data", "application/pdf")},
        )
        assert r.status_code == 422


# --------------------------------------------------------------------- #
# DELETE — happy path
# --------------------------------------------------------------------- #

class TestDeleteAttachment:
    def test_returns_200_and_jumper_without_attachment(
        self, client: TestClient
    ) -> None:
        jumper = _create_jumper_via_api(client)
        post_r = _post_attachment(client, jumper["id"], "card.pdf", b"data")
        att_id = post_r.json()["attachments"][0]["id"]

        r = client.delete(
            f"/api/v1/jumpers/{jumper['id']}/attachments/{att_id}"
        )
        assert r.status_code == 200
        body = r.json()
        assert body["attachments"] == []

    def test_disk_file_unlinked(
        self, client: TestClient, bootstrapped_root: Path
    ) -> None:
        jumper = _create_jumper_via_api(client)
        post_r = _post_attachment(client, jumper["id"], "card.pdf", b"data")
        att_id = post_r.json()["attachments"][0]["id"]
        disk_path = (
            bootstrapped_root
            / JUMPERS_DIRNAME
            / jumper["id"]
            / ATTACHMENTS_DIRNAME
            / f"{att_id}__card.pdf"
        )
        assert disk_path.is_file()
        client.delete(
            f"/api/v1/jumpers/{jumper['id']}/attachments/{att_id}"
        )
        assert not disk_path.exists()


# --------------------------------------------------------------------- #
# DELETE — error paths
# --------------------------------------------------------------------- #

class TestDeleteAttachmentErrors:
    def test_unknown_jumper_returns_404(self, client: TestClient) -> None:
        r = client.delete(
            f"/api/v1/jumpers/{uuid4()}/attachments/{uuid4()}"
        )
        assert r.status_code == 404
        assert r.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)

    def test_unknown_attachment_returns_404(self, client: TestClient) -> None:
        jumper = _create_jumper_via_api(client)
        r = client.delete(
            f"/api/v1/jumpers/{jumper['id']}/attachments/{uuid4()}"
        )
        assert r.status_code == 404
        body = r.json()
        assert body["code"] == "not_found"

    def test_referenced_attachment_returns_409(
        self, client: TestClient, bootstrapped_root: Path
    ) -> None:
        # Set up a jumper whose membership references the attachment.
        # JumperUpdate is identity-only, so we use _write_jumper
        # directly for the credential setup (Phase D will land
        # credential CRUD endpoints and obviate this).
        jumper = _create_jumper_via_api(client)
        post_r = _post_attachment(client, jumper["id"], "card.pdf", b"data")
        att_id = post_r.json()["attachments"][0]["id"]

        from uuid import UUID

        # Read the current Jumper through the service, then write back
        # with a credential that references the attachment.
        from_service = jumper_service.get_jumper(
            bootstrapped_root, "default", UUID(jumper["id"])
        )
        with_credential = from_service.model_copy(
            update={
                "memberships": [
                    Membership(
                        org=OrgEnum.CSPA,
                        member_number="12345",
                        expiry_date=date(2027, 4, 29),
                        card_attachment_id=UUID(att_id),
                    ),
                ],
            },
        )
        _write_jumper(bootstrapped_root, with_credential)

        r = client.delete(
            f"/api/v1/jumpers/{jumper['id']}/attachments/{att_id}"
        )
        assert r.status_code == 409
        assert r.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)
        body = r.json()
        assert body["code"] == "conflict"
        assert any(
            e.get("pointer") == "#/memberships/0/card_attachment_id"
            for e in body.get("errors", [])
        )

    def test_referenced_attachment_409_lists_multiple_pointers(
        self, client: TestClient, bootstrapped_root: Path
    ) -> None:
        # Two credentials referencing the same attachment — both
        # pointers in the 409's errors array.
        from uuid import UUID

        jumper = _create_jumper_via_api(client)
        post_r = _post_attachment(client, jumper["id"], "card.pdf", b"data")
        att_id = post_r.json()["attachments"][0]["id"]

        from_service = jumper_service.get_jumper(
            bootstrapped_root, "default", UUID(jumper["id"])
        )
        with_creds = from_service.model_copy(
            update={
                "memberships": [
                    Membership(
                        org=OrgEnum.CSPA,
                        member_number="12345",
                        expiry_date=date(2027, 4, 29),
                        card_attachment_id=UUID(att_id),
                    ),
                ],
                "medicals": [
                    Medical(
                        kind=MedicalKind.CLASS_III,
                        issuing_authority="Transport Canada",
                        expiry_date=date(2028, 6, 15),
                        card_attachment_id=UUID(att_id),
                    ),
                ],
            },
        )
        _write_jumper(bootstrapped_root, with_creds)

        r = client.delete(
            f"/api/v1/jumpers/{jumper['id']}/attachments/{att_id}"
        )
        assert r.status_code == 409
        pointers = {e.get("pointer") for e in r.json().get("errors", [])}
        assert "#/memberships/0/card_attachment_id" in pointers
        assert "#/medicals/0/card_attachment_id" in pointers

    def test_referenced_attachment_remains_on_disk_after_409(
        self, client: TestClient, bootstrapped_root: Path
    ) -> None:
        from uuid import UUID

        jumper = _create_jumper_via_api(client)
        post_r = _post_attachment(client, jumper["id"], "card.pdf", b"data")
        att_id = post_r.json()["attachments"][0]["id"]

        from_service = jumper_service.get_jumper(
            bootstrapped_root, "default", UUID(jumper["id"])
        )
        with_credential = from_service.model_copy(
            update={
                "memberships": [
                    Membership(
                        org=OrgEnum.CSPA,
                        member_number="12345",
                        expiry_date=date(2027, 4, 29),
                        card_attachment_id=UUID(att_id),
                    ),
                ],
            },
        )
        _write_jumper(bootstrapped_root, with_credential)

        disk_path = (
            bootstrapped_root
            / JUMPERS_DIRNAME
            / jumper["id"]
            / ATTACHMENTS_DIRNAME
            / f"{att_id}__card.pdf"
        )
        client.delete(
            f"/api/v1/jumpers/{jumper['id']}/attachments/{att_id}"
        )
        # The file must survive a rejected deletion.
        assert disk_path.is_file()
        assert disk_path.read_bytes() == b"data"
