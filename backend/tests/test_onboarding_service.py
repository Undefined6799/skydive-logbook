"""Service-layer tests for onboarding_service (D65).

Covers sentinel read/write + the three "has_*" detection helpers.
Each test uses a real ``tmp_path``-backed logbook (CLAUDE.md §7 —
integration tests for storage primitives must touch a real
directory, not mocks).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.models.dropzone import DropzoneCreate, Environment
from backend.models.onboarding import OnboardingComplete, OnboardingStatus
from backend.services import dropzone_service, onboarding_service
from backend.storage.bootstrap import bootstrap_logbook


@pytest.fixture
def bootstrapped_root(tmp_path: Path) -> Path:
    """A logbook root with bootstrap applied — XSDs, dropzones/, rigs/, …"""
    root = tmp_path / "logbook"
    bootstrap_logbook(root)
    return root


# --------------------------------------------------------------------------- #
# get_state — sentinel absent (fresh logbook)
# --------------------------------------------------------------------------- #

class TestGetStateFreshLogbook:
    def test_completed_is_false_when_sentinel_absent(
        self, bootstrapped_root: Path,
    ) -> None:
        state = onboarding_service.get_state(bootstrapped_root, "default")
        assert state.completed is False
        assert state.completed_at is None
        assert state.status is None

    def test_has_flags_are_false_in_empty_logbook(
        self, bootstrapped_root: Path,
    ) -> None:
        # Bootstrap creates the subfolders empty; no records yet.
        state = onboarding_service.get_state(bootstrapped_root, "default")
        assert state.has_jumper is False
        assert state.has_dropzones is False
        assert state.has_rigs is False

    def test_has_dropzones_true_after_create(
        self, bootstrapped_root: Path,
    ) -> None:
        dropzone_service.create_dropzone(
            bootstrapped_root,
            "default",
            DropzoneCreate(
                name="Skydive Elsinore",
                city="Lake Elsinore",
                country="US",
                environment=Environment.DUST_SAND_SALT,
            ),
        )
        state = onboarding_service.get_state(bootstrapped_root, "default")
        assert state.has_dropzones is True
        # Other flags unchanged.
        assert state.has_jumper is False
        assert state.has_rigs is False

    def test_has_rigs_true_when_rig_folder_present(
        self, bootstrapped_root: Path,
    ) -> None:
        # Simulate a rig folder without fully wiring rig_service —
        # the detector cares only about "any subfolder under rigs/".
        (bootstrapped_root / "rigs" / "test-rig").mkdir(parents=True)
        state = onboarding_service.get_state(bootstrapped_root, "default")
        assert state.has_rigs is True

    def test_has_jumper_true_when_jumper_folder_present(
        self, bootstrapped_root: Path,
    ) -> None:
        (bootstrapped_root / "jumpers" / "test-jumper").mkdir(parents=True)
        state = onboarding_service.get_state(bootstrapped_root, "default")
        assert state.has_jumper is True

    def test_missing_subdir_treated_as_no_records(self, tmp_path: Path) -> None:
        # No bootstrap — none of the subfolders exist. The detector
        # must not crash; it must just return False for everything.
        root = tmp_path / "fresh"
        root.mkdir()
        # ``_has_any_dropzone`` reads the index, which open_index
        # creates on first call. That's fine — the table is empty.
        state = onboarding_service.get_state(root, "default")
        assert state.has_jumper is False
        assert state.has_dropzones is False
        assert state.has_rigs is False


# --------------------------------------------------------------------------- #
# complete — sentinel write + idempotency
# --------------------------------------------------------------------------- #

class TestComplete:
    def test_writes_sentinel_with_status(self, bootstrapped_root: Path) -> None:
        onboarding_service.complete(
            bootstrapped_root,
            "default",
            OnboardingComplete(status=OnboardingStatus.FINISHED),
        )
        sentinel = bootstrapped_root / ".onboarding_completed"
        assert sentinel.is_file()
        body = json.loads(sentinel.read_text(encoding="utf-8"))
        assert body["status"] == "finished"
        # D17 timestamp shape: ISO 8601 UTC with Z suffix.
        assert body["completed_at"].endswith("Z")
        assert "T" in body["completed_at"]

    def test_returns_updated_state(self, bootstrapped_root: Path) -> None:
        state = onboarding_service.complete(
            bootstrapped_root,
            "default",
            OnboardingComplete(status=OnboardingStatus.SKIPPED),
        )
        assert state.completed is True
        assert state.status == OnboardingStatus.SKIPPED
        assert state.completed_at is not None

    def test_get_state_reflects_completion(
        self, bootstrapped_root: Path,
    ) -> None:
        onboarding_service.complete(
            bootstrapped_root,
            "default",
            OnboardingComplete(status=OnboardingStatus.FINISHED),
        )
        state = onboarding_service.get_state(bootstrapped_root, "default")
        assert state.completed is True
        assert state.status == OnboardingStatus.FINISHED

    def test_second_call_overwrites_status(
        self, bootstrapped_root: Path,
    ) -> None:
        # First a skip, then a finish — the sentinel reflects the latest.
        onboarding_service.complete(
            bootstrapped_root,
            "default",
            OnboardingComplete(status=OnboardingStatus.SKIPPED),
        )
        state = onboarding_service.complete(
            bootstrapped_root,
            "default",
            OnboardingComplete(status=OnboardingStatus.FINISHED),
        )
        assert state.status == OnboardingStatus.FINISHED

    def test_completion_does_not_alter_has_flags(
        self, bootstrapped_root: Path,
    ) -> None:
        # Skipping the wizard does NOT create records; the has_*
        # flags stay False so the resumption banner can surface.
        state = onboarding_service.complete(
            bootstrapped_root,
            "default",
            OnboardingComplete(status=OnboardingStatus.SKIPPED),
        )
        assert state.has_jumper is False
        assert state.has_dropzones is False
        assert state.has_rigs is False


# --------------------------------------------------------------------------- #
# get_state — malformed sentinel (parse-resilience)
# --------------------------------------------------------------------------- #

class TestSentinelParseResilience:
    def test_completed_true_for_empty_file(
        self, bootstrapped_root: Path,
    ) -> None:
        # An empty sentinel still counts as "done" — presence is the
        # load-bearing signal, not contents.
        (bootstrapped_root / ".onboarding_completed").write_bytes(b"")
        state = onboarding_service.get_state(bootstrapped_root, "default")
        assert state.completed is True
        assert state.completed_at is None
        assert state.status is None

    def test_completed_true_for_garbage_json(
        self, bootstrapped_root: Path,
    ) -> None:
        (bootstrapped_root / ".onboarding_completed").write_bytes(b"{not valid")
        state = onboarding_service.get_state(bootstrapped_root, "default")
        assert state.completed is True
        assert state.completed_at is None
        assert state.status is None

    def test_unknown_status_value_drops_to_null(
        self, bootstrapped_root: Path,
    ) -> None:
        (bootstrapped_root / ".onboarding_completed").write_text(
            json.dumps({"completed_at": "2026-05-14T12:00:00.000Z",
                        "status": "wat"}),
            encoding="utf-8",
        )
        state = onboarding_service.get_state(bootstrapped_root, "default")
        assert state.completed is True
        # completed_at parsed; status fell back to null.
        assert state.completed_at == "2026-05-14T12:00:00.000Z"
        assert state.status is None

    def test_non_object_body_treated_as_presence_only(
        self, bootstrapped_root: Path,
    ) -> None:
        (bootstrapped_root / ".onboarding_completed").write_text(
            '"just a string"', encoding="utf-8",
        )
        state = onboarding_service.get_state(bootstrapped_root, "default")
        assert state.completed is True
        assert state.completed_at is None
        assert state.status is None
