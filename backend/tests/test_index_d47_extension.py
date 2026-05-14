"""Phase D.4 — SQLite index extension for credentials + is_tandem on jumps.

Tests cover three load-bearing properties:

  * **Schema bump triggers drop-and-reindex.** Opening a v7 index
    against the v8 code drops every user table and re-installs the
    schema (D26).
  * **`is_tandem` lives on jumps.** create_jump and update_jump
    populate the column from the Pydantic model; reindex repopulates
    from jump.xml. The partial index on (user_id, date) WHERE
    is_tandem = 1 is in place.
  * **`jumper_credentials` projection covers four kinds.**
    Memberships, federation ratings, tandem ratings, and medicals
    project; CoPs do NOT (they have issued_date, no expiry).
    Reindex rebuilds the projection from jumper.xml; out-of-band
    deletions of jumpers drop their projection rows on next reindex.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import UUID

import pytest

from backend.models.jump import JumpCreate
from backend.models.jumper import (
    CopCreate,
    FederationRatingCreate,
    JumperCreate,
    MedicalCreate,
    MedicalKind,
    MembershipCreate,
    OrgEnum,
    TandemRatingCreate,
    TandemSystem,
)
from backend.services import (
    jump_service,
    jumper_credential_service,
    jumper_service,
)
from backend.services.reindex_service import reindex_from_xml
from backend.storage.bootstrap import bootstrap_logbook
from backend.storage.index import (
    INDEX_FILENAME,
    INDEX_SCHEMA_VERSION,
    open_index,
)


@pytest.fixture
def bootstrapped_root(tmp_path: Path) -> Path:
    root = tmp_path / "logbook"
    bootstrap_logbook(root)
    return root


# --------------------------------------------------------------------- #
# Schema version + bump-triggers-rebuild
# --------------------------------------------------------------------- #

class TestSchemaVersion:
    def test_current_schema_version_is_10(self) -> None:
        # If a future contributor bumps INDEX_SCHEMA_VERSION without
        # updating this test, the bump documentation in index.py is
        # the single source of truth — adjust the assertion in the
        # same commit.
        # v8 → v9 (D54, Phase 2b): added the ``people`` table.
        # v9 → v10 (D60): added ``dropzone_id`` column on jumps and
        # ``starred`` column on dropzones — see index.py header.
        assert INDEX_SCHEMA_VERSION == 10

    def test_fresh_index_has_current_tables(self, bootstrapped_root: Path) -> None:
        result = open_index(bootstrapped_root)
        try:
            tables = {
                row[0]
                for row in result.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        finally:
            result.conn.close()
        assert "jumps" in tables
        assert "dropzones" in tables
        assert "jumper_credentials" in tables
        # v9 (D54): the people table mirrors the dropzones flat
        # entity shape and lands at INDEX_SCHEMA_VERSION = 9.
        assert "people" in tables

    def test_old_index_triggers_drop_and_reindex(
        self, bootstrapped_root: Path
    ) -> None:
        # Stamp PRAGMA user_version to a non-current value, close,
        # reopen — the open should rebuild the schema and report
        # schema_was_rebuilt=True.
        result = open_index(bootstrapped_root)
        try:
            result.conn.execute("PRAGMA user_version = 7")
        finally:
            result.conn.close()

        result2 = open_index(bootstrapped_root)
        try:
            assert result2.schema_was_rebuilt
            assert result2.previous_version == 7
        finally:
            result2.conn.close()


# --------------------------------------------------------------------- #
# is_tandem column on jumps
# --------------------------------------------------------------------- #

class TestIsTandemColumn:
    def _create_jump(
        self, root: Path, *, is_tandem: bool | None = None
    ) -> UUID:
        payload = JumpCreate(
            jump_number=1,
            date=date(2026, 4, 29),
            dropzone="Skydive Test",
            exit_altitude_m=4000,
            deployment_altitude_m=900,
            is_tandem=is_tandem,
        )
        created = jump_service.create_jump(
            root, "default", payload, uploads=[]
        )
        return created.id

    def test_create_jump_persists_is_tandem_true(
        self, bootstrapped_root: Path
    ) -> None:
        jump_id = self._create_jump(bootstrapped_root, is_tandem=True)
        result = open_index(bootstrapped_root)
        try:
            row = result.conn.execute(
                "SELECT is_tandem FROM jumps WHERE id = ?",
                (str(jump_id),),
            ).fetchone()
        finally:
            result.conn.close()
        assert row["is_tandem"] == 1

    def test_create_jump_without_is_tandem_stores_null(
        self, bootstrapped_root: Path
    ) -> None:
        jump_id = self._create_jump(bootstrapped_root)  # is_tandem=None
        result = open_index(bootstrapped_root)
        try:
            row = result.conn.execute(
                "SELECT is_tandem FROM jumps WHERE id = ?",
                (str(jump_id),),
            ).fetchone()
        finally:
            result.conn.close()
        assert row["is_tandem"] is None

    def test_create_jump_with_is_tandem_false_stores_null(
        self, bootstrapped_root: Path
    ) -> None:
        # Per the model + service comment: None and False both map to
        # NULL on disk so a jump.xml that elides the element round-
        # trips byte-stable.
        jump_id = self._create_jump(bootstrapped_root, is_tandem=False)
        result = open_index(bootstrapped_root)
        try:
            row = result.conn.execute(
                "SELECT is_tandem FROM jumps WHERE id = ?",
                (str(jump_id),),
            ).fetchone()
        finally:
            result.conn.close()
        assert row["is_tandem"] is None

    def test_partial_index_on_tandem_jumps_exists(
        self, bootstrapped_root: Path
    ) -> None:
        result = open_index(bootstrapped_root)
        try:
            row = result.conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'index' AND name = 'idx_jumps_tandem'"
            ).fetchone()
        finally:
            result.conn.close()
        assert row is not None

    def test_reindex_repopulates_is_tandem_from_xml(
        self, bootstrapped_root: Path
    ) -> None:
        # Create a tandem jump, then delete the index file, then
        # reindex — the is_tandem column must come back from the
        # jump.xml claims.
        jump_id = self._create_jump(bootstrapped_root, is_tandem=True)

        (bootstrapped_root / INDEX_FILENAME).unlink()

        report = reindex_from_xml(bootstrapped_root)
        assert report.aborted is None
        assert report.jumps_indexed == 1

        result = open_index(bootstrapped_root)
        try:
            row = result.conn.execute(
                "SELECT is_tandem FROM jumps WHERE id = ?",
                (str(jump_id),),
            ).fetchone()
        finally:
            result.conn.close()
        assert row["is_tandem"] == 1


# --------------------------------------------------------------------- #
# jumper_credentials projection
# --------------------------------------------------------------------- #

class TestJumperCredentialsProjection:
    def _setup_jumper_with_all_kinds(
        self, root: Path
    ) -> tuple[UUID, dict[str, UUID]]:
        """Helper: create a jumper carrying one of each kind. Returns
        the jumper id and a mapping of kind→credential id."""
        j = jumper_service.create_jumper(
            root, "default", JumperCreate(exit_weight_lb=180)
        )
        membership = jumper_credential_service.add_membership_to_jumper(
            root,
            "default",
            j.id,
            MembershipCreate(
                org=OrgEnum.CSPA,
                member_number="12345",
                expiry_date=date(2027, 4, 29),
            ),
        )
        cop = jumper_credential_service.add_cop_to_jumper(
            root,
            "default",
            j.id,
            CopCreate(
                org=OrgEnum.CSPA,
                level="d",
                issued_date=date(2024, 6, 15),
            ),
        )
        rating = jumper_credential_service.add_rating_to_jumper(
            root,
            "default",
            j.id,
            FederationRatingCreate(
                org=OrgEnum.CSPA,
                code="pffi",
                expiry_date=date(2027, 3, 31),
            ),
        )
        tandem = jumper_credential_service.add_tandem_rating_to_jumper(
            root,
            "default",
            j.id,
            TandemRatingCreate(
                system=TandemSystem.UPT_SIGMA,
                expiry_date=date(2027, 4, 29),
            ),
        )
        medical = jumper_credential_service.add_medical_to_jumper(
            root,
            "default",
            j.id,
            MedicalCreate(
                kind=MedicalKind.CLASS_III,
                issuing_authority="Transport Canada",
                expiry_date=date(2028, 6, 15),
            ),
        )
        ids = {
            "membership": membership.memberships[-1].id,
            "cop": cop.cops[-1].id,
            "rating": rating.ratings[-1].id,
            "tandem_rating": tandem.tandem_ratings[-1].id,
            "medical": medical.medicals[-1].id,
        }
        return j.id, ids

    def test_reindex_projects_four_kinds_excluding_cops(
        self, bootstrapped_root: Path
    ) -> None:
        jumper_id, ids = self._setup_jumper_with_all_kinds(bootstrapped_root)

        report = reindex_from_xml(bootstrapped_root)
        assert report.aborted is None
        # Four credential kinds get projected (memberships, ratings,
        # tandem ratings, medicals); CoPs are excluded.
        assert report.jumper_credentials_indexed == 4
        assert report.jumpers_scanned == 1

        result = open_index(bootstrapped_root)
        try:
            kinds = {
                row[0]
                for row in result.conn.execute(
                    "SELECT kind FROM jumper_credentials WHERE jumper_id = ?",
                    (str(jumper_id),),
                ).fetchall()
            }
        finally:
            result.conn.close()
        assert kinds == {
            "membership",
            "federation_rating",
            "tandem_rating",
            "medical",
        }
        # CoP id must not appear.
        all_ids = {
            UUID(row[0]) for row in _all_credential_ids(bootstrapped_root)
        }
        assert ids["cop"] not in all_ids

    def test_projection_carries_discriminator_per_kind(
        self, bootstrapped_root: Path
    ) -> None:
        jumper_id, _ = self._setup_jumper_with_all_kinds(bootstrapped_root)
        reindex_from_xml(bootstrapped_root)

        result = open_index(bootstrapped_root)
        try:
            rows = result.conn.execute(
                "SELECT kind, discriminator FROM jumper_credentials "
                "WHERE jumper_id = ? ORDER BY kind",
                (str(jumper_id),),
            ).fetchall()
        finally:
            result.conn.close()

        by_kind = {row["kind"]: row["discriminator"] for row in rows}
        assert by_kind["membership"] == "CSPA"
        assert by_kind["federation_rating"] == "CSPA"
        assert by_kind["tandem_rating"] == "upt_sigma"
        assert by_kind["medical"] == "class_iii"

    def test_projection_carries_expiry_dates(
        self, bootstrapped_root: Path
    ) -> None:
        jumper_id, _ = self._setup_jumper_with_all_kinds(bootstrapped_root)
        reindex_from_xml(bootstrapped_root)

        result = open_index(bootstrapped_root)
        try:
            rows = result.conn.execute(
                "SELECT kind, expiry_date FROM jumper_credentials "
                "WHERE jumper_id = ?",
                (str(jumper_id),),
            ).fetchall()
        finally:
            result.conn.close()

        by_kind = {row["kind"]: row["expiry_date"] for row in rows}
        assert by_kind["membership"] == "2027-04-29"
        assert by_kind["federation_rating"] == "2027-03-31"
        assert by_kind["tandem_rating"] == "2027-04-29"
        assert by_kind["medical"] == "2028-06-15"

    def test_reindex_clears_stale_credentials_after_jumper_deleted(
        self, bootstrapped_root: Path
    ) -> None:
        # Out-of-band jumper deletion (e.g. soft-delete) should leave
        # the projection without that jumper's credentials after the
        # next reindex.
        jumper_id, _ = self._setup_jumper_with_all_kinds(bootstrapped_root)
        reindex_from_xml(bootstrapped_root)

        # Delete the jumper folder out-of-band (mimics soft-delete
        # post-fact when reindex runs after a delete).
        jumper_service.delete_jumper(
            bootstrapped_root, "default", jumper_id
        )

        report = reindex_from_xml(bootstrapped_root)
        assert report.jumper_credentials_indexed == 0
        assert report.jumpers_scanned == 0

        result = open_index(bootstrapped_root)
        try:
            count = result.conn.execute(
                "SELECT COUNT(*) FROM jumper_credentials WHERE jumper_id = ?",
                (str(jumper_id),),
            ).fetchone()[0]
        finally:
            result.conn.close()
        assert count == 0

    def test_reindex_idempotent_with_credentials(
        self, bootstrapped_root: Path
    ) -> None:
        # Second consecutive reindex returns the same count and the
        # same row set.
        self._setup_jumper_with_all_kinds(bootstrapped_root)
        first = reindex_from_xml(bootstrapped_root)
        second = reindex_from_xml(bootstrapped_root)
        assert first.jumper_credentials_indexed == 4
        assert second.jumper_credentials_indexed == 4

    def test_projection_indexes_exist(
        self, bootstrapped_root: Path
    ) -> None:
        result = open_index(bootstrapped_root)
        try:
            indexes = {
                row[0]
                for row in result.conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'index' AND tbl_name = 'jumper_credentials'"
                ).fetchall()
            }
        finally:
            result.conn.close()
        assert "idx_jumper_credentials_jumper_expiry" in indexes
        assert "idx_jumper_credentials_expiry" in indexes


def _all_credential_ids(root: Path) -> list:
    """Helper for cross-test ID lookup."""
    result = open_index(root)
    try:
        return result.conn.execute(
            "SELECT id FROM jumper_credentials"
        ).fetchall()
    finally:
        result.conn.close()


# --------------------------------------------------------------------- #
# Jumpers folder skipped reasons
# --------------------------------------------------------------------- #

class TestJumperSkipPaths:
    def test_jumper_with_invalid_xml_skipped_with_warning(
        self, bootstrapped_root: Path
    ) -> None:
        # Set up: create one valid jumper, plus a folder with bad
        # jumper.xml. Reindex should index the valid one and report
        # the bad one as skipped.
        jumper_service.create_jumper(
            bootstrapped_root, "default", JumperCreate(exit_weight_lb=180)
        )
        bad_folder = (
            bootstrapped_root
            / "jumpers"
            / "deadbeef-dead-4eef-8eef-deadbeefdead"
        )
        bad_folder.mkdir()
        (bad_folder / "jumper.xml").write_bytes(b"<jumper>invalid</jumper>")

        report = reindex_from_xml(bootstrapped_root)
        # One scanned valid + one scanned invalid = 2 scanned.
        assert report.jumpers_scanned == 2
        assert len(report.jumpers_skipped) == 1
        assert any(
            "invalid" in reason for _, reason in report.jumpers_skipped
        )
