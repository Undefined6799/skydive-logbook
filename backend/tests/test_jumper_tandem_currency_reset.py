"""Phase D.3 — PATCH /tandem-ratings/{id}/currency-reset.

The one tandem-rating-specific operation D47 calls out: stamp
``currency_reset_at`` on a tandem rating to today's UTC date. The
Phase E currency calculator reads this field to suppress the not-
current warning when set within the system's currency window
(UPT 90 d, Strong 12 mo).

This file exercises both layers:
  * ``reset_tandem_rating_currency`` (service)
  * ``PATCH .../currency-reset`` (REST)
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_logbook_root, get_user_id
from backend.api.errors import NotFoundError
from backend.api.rest import create_app
from backend.models.jumper import (
    JumperCreate,
    TandemRatingCreate,
    TandemSystem,
)
from backend.services import jumper_service
from backend.services.jumper_credential_service import (
    add_tandem_rating_to_jumper,
    reset_tandem_rating_currency,
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


def _add_tandem(
    bootstrapped_root: Path,
    jumper_id: UUID,
    *,
    currency_reset_at: date | None = None,
) -> UUID:
    payload = TandemRatingCreate(
        system=TandemSystem.UPT_SIGMA,
        expiry_date=date(2027, 4, 29),
        currency_reset_at=currency_reset_at,
    )
    updated = add_tandem_rating_to_jumper(
        bootstrapped_root, "default", jumper_id, payload
    )
    return updated.tandem_ratings[-1].id


# --------------------------------------------------------------------- #
# Service-layer tests
# --------------------------------------------------------------------- #

class TestService:
    def test_stamps_currency_reset_to_today(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        tid = _add_tandem(bootstrapped_root, jumper_id)
        updated = reset_tandem_rating_currency(
            bootstrapped_root, "default", jumper_id, tid
        )
        target = next(t for t in updated.tandem_ratings if t.id == tid)
        assert target.currency_reset_at == datetime.now(UTC).date()

    def test_overwrites_existing_reset(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        # Adding a tandem with an existing reset date and re-running
        # should overwrite to today.
        tid = _add_tandem(
            bootstrapped_root,
            jumper_id,
            currency_reset_at=date(2025, 1, 1),
        )
        updated = reset_tandem_rating_currency(
            bootstrapped_root, "default", jumper_id, tid
        )
        target = next(t for t in updated.tandem_ratings if t.id == tid)
        assert target.currency_reset_at == datetime.now(UTC).date()
        # Original date is gone.
        assert target.currency_reset_at != date(2025, 1, 1)

    def test_other_fields_untouched(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        # Reset must not change system, expiry_date, card_attachment_id,
        # or notes — only currency_reset_at moves.
        tid = _add_tandem(bootstrapped_root, jumper_id)
        before = next(
            t
            for t in jumper_service.get_jumper(
                bootstrapped_root, "default", jumper_id
            ).tandem_ratings
            if t.id == tid
        )

        updated = reset_tandem_rating_currency(
            bootstrapped_root, "default", jumper_id, tid
        )
        after = next(t for t in updated.tandem_ratings if t.id == tid)

        assert after.system == before.system
        assert after.expiry_date == before.expiry_date
        assert after.card_attachment_id == before.card_attachment_id
        assert after.notes == before.notes

    def test_other_tandem_ratings_unchanged(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        # Two tandem ratings: reset only one; the other stays put.
        first = _add_tandem(bootstrapped_root, jumper_id)
        second = add_tandem_rating_to_jumper(
            bootstrapped_root,
            "default",
            jumper_id,
            TandemRatingCreate(
                system=TandemSystem.UPT_VECTOR,
                expiry_date=date(2027, 4, 29),
                currency_reset_at=date(2025, 1, 1),
            ),
        ).tandem_ratings[-1].id

        reset_tandem_rating_currency(
            bootstrapped_root, "default", jumper_id, first
        )

        # Second rating's currency_reset_at unchanged.
        fetched = jumper_service.get_jumper(
            bootstrapped_root, "default", jumper_id
        )
        second_record = next(
            t for t in fetched.tandem_ratings if t.id == second
        )
        assert second_record.currency_reset_at == date(2025, 1, 1)

    def test_unknown_jumper_404(self, bootstrapped_root: Path) -> None:
        with pytest.raises(NotFoundError):
            reset_tandem_rating_currency(
                bootstrapped_root, "default", uuid4(), uuid4()
            )

    def test_unknown_tandem_rating_id_404(
        self, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        with pytest.raises(NotFoundError):
            reset_tandem_rating_currency(
                bootstrapped_root, "default", jumper_id, uuid4()
            )


# --------------------------------------------------------------------- #
# REST integration
# --------------------------------------------------------------------- #

class TestRest:
    def test_patch_returns_updated_jumper(
        self, client: TestClient, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        tid = _add_tandem(bootstrapped_root, jumper_id)
        r = client.patch(
            f"/api/v1/jumpers/{jumper_id}/tandem-ratings/{tid}/currency-reset"
        )
        assert r.status_code == 200
        body = r.json()
        target = next(
            t for t in body["tandem_ratings"] if t["id"] == str(tid)
        )
        assert target["currency_reset_at"] == datetime.now(UTC).date().isoformat()

    def test_patch_unknown_jumper_404(self, client: TestClient) -> None:
        r = client.patch(
            f"/api/v1/jumpers/{uuid4()}/tandem-ratings/{uuid4()}/currency-reset"
        )
        assert r.status_code == 404

    def test_patch_unknown_tandem_rating_404(
        self, client: TestClient, jumper_id: UUID
    ) -> None:
        r = client.patch(
            f"/api/v1/jumpers/{jumper_id}/tandem-ratings/{uuid4()}/currency-reset"
        )
        assert r.status_code == 404

    def test_patch_no_body_required(
        self, client: TestClient, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        # The PATCH is body-less: no Content-Type, no payload. The
        # endpoint still succeeds.
        tid = _add_tandem(bootstrapped_root, jumper_id)
        r = client.patch(
            f"/api/v1/jumpers/{jumper_id}/tandem-ratings/{tid}/currency-reset"
        )
        assert r.status_code == 200

    def test_patch_response_has_request_id(
        self, client: TestClient, bootstrapped_root: Path, jumper_id: UUID
    ) -> None:
        tid = _add_tandem(bootstrapped_root, jumper_id)
        r = client.patch(
            f"/api/v1/jumpers/{jumper_id}/tandem-ratings/{tid}/currency-reset"
        )
        assert "x-request-id" in {k.lower() for k in r.headers}
