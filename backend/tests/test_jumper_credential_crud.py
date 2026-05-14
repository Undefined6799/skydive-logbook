"""Phase D.2 — CRUD for the four remaining credential collections.

Memberships were proven in Phase D.1 (test_jumper_membership_crud.py).
This file applies the same pattern to:

  * cops (Certificates of Proficiency / licenses)
  * ratings (federation-issued)
  * tandem_ratings (manufacturer-issued)
  * medicals

Per-collection tests focus on what's distinctive to that collection:

  * **cops** — per-org ``level`` enum (CSPACopLevel / USPACopLevel /
    free text for OTHER) enforced via Pydantic.
  * **ratings** — per-org ``code`` enum (CSPARatingCode /
    USPARatingCode / free text for OTHER).
  * **tandem_ratings** — closed ``system`` enum, ``system_other``
    cross-field rule, ``currency_reset_at`` round-trips.
  * **medicals** — closed ``kind`` enum (``class_iii`` only in v0.1).

Cross-cutting properties already proven in D.1 (cross-reference
validation, "other collections untouched", REST envelope, request id)
are spot-checked here rather than re-tested per collection — D.1's
guarantees apply because all five collections share the
``_persist_with_credentials_update`` helper.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.api.deps import get_logbook_root, get_user_id
from backend.api.errors import (
    NotFoundError,
    ValidationFailedError,
)
from backend.api.rest import create_app
from backend.models.jumper import (
    CopCreate,
    FederationRatingCreate,
    JumperCreate,
    MedicalCreate,
    MedicalKind,
    OrgEnum,
    TandemRatingCreate,
    TandemSystem,
)
from backend.services import jumper_service
from backend.services.jumper_credential_service import (
    add_cop_to_jumper,
    add_medical_to_jumper,
    add_rating_to_jumper,
    add_tandem_rating_to_jumper,
    delete_cop_from_jumper,
    delete_medical_from_jumper,
    delete_rating_from_jumper,
    delete_tandem_rating_from_jumper,
    update_cop_on_jumper,
    update_medical_on_jumper,
    update_rating_on_jumper,
    update_tandem_rating_on_jumper,
)
from backend.services.jumper_service import (
    Upload,
    add_attachment_to_jumper,
)
from backend.storage.bootstrap import bootstrap_logbook


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


def _attach(bootstrapped_root: Path, jumper_id: UUID) -> UUID:
    updated = add_attachment_to_jumper(
        bootstrapped_root,
        "default",
        jumper_id,
        Upload(filename="card.pdf", content_type="application/pdf", chunks=[b"data"]),
    )
    return updated.attachments[-1].id


# --------------------------------------------------------------------- #
# CoPs
# --------------------------------------------------------------------- #

class TestCops:
    def _payload(self, **overrides) -> CopCreate:
        base = {
            "org": OrgEnum.CSPA,
            "level": "d",
            "issued_date": date(2024, 6, 15),
        }
        base.update(overrides)
        return CopCreate(**base)

    def test_add_appends_cop(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        updated = add_cop_to_jumper(
            bootstrapped_root, "default", jumper_id, self._payload()
        )
        assert len(updated.cops) == 1
        assert updated.cops[0].level == "d"

    def test_update_replaces_cop(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        added = add_cop_to_jumper(
            bootstrapped_root, "default", jumper_id, self._payload()
        )
        cop_id = added.cops[0].id
        updated = update_cop_on_jumper(
            bootstrapped_root,
            "default",
            jumper_id,
            cop_id,
            self._payload(level="c"),
        )
        assert updated.cops[0].id == cop_id
        assert updated.cops[0].level == "c"

    def test_delete_removes_cop(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        added = add_cop_to_jumper(
            bootstrapped_root, "default", jumper_id, self._payload()
        )
        cop_id = added.cops[0].id
        result = delete_cop_from_jumper(
            bootstrapped_root, "default", jumper_id, cop_id
        )
        assert result.cops == []

    def test_unknown_cop_id_404(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        with pytest.raises(NotFoundError):
            update_cop_on_jumper(
                bootstrapped_root,
                "default",
                jumper_id,
                uuid4(),
                self._payload(),
            )

    def test_card_attachment_id_must_exist(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        with pytest.raises(ValidationFailedError):
            add_cop_to_jumper(
                bootstrapped_root,
                "default",
                jumper_id,
                self._payload(card_attachment_id=uuid4()),
            )

    @pytest.mark.parametrize(
        "org,bad_level",
        [
            (OrgEnum.CSPA, "banana"),  # not a CSPA level
            (OrgEnum.USPA, "solo"),  # solo is CSPA-only
        ],
    )
    def test_bad_level_for_org_rejected_by_pydantic(
        self, org: OrgEnum, bad_level: str
    ) -> None:
        # Pydantic-side cross-field check fires before any service runs.
        with pytest.raises(ValidationError):
            CopCreate(
                org=org, level=bad_level, issued_date=date(2024, 6, 15)
            )

    def test_other_org_with_arbitrary_level_works(self) -> None:
        cop = CopCreate(
            org=OrgEnum.OTHER,
            org_other="Federation X",
            level="cat-a",
            issued_date=date(2024, 6, 15),
        )
        assert cop.level == "cat-a"

    def test_rest_post_201(self, client: TestClient, jumper_id: UUID) -> None:
        r = client.post(
            f"/api/v1/jumpers/{jumper_id}/cops",
            json={
                "org": "CSPA",
                "level": "d",
                "issued_date": "2024-06-15",
            },
        )
        assert r.status_code == 201
        assert r.json()["cops"][0]["level"] == "d"

    def test_rest_post_bad_level_422(
        self, client: TestClient, jumper_id: UUID
    ) -> None:
        # Pydantic-level body validation returns FastAPI's default
        # 422 envelope (application/json), not RFC 9457. This is a
        # documented carve-out in backend/api/rest.py — RequestValidationError
        # retains its native handler. The status code is the load-
        # bearing assertion here.
        r = client.post(
            f"/api/v1/jumpers/{jumper_id}/cops",
            json={
                "org": "USPA",
                "level": "solo",  # CSPA-only
                "issued_date": "2024-06-15",
            },
        )
        assert r.status_code == 422


# --------------------------------------------------------------------- #
# Federation ratings
# --------------------------------------------------------------------- #

class TestRatings:
    def _payload(self, **overrides) -> FederationRatingCreate:
        base = {
            "org": OrgEnum.CSPA,
            "code": "pffi",
            "expiry_date": date(2027, 4, 29),
        }
        base.update(overrides)
        return FederationRatingCreate(**base)

    def test_add_appends_rating(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        updated = add_rating_to_jumper(
            bootstrapped_root, "default", jumper_id, self._payload()
        )
        assert updated.ratings[0].code == "pffi"

    def test_update_replaces_rating(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        added = add_rating_to_jumper(
            bootstrapped_root, "default", jumper_id, self._payload()
        )
        rid = added.ratings[0].id
        updated = update_rating_on_jumper(
            bootstrapped_root,
            "default",
            jumper_id,
            rid,
            self._payload(code="c1"),
        )
        assert updated.ratings[0].code == "c1"

    def test_delete_removes_rating(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        added = add_rating_to_jumper(
            bootstrapped_root, "default", jumper_id, self._payload()
        )
        rid = added.ratings[0].id
        delete_rating_from_jumper(
            bootstrapped_root, "default", jumper_id, rid
        )
        # GET now returns no ratings
        fetched = jumper_service.get_jumper(
            bootstrapped_root, "default", jumper_id
        )
        assert fetched.ratings == []

    def test_unknown_rating_id_404(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        with pytest.raises(NotFoundError):
            delete_rating_from_jumper(
                bootstrapped_root, "default", jumper_id, uuid4()
            )

    @pytest.mark.parametrize(
        "org,bad_code",
        [
            (OrgEnum.CSPA, "affi"),  # USPA code, not CSPA
            (OrgEnum.USPA, "pffi"),  # CSPA code, not USPA
        ],
    )
    def test_bad_code_for_org_rejected(
        self, org: OrgEnum, bad_code: str
    ) -> None:
        with pytest.raises(ValidationError):
            FederationRatingCreate(
                org=org, code=bad_code, expiry_date=date(2027, 4, 29)
            )

    def test_rest_post_201(self, client: TestClient, jumper_id: UUID) -> None:
        r = client.post(
            f"/api/v1/jumpers/{jumper_id}/ratings",
            json={
                "org": "USPA",
                "code": "affi",
                "expiry_date": "2027-04-29",
            },
        )
        assert r.status_code == 201
        assert r.json()["ratings"][0]["code"] == "affi"


# --------------------------------------------------------------------- #
# Tandem ratings
# --------------------------------------------------------------------- #

class TestTandemRatings:
    def _payload(self, **overrides) -> TandemRatingCreate:
        base = {
            "system": TandemSystem.UPT_SIGMA,
            "expiry_date": date(2027, 4, 29),
        }
        base.update(overrides)
        return TandemRatingCreate(**base)

    def test_add_appends_tandem(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        updated = add_tandem_rating_to_jumper(
            bootstrapped_root, "default", jumper_id, self._payload()
        )
        assert updated.tandem_ratings[0].system == TandemSystem.UPT_SIGMA

    def test_update_preserves_currency_reset(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        # currency_reset_at is part of the request body so a PUT
        # must round-trip it. (D47: the reset is the only piece of
        # currency state stored in XML.)
        added = add_tandem_rating_to_jumper(
            bootstrapped_root,
            "default",
            jumper_id,
            self._payload(currency_reset_at=date(2026, 4, 15)),
        )
        tid = added.tandem_ratings[0].id
        updated = update_tandem_rating_on_jumper(
            bootstrapped_root,
            "default",
            jumper_id,
            tid,
            self._payload(currency_reset_at=date(2026, 7, 1)),
        )
        assert updated.tandem_ratings[0].currency_reset_at == date(2026, 7, 1)

    def test_delete_removes_tandem(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        added = add_tandem_rating_to_jumper(
            bootstrapped_root, "default", jumper_id, self._payload()
        )
        tid = added.tandem_ratings[0].id
        result = delete_tandem_rating_from_jumper(
            bootstrapped_root, "default", jumper_id, tid
        )
        assert result.tandem_ratings == []

    def test_other_system_requires_system_other(self) -> None:
        with pytest.raises(ValidationError):
            TandemRatingCreate(
                system=TandemSystem.OTHER, expiry_date=date(2027, 4, 29)
            )

    def test_known_system_rejects_system_other(self) -> None:
        with pytest.raises(ValidationError):
            TandemRatingCreate(
                system=TandemSystem.UPT_SIGMA,
                system_other="should not be set",
                expiry_date=date(2027, 4, 29),
            )

    def test_other_system_with_system_other_works(self) -> None:
        t = TandemRatingCreate(
            system=TandemSystem.OTHER,
            system_other="JumpShack Racer",
            expiry_date=date(2027, 4, 29),
        )
        assert t.system_other == "JumpShack Racer"

    def test_rest_post_201_with_currency_reset(
        self, client: TestClient, jumper_id: UUID
    ) -> None:
        r = client.post(
            f"/api/v1/jumpers/{jumper_id}/tandem-ratings",
            json={
                "system": "upt_sigma",
                "expiry_date": "2027-04-29",
                "currency_reset_at": "2026-04-15",
            },
        )
        assert r.status_code == 201
        body = r.json()
        assert body["tandem_ratings"][0]["system"] == "upt_sigma"
        assert body["tandem_ratings"][0]["currency_reset_at"] == "2026-04-15"

    def test_rest_post_other_without_system_other_422(
        self, client: TestClient, jumper_id: UUID
    ) -> None:
        r = client.post(
            f"/api/v1/jumpers/{jumper_id}/tandem-ratings",
            json={"system": "other", "expiry_date": "2027-04-29"},
        )
        assert r.status_code == 422


# --------------------------------------------------------------------- #
# Medicals
# --------------------------------------------------------------------- #

class TestMedicals:
    def _payload(self, **overrides) -> MedicalCreate:
        base = {
            "kind": MedicalKind.CLASS_III,
            "issuing_authority": "Transport Canada",
            "expiry_date": date(2028, 6, 15),
        }
        base.update(overrides)
        return MedicalCreate(**base)

    def test_add_appends_medical(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        updated = add_medical_to_jumper(
            bootstrapped_root, "default", jumper_id, self._payload()
        )
        assert len(updated.medicals) == 1
        assert updated.medicals[0].issuing_authority == "Transport Canada"

    def test_update_replaces_medical(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        added = add_medical_to_jumper(
            bootstrapped_root, "default", jumper_id, self._payload()
        )
        mid = added.medicals[0].id
        updated = update_medical_on_jumper(
            bootstrapped_root,
            "default",
            jumper_id,
            mid,
            self._payload(issuing_authority="FAA"),
        )
        assert updated.medicals[0].issuing_authority == "FAA"

    def test_delete_removes_medical(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        added = add_medical_to_jumper(
            bootstrapped_root, "default", jumper_id, self._payload()
        )
        mid = added.medicals[0].id
        result = delete_medical_from_jumper(
            bootstrapped_root, "default", jumper_id, mid
        )
        assert result.medicals == []

    def test_unknown_medical_404(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        with pytest.raises(NotFoundError):
            update_medical_on_jumper(
                bootstrapped_root,
                "default",
                jumper_id,
                uuid4(),
                self._payload(),
            )

    def test_unknown_kind_rejected_by_pydantic(self) -> None:
        with pytest.raises(ValidationError):
            MedicalCreate(
                kind="class_i",  # Not in MedicalKind v0.1
                issuing_authority="FAA",
                expiry_date=date(2028, 6, 15),
            )

    def test_rest_post_201(self, client: TestClient, jumper_id: UUID) -> None:
        r = client.post(
            f"/api/v1/jumpers/{jumper_id}/medicals",
            json={
                "kind": "class_iii",
                "issuing_authority": "Transport Canada",
                "expiry_date": "2028-06-15",
            },
        )
        assert r.status_code == 201
        assert r.json()["medicals"][0]["kind"] == "class_iii"


# --------------------------------------------------------------------- #
# Cross-cutting: card_attachment_id valid for every collection
# --------------------------------------------------------------------- #

class TestCrossReferenceAcrossCollections:
    """The credential→attachment cross-reference fires uniformly
    across every collection — not just memberships (D.1)."""

    @pytest.mark.parametrize(
        "endpoint,body",
        [
            (
                "cops",
                lambda att: {
                    "org": "CSPA",
                    "level": "d",
                    "issued_date": "2024-06-15",
                    "card_attachment_id": str(att),
                },
            ),
            (
                "ratings",
                lambda att: {
                    "org": "CSPA",
                    "code": "pffi",
                    "expiry_date": "2027-04-29",
                    "card_attachment_id": str(att),
                },
            ),
            (
                "tandem-ratings",
                lambda att: {
                    "system": "upt_sigma",
                    "expiry_date": "2027-04-29",
                    "card_attachment_id": str(att),
                },
            ),
            (
                "medicals",
                lambda att: {
                    "kind": "class_iii",
                    "issuing_authority": "Transport Canada",
                    "expiry_date": "2028-06-15",
                    "card_attachment_id": str(att),
                },
            ),
        ],
    )
    def test_unknown_attachment_id_returns_422(
        self,
        client: TestClient,
        jumper_id: UUID,
        endpoint: str,
        body,
    ) -> None:
        r = client.post(
            f"/api/v1/jumpers/{jumper_id}/{endpoint}",
            json=body(uuid4()),
        )
        assert r.status_code == 422
        pointers = [
            e.get("pointer") for e in r.json().get("errors", [])
        ]
        assert "#/card_attachment_id" in pointers

    @pytest.mark.parametrize(
        "endpoint,body",
        [
            (
                "cops",
                lambda att: {
                    "org": "CSPA",
                    "level": "d",
                    "issued_date": "2024-06-15",
                    "card_attachment_id": str(att),
                },
            ),
            (
                "ratings",
                lambda att: {
                    "org": "CSPA",
                    "code": "pffi",
                    "expiry_date": "2027-04-29",
                    "card_attachment_id": str(att),
                },
            ),
            (
                "tandem-ratings",
                lambda att: {
                    "system": "upt_sigma",
                    "expiry_date": "2027-04-29",
                    "card_attachment_id": str(att),
                },
            ),
            (
                "medicals",
                lambda att: {
                    "kind": "class_iii",
                    "issuing_authority": "Transport Canada",
                    "expiry_date": "2028-06-15",
                    "card_attachment_id": str(att),
                },
            ),
        ],
    )
    def test_existing_attachment_id_works(
        self,
        client: TestClient,
        bootstrapped_root: Path,
        jumper_id: UUID,
        endpoint: str,
        body,
    ) -> None:
        att_id = _attach(bootstrapped_root, jumper_id)
        r = client.post(
            f"/api/v1/jumpers/{jumper_id}/{endpoint}",
            json=body(att_id),
        )
        assert r.status_code == 201


# --------------------------------------------------------------------- #
# All five collections coexist on one jumper (sanity check)
# --------------------------------------------------------------------- #

def test_one_jumper_can_carry_all_five_collections(
    client: TestClient, jumper_id: UUID
) -> None:
    """Sanity: a single jumper can carry one of each credential kind
    simultaneously, GET returns all five, and the records survive
    serialize/parse round-trips."""
    client.post(
        f"/api/v1/jumpers/{jumper_id}/memberships",
        json={"org": "CSPA", "member_number": "1", "expiry_date": "2027-04-29"},
    )
    client.post(
        f"/api/v1/jumpers/{jumper_id}/cops",
        json={"org": "CSPA", "level": "d", "issued_date": "2024-06-15"},
    )
    client.post(
        f"/api/v1/jumpers/{jumper_id}/ratings",
        json={"org": "CSPA", "code": "pffi", "expiry_date": "2027-04-29"},
    )
    client.post(
        f"/api/v1/jumpers/{jumper_id}/tandem-ratings",
        json={"system": "upt_sigma", "expiry_date": "2027-04-29"},
    )
    client.post(
        f"/api/v1/jumpers/{jumper_id}/medicals",
        json={
            "kind": "class_iii",
            "issuing_authority": "Transport Canada",
            "expiry_date": "2028-06-15",
        },
    )

    body = client.get(f"/api/v1/jumpers/{jumper_id}").json()
    assert len(body["memberships"]) == 1
    assert len(body["cops"]) == 1
    assert len(body["ratings"]) == 1
    assert len(body["tandem_ratings"]) == 1
    assert len(body["medicals"]) == 1
