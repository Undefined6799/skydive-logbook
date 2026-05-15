"""D70 rig partial-create recovery: ``folder_reconcile_rigs``.

These tests pin the recovery flow for the failure mode described in
``reviews/2026-05-15-chatgpt-findings-deep-dive.md`` §8.1:
``create_rig`` writes rig.xml referencing four components, then
iterates the D37 assignment loop. A crash partway through that loop
leaves the rig folder on disk, the first iterations' components
pointing back at the rig, and the remaining components still
unassigned.

Per D70 the recovery policy is **forward-complete**: the on-disk
rig.xml is the authoritative statement of intent, so reconcile
brings each component the rig references into agreement. Orphan
assignments (component points at a rig that doesn't exist or doesn't
reference back) are cleared.

Each test uses a real ``tmp_path``-backed logbook root per CLAUDE.md
§7 — integration tests for storage primitives must touch a real
directory, not mocks.
"""
from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest

from backend.models._component_base import ComponentStatus
from backend.models.aad import AADCreate
from backend.models.container import ContainerCreate
from backend.models.main import MainCreate
from backend.models.reserve import ReserveCreate
from backend.models.rig import Jurisdiction, RigCreate
from backend.services import (
    aad_service,
    container_service,
    main_service,
    reserve_service,
    rig_reconcile_service,
    rig_service,
)
from backend.storage.bootstrap import bootstrap_logbook


@pytest.fixture
def bootstrapped_root(logbook_root: Path) -> Path:
    bootstrap_logbook(logbook_root)
    return logbook_root


def _seed_components(root: Path) -> dict[str, UUID]:
    main = main_service.create_main(
        root,
        "default",
        MainCreate(status=ComponentStatus.ACTIVE, jump_count_initial=0),
    )
    reserve = reserve_service.create_reserve(
        root,
        "default",
        ReserveCreate(
            status=ComponentStatus.ACTIVE,
            repack_count_initial=0,
            ride_count_initial=0,
        ),
    )
    aad = aad_service.create_aad(
        root,
        "default",
        AADCreate(
            status=ComponentStatus.ACTIVE,
            jump_count_initial=0,
            fire_count_initial=0,
        ),
    )
    container = container_service.create_container(
        root,
        "default",
        ContainerCreate(status=ComponentStatus.ACTIVE, jump_count_initial=0),
    )
    return {
        "main": main.id,
        "reserve": reserve.id,
        "aad": aad.id,
        "container": container.id,
    }


def _build_rig_payload(components: dict[str, UUID], **overrides) -> RigCreate:
    base: dict = {
        "nickname": "Black Cobra",
        "jurisdiction": Jurisdiction.USPA,
        "current_main_id": components["main"],
        "current_reserve_id": components["reserve"],
        "current_aad_id": components["aad"],
        "current_container_id": components["container"],
    }
    base.update(overrides)
    return RigCreate(**base)


class TestCleanBootIsANoOp:
    def test_empty_logbook(self, bootstrapped_root: Path):
        report = rig_reconcile_service.folder_reconcile_rigs(bootstrapped_root)
        assert report.rigs_scanned == 0
        assert report.components_scanned == 0
        assert report.components_forward_completed == 0
        assert report.components_cleared == 0
        assert report.conflicts == ()

    def test_healthy_rig_no_repairs(self, bootstrapped_root: Path):
        # A normally-created rig has bidirectional refs intact —
        # reconcile must touch nothing.
        components = _seed_components(bootstrapped_root)
        rig_service.create_rig(
            bootstrapped_root, "default", _build_rig_payload(components)
        )

        # Snapshot the four component XMLs before reconcile.
        main_path = bootstrapped_root / "inventory" / "mains" / (
            f"{components['main']}.xml"
        )
        before = main_path.read_bytes()

        report = rig_reconcile_service.folder_reconcile_rigs(bootstrapped_root)
        assert report.rigs_scanned == 1
        assert report.components_scanned == 4
        assert report.components_forward_completed == 0
        assert report.components_cleared == 0
        assert report.conflicts == ()

        # Component XML byte-identical → no atomic_write happened.
        assert main_path.read_bytes() == before

    def test_idempotent_second_run(self, bootstrapped_root: Path):
        components = _seed_components(bootstrapped_root)
        rig_service.create_rig(
            bootstrapped_root, "default", _build_rig_payload(components)
        )
        rig_reconcile_service.folder_reconcile_rigs(bootstrapped_root)
        report = rig_reconcile_service.folder_reconcile_rigs(bootstrapped_root)
        assert report.components_forward_completed == 0
        assert report.components_cleared == 0


class TestPartialCreateRecovery:
    def test_crash_after_first_assignment_forward_completes(
        self,
        bootstrapped_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        # Set up the partial-create state by raising in the
        # assignment loop after the first iteration (the main has
        # already been assigned). This is the §8.1 deep-dive scenario.
        # We monkeypatch the low-level ``_write_reserve`` rather than
        # the public ``set_assigned_rig_id`` because rig_service's
        # ``_COMPONENT_REGISTRY`` captured the function reference at
        # import time; patching the module attribute doesn't reach
        # the tuple-bound copy.
        components = _seed_components(bootstrapped_root)

        def boom(*args, **kwargs):
            raise RuntimeError("simulated crash mid-loop")

        monkeypatch.setattr(reserve_service, "_write_reserve", boom)

        with pytest.raises(RuntimeError, match="simulated crash"):
            rig_service.create_rig(
                bootstrapped_root,
                "default",
                _build_rig_payload(components),
            )

        # On-disk state after the crash: rig.xml exists referencing
        # all four components; main points back at the rig; reserve /
        # AAD / container still have None.
        rig_folder = bootstrapped_root / "rigs" / "Black Cobra"
        assert (rig_folder / "rig.xml").is_file()

        main = main_service.get_main(
            bootstrapped_root, "default", components["main"]
        )
        assert main.assigned_rig_id is not None
        partial_rig_id = main.assigned_rig_id

        reserve = reserve_service.get_reserve(
            bootstrapped_root, "default", components["reserve"]
        )
        assert reserve.assigned_rig_id is None

        aad = aad_service.get_aad(
            bootstrapped_root, "default", components["aad"]
        )
        assert aad.assigned_rig_id is None

        container = container_service.get_container(
            bootstrapped_root, "default", components["container"]
        )
        assert container.assigned_rig_id is None

        # Restore the real writer before running reconcile.
        monkeypatch.undo()

        # Run the reconcile.
        report = rig_reconcile_service.folder_reconcile_rigs(bootstrapped_root)

        # Three components needed forward-completion (reserve, AAD,
        # container); the main was already pointing at the rig.
        assert report.rigs_scanned == 1
        assert report.components_scanned == 4
        assert report.components_forward_completed == 3
        assert report.components_cleared == 0

        # All four components now point at the rig.
        for kind, getter, key in (
            ("main", main_service.get_main, "main"),
            ("reserve", reserve_service.get_reserve, "reserve"),
            ("aad", aad_service.get_aad, "aad"),
            ("container", container_service.get_container, "container"),
        ):
            comp = getter(bootstrapped_root, "default", components[key])
            assert comp.assigned_rig_id == partial_rig_id, (
                f"{kind} expected to point at {partial_rig_id}, "
                f"got {comp.assigned_rig_id}"
            )

    def test_retry_after_reconcile_is_idempotent(
        self,
        bootstrapped_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        # After reconcile heals the partial state, a second reconcile
        # is a no-op (no further repairs).
        components = _seed_components(bootstrapped_root)

        def boom(*args, **kwargs):
            raise RuntimeError("simulated crash")

        monkeypatch.setattr(aad_service, "_write_aad", boom)
        with pytest.raises(RuntimeError):
            rig_service.create_rig(
                bootstrapped_root,
                "default",
                _build_rig_payload(components),
            )
        monkeypatch.undo()

        rig_reconcile_service.folder_reconcile_rigs(bootstrapped_root)
        second = rig_reconcile_service.folder_reconcile_rigs(bootstrapped_root)
        assert second.components_forward_completed == 0
        assert second.components_cleared == 0


class TestOrphanCleanup:
    def test_component_pointing_at_missing_rig_is_cleared(
        self, bootstrapped_root: Path
    ):
        # Manually create a component with an assigned_rig_id that
        # doesn't correspond to any rig folder on disk. This is the
        # state left by hand-deleting a rig folder mid-recovery.
        components = _seed_components(bootstrapped_root)
        ghost_rig_id = uuid4()
        main_service.set_assigned_rig_id(
            bootstrapped_root, components["main"], ghost_rig_id
        )

        # Sanity check.
        m = main_service.get_main(
            bootstrapped_root, "default", components["main"]
        )
        assert m.assigned_rig_id == ghost_rig_id

        report = rig_reconcile_service.folder_reconcile_rigs(bootstrapped_root)
        assert report.components_cleared == 1
        assert report.components_forward_completed == 0

        m = main_service.get_main(
            bootstrapped_root, "default", components["main"]
        )
        assert m.assigned_rig_id is None

    def test_component_pointing_at_rig_that_does_not_ref_back_is_cleared(
        self, bootstrapped_root: Path
    ):
        # Create rig A (healthy). Then create a second main and
        # manually set its assigned_rig_id to A's id — A doesn't
        # reference this new main, so reconcile clears it.
        components_a = _seed_components(bootstrapped_root)
        rig_a = rig_service.create_rig(
            bootstrapped_root, "default", _build_rig_payload(components_a)
        )

        unrelated_main = main_service.create_main(
            bootstrapped_root,
            "default",
            MainCreate(status=ComponentStatus.ACTIVE, jump_count_initial=0),
        )
        main_service.set_assigned_rig_id(
            bootstrapped_root, unrelated_main.id, rig_a.id
        )

        report = rig_reconcile_service.folder_reconcile_rigs(bootstrapped_root)
        # rig_a's four components are healthy; only the unrelated
        # main has an orphan assignment.
        assert report.components_cleared == 1
        assert report.components_forward_completed == 0

        cleared = main_service.get_main(
            bootstrapped_root, "default", unrelated_main.id
        )
        assert cleared.assigned_rig_id is None

        # rig_a's own main is still attached.
        original = main_service.get_main(
            bootstrapped_root, "default", components_a["main"]
        )
        assert original.assigned_rig_id == rig_a.id


class TestConflictDetection:
    def test_two_rigs_claiming_one_component_logs_and_skips(
        self,
        bootstrapped_root: Path,
        caplog: pytest.LogCaptureFixture,
    ):
        # Construct the "two rigs claim the same component" state by
        # hand-editing one rig.xml to reference another rig's main.
        # (Pre-write D37 validation prevents this through the service,
        # so we go around it via the XML.)
        components_a = _seed_components(bootstrapped_root)
        rig_a = rig_service.create_rig(
            bootstrapped_root, "default", _build_rig_payload(components_a)
        )
        components_b = _seed_components(bootstrapped_root)
        rig_b = rig_service.create_rig(
            bootstrapped_root,
            "default",
            _build_rig_payload(components_b, nickname="Red Mamba"),
        )

        # Hand-edit rig_b's rig.xml to claim rig_a's main. We rewrite
        # the file using the service's serializer to keep the XSD
        # happy.
        from backend.xml.serialize import rig_to_bytes

        rig_b_xml_path = bootstrapped_root / "rigs" / "Red Mamba" / "rig.xml"
        tampered = rig_b.model_copy(
            update={"current_main_id": components_a["main"]}
        )
        rig_b_xml_path.write_bytes(rig_to_bytes(tampered))

        with caplog.at_level("WARNING", logger="backend.services.rig_reconcile"):
            report = rig_reconcile_service.folder_reconcile_rigs(bootstrapped_root)

        # The conflict was surfaced.
        assert len(report.conflicts) == 1
        assert any(
            rec.message == "rig_reconcile_conflict"
            for rec in caplog.records
        ), "expected a rig_reconcile_conflict log record"

        # The contested main was NOT modified — reconcile can't pick
        # a side. Other components still get reconciled normally.
        main = main_service.get_main(
            bootstrapped_root, "default", components_a["main"]
        )
        assert main.assigned_rig_id == rig_a.id

        # Rig B's other three components are still healthy (already
        # pointing at rig_b).
        for getter, key in (
            (reserve_service.get_reserve, "reserve"),
            (aad_service.get_aad, "aad"),
            (container_service.get_container, "container"),
        ):
            comp = getter(bootstrapped_root, "default", components_b[key])
            assert comp.assigned_rig_id == rig_b.id


class TestInvalidRigFoldersAreSkipped:
    def test_rig_folder_without_rig_xml_is_ignored(
        self, bootstrapped_root: Path
    ):
        # A partial-create stub: folder exists but no rig.xml. Per
        # D70 §"intentionally tolerant" this folder is skipped (the
        # rig didn't exist as far as reconcile is concerned), and any
        # components that pointed at the stub's would-be id get
        # cleared as orphans on the next pass.
        stub = bootstrapped_root / "rigs" / "Stub"
        stub.mkdir(parents=True)

        # Should not raise; should report zero rigs scanned.
        report = rig_reconcile_service.folder_reconcile_rigs(bootstrapped_root)
        assert report.rigs_scanned == 0
