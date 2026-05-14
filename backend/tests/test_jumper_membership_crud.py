"""Phase D.1 — Memberships CRUD: service + REST integration.

Both layers exercised in one file because the membership CRUD
surface is small and the same fixtures cover both:

  * **Service:** ``add_membership_to_jumper`` /
    ``update_membership_on_jumper`` /
    ``delete_membership_from_jumper`` against a real ``tmp_path``-
    backed logbook root.
  * **REST:** ``POST`` / ``PUT`` / ``DELETE`` under
    ``/api/v1/jumpers/{id}/memberships`` through the TestClient,
    asserting D16 problem+json on errors and D27 ``X-Request-Id``.

Key surfaces covered:
  * Cross-reference validation: ``card_attachment_id`` referencing
    a non-existent attachment fails with 422 + ``#/card_attachment_id``.
  * Cross-field rules from the Pydantic ``MembershipCreate`` (org=OTHER
    needs org_other, etc.) surface as 422.
  * Updates preserve the other collections — adding a membership
    doesn't touch medicals, attachments stay intact, etc.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_logbook_root, get_user_id
from backend.api.errors import PROBLEM_JSON_MEDIA_TYPE, NotFoundError, ValidationFailedError
from backend.api.rest import create_app
from backend.models.jumper import (
    JumperCreate,
    Medical,
    MedicalKind,
    MembershipCreate,
    OrgEnum,
)
from backend.services import jumper_service
from backend.services.jumper_credential_service import (
    add_membership_to_jumper,
    delete_membership_from_jumper,
    update_membership_on_jumper,
)
from backend.services.jumper_service import (
    Upload,
    _write_jumper,
    add_attachment_to_jumper,
)
from backend.storage.bootstrap import bootstrap_logbook

# --------------------------------------------------------------------- #
# Fixtures shared by service + REST tests
# --------------------------------------------------------------------- #

@pytest.fixture
def bootstrapped_root(tmp_path: Path) -> Path:
    root = tmp_path / "logbook"
    bootstrap_logbook(root)
    return root


@pytest.fixture
def jumper_id(bootstrapped_root: Path) -> UUID:
    j = jumper_service.create_jumper(
        bootstrapped_root, "default", JumperCreate(exit_weight_lb=180)
    )
    return j.id


@pytest.fixture
def client(bootstrapped_root: Path) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_logbook_root] = lambda: bootstrapped_root
    app.dependency_overrides[get_user_id] = lambda: "default"
    return TestClient(app)


def _membership_payload(**overrides) -> MembershipCreate:
    base = {
        "org": OrgEnum.CSPA,
        "member_number": "12345",
        "expiry_date": date(2027, 4, 29),
    }
    base.update(overrides)
    return MembershipCreate(**base)


def _membership_body(**overrides) -> dict:
    """JSON body shape for the REST endpoint."""
    base = {
        "org": "CSPA",
        "member_number": "12345",
        "expiry_date": "2027-04-29",
    }
    base.update(overrides)
    return base


def _attach_card(
    bootstrapped_root: Path, jumper_id: UUID, payload: bytes = b"PDF bytes"
) -> UUID:
    """Helper: upload one attachment, return its id."""
    updated = add_attachment_to_jumper(
        bootstrapped_root,
        "default",
        jumper_id,
        Upload(filename="card.pdf", content_type="application/pdf", chunks=[payload]),
    )
    return updated.attachments[-1].id


# --------------------------------------------------------------------- #
# Service: add_membership_to_jumper
# --------------------------------------------------------------------- #

class TestServiceAdd:
    def test_appends_membership(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        updated = add_membership_to_jumper(
            bootstrapped_root, "default", jumper_id, _membership_payload()
        )
        assert len(updated.memberships) == 1
        m = updated.memberships[0]
        assert m.org == OrgEnum.CSPA
        assert m.member_number == "12345"
        assert m.expiry_date == date(2027, 4, 29)

    def test_server_mints_membership_id(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        updated = add_membership_to_jumper(
            bootstrapped_root, "default", jumper_id, _membership_payload()
        )
        assert isinstance(updated.memberships[0].id, UUID)

    def test_two_adds_get_distinct_ids(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        first = add_membership_to_jumper(
            bootstrapped_root, "default", jumper_id, _membership_payload()
        )
        second = add_membership_to_jumper(
            bootstrapped_root,
            "default",
            jumper_id,
            _membership_payload(org=OrgEnum.USPA, member_number="987654"),
        )
        assert (
            first.memberships[0].id != second.memberships[1].id
        )

    def test_persists_through_get_jumper(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        added = add_membership_to_jumper(
            bootstrapped_root, "default", jumper_id, _membership_payload()
        )
        fetched = jumper_service.get_jumper(
            bootstrapped_root, "default", jumper_id
        )
        assert fetched.memberships == added.memberships

    def test_unknown_jumper_raises_not_found(
        self, bootstrapped_root: Path
    ) -> None:
        with pytest.raises(NotFoundError):
            add_membership_to_jumper(
                bootstrapped_root, "default", uuid4(), _membership_payload()
            )

    def test_card_attachment_id_must_exist(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        with pytest.raises(ValidationFailedError) as exc_info:
            add_membership_to_jumper(
                bootstrapped_root,
                "default",
                jumper_id,
                _membership_payload(card_attachment_id=uuid4()),
            )
        pointers = [e.pointer for e in exc_info.value.errors]
        assert "#/card_attachment_id" in pointers

    def test_card_attachment_id_existing_works(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        att_id = _attach_card(bootstrapped_root, jumper_id)
        added = add_membership_to_jumper(
            bootstrapped_root,
            "default",
            jumper_id,
            _membership_payload(card_attachment_id=att_id),
        )
        assert added.memberships[0].card_attachment_id == att_id

    def test_other_collections_untouched(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        # Pre-seed a medical via _write_jumper so we can assert that
        # adding a membership doesn't drop or mutate the medical.
        jumper = jumper_service.get_jumper(bootstrapped_root, "default", jumper_id)
        _write_jumper(
            bootstrapped_root,
            jumper.model_copy(
                update={
                    "medicals": [
                        Medical(
                            kind=MedicalKind.CLASS_III,
                            issuing_authority="Transport Canada",
                            expiry_date=date(2028, 6, 15),
                        ),
                    ],
                }
            ),
        )

        added = add_membership_to_jumper(
            bootstrapped_root, "default", jumper_id, _membership_payload()
        )
        assert len(added.medicals) == 1
        assert added.medicals[0].issuing_authority == "Transport Canada"

    def test_other_org_requires_org_other(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        # Pydantic's MembershipCreate validator rejects org=OTHER
        # without org_other before the service runs. The REST layer
        # surfaces this as 422; here we assert the model rejects
        # construction so the rule is encoded at the type level.
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            _membership_payload(org=OrgEnum.OTHER)


# --------------------------------------------------------------------- #
# Service: update_membership_on_jumper
# --------------------------------------------------------------------- #

class TestServiceUpdate:
    def test_replaces_existing_membership(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        added = add_membership_to_jumper(
            bootstrapped_root, "default", jumper_id, _membership_payload()
        )
        m_id = added.memberships[0].id

        updated = update_membership_on_jumper(
            bootstrapped_root,
            "default",
            jumper_id,
            m_id,
            _membership_payload(member_number="updated-99999"),
        )
        assert len(updated.memberships) == 1
        assert updated.memberships[0].id == m_id
        assert updated.memberships[0].member_number == "updated-99999"

    def test_preserves_membership_id_across_replace(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        # Even if the body somehow drifted (it doesn't carry id), the
        # URL id wins. Re-PUT with the same payload preserves the id.
        added = add_membership_to_jumper(
            bootstrapped_root, "default", jumper_id, _membership_payload()
        )
        m_id = added.memberships[0].id
        updated = update_membership_on_jumper(
            bootstrapped_root,
            "default",
            jumper_id,
            m_id,
            _membership_payload(),
        )
        assert updated.memberships[0].id == m_id

    def test_unknown_membership_id_raises_not_found(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        with pytest.raises(NotFoundError):
            update_membership_on_jumper(
                bootstrapped_root,
                "default",
                jumper_id,
                uuid4(),
                _membership_payload(),
            )

    def test_card_attachment_id_must_exist_on_put(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        added = add_membership_to_jumper(
            bootstrapped_root, "default", jumper_id, _membership_payload()
        )
        m_id = added.memberships[0].id
        with pytest.raises(ValidationFailedError) as exc_info:
            update_membership_on_jumper(
                bootstrapped_root,
                "default",
                jumper_id,
                m_id,
                _membership_payload(card_attachment_id=uuid4()),
            )
        pointers = [e.pointer for e in exc_info.value.errors]
        assert "#/card_attachment_id" in pointers

    def test_other_memberships_unchanged(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        first = add_membership_to_jumper(
            bootstrapped_root, "default", jumper_id, _membership_payload()
        )
        second = add_membership_to_jumper(
            bootstrapped_root,
            "default",
            jumper_id,
            _membership_payload(org=OrgEnum.USPA, member_number="987654"),
        )
        first_id = first.memberships[0].id
        second_id = second.memberships[1].id

        # Update ONLY the second one.
        updated = update_membership_on_jumper(
            bootstrapped_root,
            "default",
            jumper_id,
            second_id,
            _membership_payload(org=OrgEnum.USPA, member_number="updated"),
        )
        # First membership untouched.
        first_after = next(m for m in updated.memberships if m.id == first_id)
        assert first_after.member_number == "12345"
        # Second membership replaced.
        second_after = next(m for m in updated.memberships if m.id == second_id)
        assert second_after.member_number == "updated"


# --------------------------------------------------------------------- #
# Service: delete_membership_from_jumper
# --------------------------------------------------------------------- #

class TestServiceDelete:
    def test_removes_one_membership(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        added = add_membership_to_jumper(
            bootstrapped_root, "default", jumper_id, _membership_payload()
        )
        m_id = added.memberships[0].id

        updated = delete_membership_from_jumper(
            bootstrapped_root, "default", jumper_id, m_id
        )
        assert updated.memberships == []

    def test_unknown_membership_id_raises_not_found(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        with pytest.raises(NotFoundError):
            delete_membership_from_jumper(
                bootstrapped_root, "default", jumper_id, uuid4()
            )

    def test_attachment_referenced_by_deleted_membership_remains(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        # Deleting a membership does NOT cascade-delete its attachment.
        # The card may still be valid even if the membership is no
        # longer being tracked.
        att_id = _attach_card(bootstrapped_root, jumper_id)
        added = add_membership_to_jumper(
            bootstrapped_root,
            "default",
            jumper_id,
            _membership_payload(card_attachment_id=att_id),
        )
        m_id = added.memberships[0].id

        updated = delete_membership_from_jumper(
            bootstrapped_root, "default", jumper_id, m_id
        )
        # Attachment still in the list.
        assert any(a.id == att_id for a in updated.attachments)


# --------------------------------------------------------------------- #
# REST integration
# --------------------------------------------------------------------- #

class TestRestPost:
    def test_returns_201_and_jumper_with_membership(
        self, client: TestClient, jumper_id: UUID
    ) -> None:
        r = client.post(
            f"/api/v1/jumpers/{jumper_id}/memberships",
            json=_membership_body(),
        )
        assert r.status_code == 201
        body = r.json()
        assert len(body["memberships"]) == 1
        assert body["memberships"][0]["org"] == "CSPA"

    def test_unknown_jumper_returns_404(self, client: TestClient) -> None:
        r = client.post(
            f"/api/v1/jumpers/{uuid4()}/memberships",
            json=_membership_body(),
        )
        assert r.status_code == 404
        assert r.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)

    def test_other_org_without_org_other_returns_422(
        self, client: TestClient, jumper_id: UUID
    ) -> None:
        # Pydantic-side cross-field rule fires before the service.
        r = client.post(
            f"/api/v1/jumpers/{jumper_id}/memberships",
            json=_membership_body(org="OTHER"),
        )
        assert r.status_code == 422

    def test_unknown_card_attachment_id_returns_422(
        self, client: TestClient, jumper_id: UUID
    ) -> None:
        r = client.post(
            f"/api/v1/jumpers/{jumper_id}/memberships",
            json=_membership_body(card_attachment_id=str(uuid4())),
        )
        assert r.status_code == 422
        body = r.json()
        pointers = [e.get("pointer") for e in body.get("errors", [])]
        assert "#/card_attachment_id" in pointers

    def test_response_has_request_id_header(
        self, client: TestClient, jumper_id: UUID
    ) -> None:
        r = client.post(
            f"/api/v1/jumpers/{jumper_id}/memberships",
            json=_membership_body(),
        )
        assert "x-request-id" in {k.lower() for k in r.headers}


class TestRestPut:
    def test_replaces_membership(
        self, client: TestClient, jumper_id: UUID
    ) -> None:
        post_r = client.post(
            f"/api/v1/jumpers/{jumper_id}/memberships",
            json=_membership_body(),
        )
        m_id = post_r.json()["memberships"][0]["id"]

        r = client.put(
            f"/api/v1/jumpers/{jumper_id}/memberships/{m_id}",
            json=_membership_body(member_number="updated"),
        )
        assert r.status_code == 200
        memberships = r.json()["memberships"]
        assert len(memberships) == 1
        assert memberships[0]["id"] == m_id
        assert memberships[0]["member_number"] == "updated"

    def test_unknown_membership_returns_404(
        self, client: TestClient, jumper_id: UUID
    ) -> None:
        r = client.put(
            f"/api/v1/jumpers/{jumper_id}/memberships/{uuid4()}",
            json=_membership_body(),
        )
        assert r.status_code == 404


class TestRestDelete:
    def test_removes_membership(
        self, client: TestClient, jumper_id: UUID
    ) -> None:
        post_r = client.post(
            f"/api/v1/jumpers/{jumper_id}/memberships",
            json=_membership_body(),
        )
        m_id = post_r.json()["memberships"][0]["id"]

        r = client.delete(
            f"/api/v1/jumpers/{jumper_id}/memberships/{m_id}"
        )
        assert r.status_code == 200
        assert r.json()["memberships"] == []

    def test_unknown_membership_returns_404(
        self, client: TestClient, jumper_id: UUID
    ) -> None:
        r = client.delete(
            f"/api/v1/jumpers/{jumper_id}/memberships/{uuid4()}"
        )
        assert r.status_code == 404


class TestRestEndToEnd:
    def test_full_lifecycle(self, client: TestClient, jumper_id: UUID) -> None:
        # POST one CSPA membership, then a USPA one. PUT the CSPA to
        # update its number. DELETE the USPA. GET to confirm.
        cspa = client.post(
            f"/api/v1/jumpers/{jumper_id}/memberships",
            json=_membership_body(org="CSPA", member_number="12345"),
        ).json()["memberships"][0]
        client.post(
            f"/api/v1/jumpers/{jumper_id}/memberships",
            json=_membership_body(org="USPA", member_number="987654"),
        )
        client.put(
            f"/api/v1/jumpers/{jumper_id}/memberships/{cspa['id']}",
            json=_membership_body(org="CSPA", member_number="updated-12345"),
        )
        # Find the USPA membership id and delete it.
        list_r = client.get(f"/api/v1/jumpers/{jumper_id}")
        uspa = next(
            m for m in list_r.json()["memberships"] if m["org"] == "USPA"
        )
        client.delete(
            f"/api/v1/jumpers/{jumper_id}/memberships/{uspa['id']}"
        )

        final = client.get(f"/api/v1/jumpers/{jumper_id}").json()
        memberships = final["memberships"]
        assert len(memberships) == 1
        assert memberships[0]["org"] == "CSPA"
        assert memberships[0]["member_number"] == "updated-12345"
