"""D35 derived jump counts on Main / AAD / Container (and Main's
nested current_lineset).

Each component's ``*_initial`` field is the editable seed (D34) and
``*_derived`` is the count of jumps logged against the rig the
component is currently on, computed from the SQLite jumps index by
``backend.services._wear_counts``. The display value is
``*_total = initial + derived`` (a Pydantic computed_field on the
response model). XML on disk stays clean — only ``<*_initial>`` is
persisted, per D35 §"Component XSDs declare only the
``<*_initial>`` fields".

The bug this guards against: logging a jump with ``rig_id`` set
must surface as a higher ``jump_count_total`` on every component
currently assigned to that rig. Before the D35 wiring landed, the
service layer returned only ``*_initial`` and the frontend had to
walk the jumps list client-side to bump the displayed number — a
fragile workaround that doubled the responsibility for the same
fact and broke when a single piece of it drifted.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from backend.models._component_base import ComponentStatus
from backend.models.aad import AADCreate
from backend.models.container import ContainerCreate
from backend.models.jump import JumpCreate
from backend.models.main import Lineset, MainCreate
from backend.models.reserve import ReserveCreate
from backend.models.rig import Jurisdiction, RigCreate
from backend.services import (
    aad_service,
    container_service,
    jump_service,
    main_service,
    reserve_service,
    rig_service,
)
from backend.storage.bootstrap import bootstrap_logbook
from backend.storage.index import open_index


@pytest.fixture
def bootstrapped_root(logbook_root: Path) -> Path:
    bootstrap_logbook(logbook_root)
    # Open + close once so the v10 schema is in place before the
    # first create_jump call. create_jump opens the index itself,
    # but priming makes the test deterministic regardless of which
    # service runs first.
    open_index(logbook_root).conn.close()
    return logbook_root


def _seed_rig(
    root: Path,
    *,
    main_initial: int = 0,
    aad_initial: int = 0,
    container_initial: int = 0,
    lineset_initial: int = 0,
):
    """Create four components + a rig assembling them.

    Returns ``(rig, main_id, reserve_id, aad_id, container_id)`` so
    tests can address each piece by id.
    """
    main = main_service.create_main(
        root,
        "default",
        MainCreate(
            jump_count_initial=main_initial,
            current_lineset=Lineset(
                line_type="Vectran V750",
                breaking_strength_lb=750.0,
                install_date=date(2025, 1, 15),
                jumps_on_lineset_initial=lineset_initial,
            ),
        ),
    )
    reserve = reserve_service.create_reserve(
        root,
        "default",
        ReserveCreate(),
    )
    aad = aad_service.create_aad(
        root,
        "default",
        AADCreate(jump_count_initial=aad_initial),
    )
    container = container_service.create_container(
        root,
        "default",
        ContainerCreate(jump_count_initial=container_initial),
    )
    rig = rig_service.create_rig(
        root,
        "default",
        RigCreate(
            nickname="Black Cobra",
            jurisdiction=Jurisdiction.USPA,
            current_main_id=main.id,
            current_reserve_id=reserve.id,
            current_aad_id=aad.id,
            current_container_id=container.id,
        ),
    )
    return rig, main.id, reserve.id, aad.id, container.id


def _log_jump(
    root: Path,
    *,
    jump_number: int,
    rig_id=None,
) -> None:
    """Minimal jump payload with optional rig_id."""
    jump_service.create_jump(
        root,
        "default",
        JumpCreate(
            jump_number=jump_number,
            date=date(2026, 4, 22),
            dropzone="Skydive Elsinore",
            exit_altitude_m=4000,
            deployment_altitude_m=900,
            rig_id=rig_id,
        ),
    )


class TestUnassigned:
    """A component not assigned to any rig must report derived == 0
    even when jumps exist on other rigs in the logbook."""

    def test_freshly_created_main_has_zero_derived(
        self, bootstrapped_root: Path
    ):
        m = main_service.create_main(
            bootstrapped_root, "default", MainCreate(jump_count_initial=7)
        )
        # Read it back through get_main so the enrichment step runs.
        loaded = main_service.get_main(bootstrapped_root, "default", m.id)
        assert loaded.jump_count_initial == 7
        assert loaded.jump_count_derived == 0
        assert loaded.jump_count_total == 7

    def test_unassigned_container_ignores_other_rig_jumps(
        self, bootstrapped_root: Path
    ):
        # Rig + its components on disk.
        rig, _main_id, _r, _a, _c = _seed_rig(bootstrapped_root)
        # An UNASSIGNED container that should not pick up the rig's jump.
        loose = container_service.create_container(
            bootstrapped_root,
            "default",
            ContainerCreate(jump_count_initial=10),
        )
        _log_jump(bootstrapped_root, jump_number=1, rig_id=rig.id)

        loaded = container_service.get_container(
            bootstrapped_root, "default", loose.id
        )
        assert loaded.assigned_rig_id is None
        assert loaded.jump_count_derived == 0
        assert loaded.jump_count_total == 10


class TestAssigned:
    """Components on a rig pick up the rig's jump count."""

    def test_main_jump_count_derived_matches_rig_jumps(
        self, bootstrapped_root: Path
    ):
        rig, main_id, _r, _a, _c = _seed_rig(
            bootstrapped_root, main_initial=500
        )
        _log_jump(bootstrapped_root, jump_number=1, rig_id=rig.id)
        _log_jump(bootstrapped_root, jump_number=2, rig_id=rig.id)

        loaded = main_service.get_main(bootstrapped_root, "default", main_id)
        assert loaded.jump_count_initial == 500
        assert loaded.jump_count_derived == 2
        assert loaded.jump_count_total == 502

    def test_aad_jump_count_derived_matches_rig_jumps(
        self, bootstrapped_root: Path
    ):
        rig, _m, _r, aad_id, _c = _seed_rig(
            bootstrapped_root, aad_initial=300
        )
        _log_jump(bootstrapped_root, jump_number=1, rig_id=rig.id)

        loaded = aad_service.get_aad(bootstrapped_root, "default", aad_id)
        assert loaded.jump_count_initial == 300
        assert loaded.jump_count_derived == 1
        assert loaded.jump_count_total == 301

    def test_container_jump_count_derived_matches_rig_jumps(
        self, bootstrapped_root: Path
    ):
        rig, _m, _r, _a, container_id = _seed_rig(
            bootstrapped_root, container_initial=700
        )
        _log_jump(bootstrapped_root, jump_number=1, rig_id=rig.id)
        _log_jump(bootstrapped_root, jump_number=2, rig_id=rig.id)
        _log_jump(bootstrapped_root, jump_number=3, rig_id=rig.id)

        loaded = container_service.get_container(
            bootstrapped_root, "default", container_id
        )
        assert loaded.jump_count_initial == 700
        assert loaded.jump_count_derived == 3
        assert loaded.jump_count_total == 703

    def test_lineset_jumps_on_lineset_derived_matches_rig_jumps(
        self, bootstrapped_root: Path
    ):
        """D46: lineset jumps also pick up the rig count in v0.1.

        The proper attribution walks rig-snapshot.xml (R.4); until
        then the lineset rides on the same approximation as the
        main itself.
        """
        rig, main_id, _r, _a, _c = _seed_rig(
            bootstrapped_root, lineset_initial=120
        )
        _log_jump(bootstrapped_root, jump_number=1, rig_id=rig.id)

        loaded = main_service.get_main(bootstrapped_root, "default", main_id)
        assert loaded.current_lineset is not None
        ls = loaded.current_lineset
        assert ls.jumps_on_lineset_initial == 120
        assert ls.jumps_on_lineset_derived == 1
        assert ls.jumps_on_lineset_total == 121

    def test_jumps_without_rig_id_dont_contribute(
        self, bootstrapped_root: Path
    ):
        rig, main_id, _r, _a, _c = _seed_rig(bootstrapped_root)
        # One jump on the rig; one quick-log jump with no rig_id.
        _log_jump(bootstrapped_root, jump_number=1, rig_id=rig.id)
        _log_jump(bootstrapped_root, jump_number=2, rig_id=None)

        loaded = main_service.get_main(bootstrapped_root, "default", main_id)
        assert loaded.jump_count_derived == 1


class TestListAndUpdatePaths:
    """list_* / update_* responses must carry the same derived
    counts as get_* — clients render against either path."""

    def test_list_mains_stamps_derived(self, bootstrapped_root: Path):
        rig, main_id, _r, _a, _c = _seed_rig(bootstrapped_root)
        _log_jump(bootstrapped_root, jump_number=1, rig_id=rig.id)
        _log_jump(bootstrapped_root, jump_number=2, rig_id=rig.id)

        listed = main_service.list_mains(bootstrapped_root, "default")
        assert len(listed) == 1
        assert listed[0].id == main_id
        assert listed[0].jump_count_derived == 2

    def test_list_aads_stamps_derived(self, bootstrapped_root: Path):
        rig, _m, _r, aad_id, _c = _seed_rig(bootstrapped_root)
        _log_jump(bootstrapped_root, jump_number=1, rig_id=rig.id)

        listed = aad_service.list_aads(bootstrapped_root, "default")
        assert len(listed) == 1
        assert listed[0].id == aad_id
        assert listed[0].jump_count_derived == 1

    def test_list_containers_stamps_derived(self, bootstrapped_root: Path):
        rig, _m, _r, _a, container_id = _seed_rig(bootstrapped_root)
        _log_jump(bootstrapped_root, jump_number=1, rig_id=rig.id)

        listed = container_service.list_containers(
            bootstrapped_root, "default"
        )
        assert len(listed) == 1
        assert listed[0].id == container_id
        assert listed[0].jump_count_derived == 1

    def test_update_main_response_stamps_derived(
        self, bootstrapped_root: Path
    ):
        from backend.models.main import MainUpdate

        rig, main_id, _r, _a, _c = _seed_rig(
            bootstrapped_root, main_initial=10
        )
        _log_jump(bootstrapped_root, jump_number=1, rig_id=rig.id)
        # Read current to echo its lineset shape on the PUT body.
        current = main_service.get_main(
            bootstrapped_root, "default", main_id
        )
        updated = main_service.update_main(
            bootstrapped_root,
            "default",
            main_id,
            MainUpdate(
                status=ComponentStatus.ACTIVE,
                jump_count_initial=10,
                current_lineset=current.current_lineset,
            ),
        )
        assert updated.jump_count_derived == 1
        assert updated.jump_count_total == 11


class TestXMLStaysClean:
    """The derived fields must NOT bleed into the on-disk XML.
    D35 is explicit that only ``<*_initial>`` lives in the XSD."""

    def test_main_xml_has_no_derived_element(
        self, bootstrapped_root: Path
    ):
        rig, main_id, _r, _a, _c = _seed_rig(
            bootstrapped_root, main_initial=42
        )
        _log_jump(bootstrapped_root, jump_number=1, rig_id=rig.id)
        # Force a write path that runs through the enrichment + service
        # round trip. A subsequent disk read should NOT see <jump_count_derived>.
        main_service.get_main(bootstrapped_root, "default", main_id)
        xml = (
            bootstrapped_root
            / "inventory"
            / "mains"
            / f"{main_id}.xml"
        ).read_text()
        assert "<jump_count_initial>42</jump_count_initial>" in xml
        assert "jump_count_derived" not in xml
        assert "jump_count_total" not in xml
        assert "jumps_on_lineset_derived" not in xml
        assert "jumps_on_lineset_total" not in xml

    def test_container_xml_has_no_derived_element(
        self, bootstrapped_root: Path
    ):
        rig, _m, _r, _a, container_id = _seed_rig(
            bootstrapped_root, container_initial=99
        )
        _log_jump(bootstrapped_root, jump_number=1, rig_id=rig.id)
        container_service.get_container(
            bootstrapped_root, "default", container_id
        )
        xml = (
            bootstrapped_root
            / "inventory"
            / "containers"
            / f"{container_id}.xml"
        ).read_text()
        assert "jump_count_derived" not in xml
        assert "jump_count_total" not in xml

    def test_aad_xml_has_no_derived_element(
        self, bootstrapped_root: Path
    ):
        rig, _m, _r, aad_id, _c = _seed_rig(
            bootstrapped_root, aad_initial=55
        )
        _log_jump(bootstrapped_root, jump_number=1, rig_id=rig.id)
        aad_service.get_aad(bootstrapped_root, "default", aad_id)
        xml = (
            bootstrapped_root
            / "inventory"
            / "aads"
            / f"{aad_id}.xml"
        ).read_text()
        assert "jump_count_derived" not in xml
        assert "jump_count_total" not in xml
