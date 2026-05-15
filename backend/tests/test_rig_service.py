"""Service-layer tests for rig_service (R.2.0b + R.2.0c.ii, D33, D37, D38).

R.2.0b covered create + get; R.2.0c.ii extends with list / update
(metadata-only with D37 swap-via-PUT rejection) / delete. The D37
cross-entity validation on create + delete (component-exists check,
mark-assigned, clear-on-delete) ships in R.2.0c.iii.

Each test uses a real ``tmp_path``-backed logbook root per CLAUDE.md
§7 (integration tests for storage primitives must touch a real
directory, not mocks).
"""
from __future__ import annotations

import logging
import time
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from backend.api.errors import (
    ComponentAlreadyAssigned,
    NotFoundError,
    RigComponentSwapUnsupported,
    RigNicknameConflict,
    ValidationFailedError,
)
from backend.models._component_base import ComponentStatus, NotesLogEntry
from backend.models.aad import AADCreate
from backend.models.container import ContainerCreate
from backend.models.main import MainCreate
from backend.models.reserve import ReserveCreate
from backend.models.rig import Jurisdiction, RepackEntry, RigCreate, RigUpdate
from backend.services import (
    aad_service,
    container_service,
    main_service,
    reserve_service,
    rig_service,
)
from backend.storage.bootstrap import bootstrap_logbook


@pytest.fixture
def bootstrapped_root(logbook_root: Path) -> Path:
    """A logbook root with bootstrap applied — XSDs + every subdir."""
    bootstrap_logbook(logbook_root)
    return logbook_root


def _seed_components(
    root: Path,
    *,
    main_status: ComponentStatus = ComponentStatus.ACTIVE,
    reserve_status: ComponentStatus = ComponentStatus.ACTIVE,
    aad_status: ComponentStatus = ComponentStatus.ACTIVE,
    container_status: ComponentStatus = ComponentStatus.ACTIVE,
) -> dict[str, UUID]:
    """Create one of each inventory component and return their ids.

    R.2.0c.iii.a's D37 cross-entity validation in ``create_rig``
    requires the four ``current_*_id`` refs to point at real, active,
    unassigned components. Every test that exercises a successful
    create needs four real components on disk; this helper makes
    that one call.

    R.2.0c.iii.b: ``assigned_rig_id`` is no longer on the *Create
    models (rig_service-owned). Tests that want pre-assigned
    components call ``set_assigned_rig_id`` after the create.

    Status overrides are for tests that exercise the D37 status
    check (e.g. retired reserve → 422 on create_rig).
    """
    main = main_service.create_main(
        root,
        "default",
        MainCreate(status=main_status, jump_count_initial=0),
    )
    reserve = reserve_service.create_reserve(
        root,
        "default",
        ReserveCreate(
            status=reserve_status,
            repack_count_initial=0,
            ride_count_initial=0,
        ),
    )
    aad = aad_service.create_aad(
        root,
        "default",
        AADCreate(
            status=aad_status,
            jump_count_initial=0,
            fire_count_initial=0,
        ),
    )
    container = container_service.create_container(
        root,
        "default",
        ContainerCreate(status=container_status, jump_count_initial=0),
    )
    return {
        "main": main.id,
        "reserve": reserve.id,
        "aad": aad.id,
        "container": container.id,
    }


def _create_payload(
    components: dict[str, UUID] | None = None,
    **overrides,
) -> RigCreate:
    """Build a RigCreate referencing the seeded ``components``.

    The legacy "random UUIDs" shape from R.2.0a/.b tests is gone:
    R.2.0c.iii.a enforces that every ref must resolve to an actual
    component. Tests that want a clean rig pass ``components=
    _seed_components(root)`` to thread the four real ids through.
    Tests that exercise validation failures override one ref to a
    deliberately-bad value.
    """
    if components is None:
        # The "no real components" path is only useful for tests
        # that exercise pre-validation failures (bad nickname,
        # bad jurisdiction). Those tests now have to rely on the
        # nickname check happening BEFORE the ref check (which it
        # does in create_rig). For safety, fall back to fresh
        # random UUIDs that will fail D37 validation — these
        # payloads should NOT be passed to create_rig.
        components = {
            "main": uuid4(),
            "reserve": uuid4(),
            "aad": uuid4(),
            "container": uuid4(),
        }
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


def _create_rig_with_seeded_components(
    bootstrapped_root: Path, **overrides
):
    """Shortcut: seed four components and create a rig using them.

    Used by every test that needs a "real" rig on disk to exercise
    the metadata path (update_rig metadata, delete_rig, get_rig,
    list_rigs). Returns the created Rig.
    """
    components = _seed_components(bootstrapped_root)
    return rig_service.create_rig(
        bootstrapped_root,
        "default",
        _create_payload(components, **overrides),
    )


def _update_payload_from(rig, **overrides) -> RigUpdate:
    """Build a RigUpdate that mirrors an existing Rig's refs.

    Tests use this to construct a "no-swap" PUT body: every
    current_*_id matches the on-disk rig, so the D37 swap check
    passes and we exercise only the metadata path. Tests for
    swap-via-PUT rejection override one ref explicitly.

    ``repack_history`` defaults to empty list (matches a pre-D66
    client that doesn't send the field) — the service then
    preserves the on-disk value per D66. Tests that exercise the
    new "replace via PUT" path supply ``repack_history`` as an
    override.
    """
    base: dict = {
        "nickname": rig.nickname,
        "jurisdiction": rig.jurisdiction,
        "current_main_id": rig.current_main_id,
        "current_reserve_id": rig.current_reserve_id,
        "current_aad_id": rig.current_aad_id,
        "current_container_id": rig.current_container_id,
        "notes_log": list(rig.notes_log),
    }
    base.update(overrides)
    return RigUpdate(**base)


# --------------------------------------------------------------------------- #
# create_rig — folder + manifest mechanics
# --------------------------------------------------------------------------- #


class TestCreate:
    def test_writes_rig_xml_at_nickname_folder(self, bootstrapped_root: Path):
        _create_rig_with_seeded_components(bootstrapped_root)
        rig_xml = bootstrapped_root / "rigs" / "Black Cobra" / "rig.xml"
        assert rig_xml.is_file(), f"expected rig.xml at {rig_xml}"

    def test_writes_manifest_alongside_rig_xml(self, bootstrapped_root: Path):
        _create_rig_with_seeded_components(bootstrapped_root)
        manifest = bootstrapped_root / "rigs" / "Black Cobra" / "SHA256SUMS"
        assert manifest.is_file(), f"expected SHA256SUMS at {manifest}"
        # The manifest covers exactly rig.xml — no attachments in v0.1.
        text = manifest.read_text("utf-8")
        assert "rig.xml" in text
        # Sanity: one line, the digest is 64 hex chars + two spaces + path.
        lines = [line for line in text.splitlines() if line.strip()]
        assert len(lines) == 1
        digest, rel = lines[0].split("  ", 1)
        assert len(digest) == 64
        assert rel == "rig.xml"

    def test_assigns_server_uuid(self, bootstrapped_root: Path):
        # RigCreate has no ``id`` field; the server mints one.
        r = _create_rig_with_seeded_components(bootstrapped_root)
        assert isinstance(r.id, UUID)

    def test_stamps_timestamps_together(self, bootstrapped_root: Path):
        r = _create_rig_with_seeded_components(bootstrapped_root)
        # Same instant for both — the service stamps once and reuses.
        assert r.created_at is not None and r.updated_at is not None
        assert r.created_at == r.updated_at

    def test_initial_repack_history_round_trips(self, bootstrapped_root: Path):
        # D38 onboarding path for used gear.
        history = [
            RepackEntry(
                date=date(2024, 11, 1),
                rigger="J. Dupont",
                jurisdiction_seal=Jurisdiction.USPA,
            ),
            RepackEntry(
                date=date(2025, 5, 1),
                rigger="J. Dupont",
                jurisdiction_seal=Jurisdiction.BOTH,
                notes="Annual + new closing loop",
            ),
        ]
        r = _create_rig_with_seeded_components(
            bootstrapped_root, repack_history=history
        )
        assert r.repack_history == history
        # And the disk read confirms it.
        fetched = rig_service.get_rig(bootstrapped_root, "default", r.id)
        assert fetched.repack_history == history

    def test_initial_notes_log_round_trips(self, bootstrapped_root: Path):
        notes = [
            NotesLogEntry(
                at="2026-04-28T14:30:00.000Z",
                text="Used purchase from local DZ",
            ),
        ]
        r = _create_rig_with_seeded_components(
            bootstrapped_root, notes_log=notes
        )
        fetched = rig_service.get_rig(bootstrapped_root, "default", r.id)
        assert fetched.notes_log == notes

    def test_d37_assigns_each_component_to_the_new_rig(
        self, bootstrapped_root: Path
    ):
        # R.2.0c.iii.a: after create_rig, every component should have
        # assigned_rig_id set to the new rig's id.
        components = _seed_components(bootstrapped_root)
        rig = rig_service.create_rig(
            bootstrapped_root,
            "default",
            _create_payload(components),
        )
        assert (
            main_service.get_main(
                bootstrapped_root, "default", components["main"]
            ).assigned_rig_id
            == rig.id
        )
        assert (
            reserve_service.get_reserve(
                bootstrapped_root, "default", components["reserve"]
            ).assigned_rig_id
            == rig.id
        )
        assert (
            aad_service.get_aad(
                bootstrapped_root, "default", components["aad"]
            ).assigned_rig_id
            == rig.id
        )
        assert (
            container_service.get_container(
                bootstrapped_root, "default", components["container"]
            ).assigned_rig_id
            == rig.id
        )

    def test_unicode_nickname_creates_unicode_folder(
        self, bootstrapped_root: Path
    ):
        r = _create_rig_with_seeded_components(
            bootstrapped_root, nickname="Élise — vol 1"
        )
        # NFC-normalized nickname is the folder name.
        folder = bootstrapped_root / "rigs" / "Élise — vol 1"
        assert folder.is_dir()
        assert (folder / "rig.xml").is_file()
        assert r.nickname == "Élise — vol 1"

    def test_each_jurisdiction_value_writes_cleanly(
        self, bootstrapped_root: Path
    ):
        # Each jurisdiction needs its own component set since the four
        # refs become assigned to that rig (and stay assigned, so they
        # can't be reused by the next iteration).
        for i, j in enumerate(Jurisdiction):
            r = _create_rig_with_seeded_components(
                bootstrapped_root, nickname=f"Rig-{i}", jurisdiction=j
            )
            assert r.jurisdiction == j


class TestCreateValidationErrors:
    def test_invalid_nickname_raises_422_with_pointer(
        self, bootstrapped_root: Path
    ):
        # Forbidden character in the nickname → folder-name sanitize
        # raises ValueError → service returns 422 with a #/nickname
        # pointer. The Pydantic model itself only enforces 1..120
        # length, so the slash here flows past Pydantic and is caught
        # at sanitize time — that's what we're pinning. The check
        # happens BEFORE the D37 validation, so random component
        # UUIDs don't trip the inventory lookup first.
        with pytest.raises(ValidationFailedError) as info:
            rig_service.create_rig(
                bootstrapped_root,
                "default",
                _create_payload(nickname="bad/name"),
            )
        assert info.value.errors is not None
        pointers = [e.pointer for e in info.value.errors]
        assert "#/nickname" in pointers

    def test_no_partial_folder_left_on_invalid_nickname(
        self, bootstrapped_root: Path
    ):
        # The sanitize check runs BEFORE mkdir — confirm no stub folder
        # was left behind for the next operator to wonder about.
        with pytest.raises(ValidationFailedError):
            rig_service.create_rig(
                bootstrapped_root,
                "default",
                _create_payload(nickname="bad/name"),
            )
        # rigs/ exists from bootstrap, but no children should be there.
        rigs_dir = bootstrapped_root / "rigs"
        assert list(rigs_dir.iterdir()) == []


class TestCreateD37Validation:
    """R.2.0c.iii.a: D37 cross-entity validation on create_rig.

    The four ``current_*_id`` refs must each:
      1. Resolve to an existing component file (else 422).
      2. Have ``status == active`` (else 422).
      3. Have ``assigned_rig_id`` either None or equal to the new
         rig (else 409 ``component_already_assigned``).
    """

    def test_unknown_main_id_raises_422_with_pointer(
        self, bootstrapped_root: Path
    ):
        components = _seed_components(bootstrapped_root)
        components["main"] = uuid4()  # bogus
        with pytest.raises(ValidationFailedError) as info:
            rig_service.create_rig(
                bootstrapped_root,
                "default",
                _create_payload(components),
            )
        pointers = [e.pointer for e in info.value.errors or []]
        assert "#/current_main_id" in pointers

    def test_retired_reserve_raises_422(self, bootstrapped_root: Path):
        components = _seed_components(
            bootstrapped_root,
            reserve_status=ComponentStatus.RETIRED,
        )
        with pytest.raises(ValidationFailedError) as info:
            rig_service.create_rig(
                bootstrapped_root,
                "default",
                _create_payload(components),
            )
        pointers = [e.pointer for e in info.value.errors or []]
        assert "#/current_reserve_id" in pointers

    def test_aad_already_on_other_rig_raises_409(
        self, bootstrapped_root: Path
    ):
        # First rig consumes one set of components.
        first_components = _seed_components(bootstrapped_root)
        rig_service.create_rig(
            bootstrapped_root,
            "default",
            _create_payload(first_components, nickname="First"),
        )
        # Second rig tries to reuse the AAD that's now on first.
        # Build a "fresh" set for the other three, but reuse the AAD.
        other = _seed_components(bootstrapped_root)
        other["aad"] = first_components["aad"]
        with pytest.raises(ComponentAlreadyAssigned) as info:
            rig_service.create_rig(
                bootstrapped_root,
                "default",
                _create_payload(other, nickname="Second"),
            )
        assert info.value.code == "component_already_assigned"
        pointers = [e.pointer for e in info.value.errors or []]
        assert "#/current_aad_id" in pointers

    def test_no_partial_assignment_on_validation_failure(
        self, bootstrapped_root: Path
    ):
        # Validation runs BEFORE any write — confirm a failed create
        # leaves the assigned_rig_id on the components untouched
        # (still None).
        components = _seed_components(bootstrapped_root)
        components["main"] = uuid4()  # bogus → 422 on first ref check
        with pytest.raises(ValidationFailedError):
            rig_service.create_rig(
                bootstrapped_root,
                "default",
                _create_payload(components),
            )
        # Reserve / AAD / container untouched.
        for kind, getter, key in (
            ("reserve", reserve_service.get_reserve, "reserve"),
            ("aad", aad_service.get_aad, "aad"),
            ("container", container_service.get_container, "container"),
        ):
            comp = getter(bootstrapped_root, "default", components[key])
            assert comp.assigned_rig_id is None, (
                f"{kind} got assigned_rig_id despite earlier validation "
                "failure"
            )

    def test_no_rig_folder_left_on_validation_failure(
        self, bootstrapped_root: Path
    ):
        components = _seed_components(bootstrapped_root)
        components["container"] = uuid4()  # bogus
        with pytest.raises(ValidationFailedError):
            rig_service.create_rig(
                bootstrapped_root,
                "default",
                _create_payload(components),
            )
        # No folder under rigs/ should have been created.
        assert list((bootstrapped_root / "rigs").iterdir()) == []


class TestRigNicknameConflict:
    def test_duplicate_nickname_raises_409(self, bootstrapped_root: Path):
        # Each rig needs its own four components — they're consumed
        # by the first rig and would fail D37 validation on the
        # second. Seed two sets, but the second create still fails
        # at the nickname-collision step before assignment.
        _create_rig_with_seeded_components(
            bootstrapped_root, nickname="My Rig"
        )
        components_b = _seed_components(bootstrapped_root)
        with pytest.raises(RigNicknameConflict) as info:
            rig_service.create_rig(
                bootstrapped_root,
                "default",
                _create_payload(components_b, nickname="My Rig"),
            )
        # The service should attach a #/nickname FieldError so the
        # REST surface can render a precise problem+json body.
        assert info.value.errors is not None
        assert any(e.pointer == "#/nickname" for e in info.value.errors)
        assert info.value.code == "rig_nickname_conflict"
        assert info.value.http_status == 409

    def test_first_rig_unaffected_by_collision(self, bootstrapped_root: Path):
        # The collision must not corrupt the first rig's folder.
        first = _create_rig_with_seeded_components(
            bootstrapped_root, nickname="My Rig"
        )
        first_xml_bytes = (
            bootstrapped_root / "rigs" / "My Rig" / "rig.xml"
        ).read_bytes()
        components_b = _seed_components(bootstrapped_root)
        with pytest.raises(RigNicknameConflict):
            rig_service.create_rig(
                bootstrapped_root,
                "default",
                _create_payload(components_b, nickname="My Rig"),
            )
        # rig.xml on disk is unchanged.
        assert (
            bootstrapped_root / "rigs" / "My Rig" / "rig.xml"
        ).read_bytes() == first_xml_bytes
        # And we can still get the first rig by id.
        fetched = rig_service.get_rig(bootstrapped_root, "default", first.id)
        assert fetched.id == first.id


# --------------------------------------------------------------------------- #
# get_rig
# --------------------------------------------------------------------------- #


class TestGet:
    def test_round_trip_full_record(self, bootstrapped_root: Path):
        created = _create_rig_with_seeded_components(bootstrapped_root)
        fetched = rig_service.get_rig(
            bootstrapped_root, "default", created.id
        )
        assert fetched == created

    def test_unknown_id_raises_not_found(self, bootstrapped_root: Path):
        with pytest.raises(NotFoundError):
            rig_service.get_rig(bootstrapped_root, "default", uuid4())

    def test_no_rigs_folder_yet_raises_not_found(self, logbook_root: Path):
        # Pre-bootstrap: rigs/ doesn't exist. ``get_rig`` must surface
        # 404, not crash.
        with pytest.raises(NotFoundError):
            rig_service.get_rig(logbook_root, "default", uuid4())

    def test_invalid_rig_xml_raises_validation_failed(
        self, bootstrapped_root: Path
    ):
        # Hand-corrupt a rig.xml file and confirm get_rig surfaces 422
        # rather than masking corruption while looking for the id.
        created = _create_rig_with_seeded_components(bootstrapped_root)
        rig_xml = bootstrapped_root / "rigs" / "Black Cobra" / "rig.xml"
        rig_xml.write_bytes(b"<rig>not valid</rig>")
        with pytest.raises(ValidationFailedError):
            rig_service.get_rig(bootstrapped_root, "default", created.id)


class TestPersistenceInvariants:
    def test_xml_validates_against_xsd_on_disk(
        self, bootstrapped_root: Path
    ):
        # Smoke-test the D2 invariant: every write XSD-validates. Read
        # the file back through the hardened parser + validator and
        # confirm it passes.
        from backend.xml.validator import parse, validate

        _create_rig_with_seeded_components(bootstrapped_root)
        rig_xml = bootstrapped_root / "rigs" / "Black Cobra" / "rig.xml"
        element = parse(rig_xml.read_bytes())
        validate(element)  # would raise XMLError on schema violation

    def test_manifest_matches_rig_xml_hash(self, bootstrapped_root: Path):
        # The SHA256SUMS line for rig.xml must match what shasum -c
        # would compute. That guarantees the recovery path can
        # detect tampering.
        from backend.storage.manifest import sha256_file

        _create_rig_with_seeded_components(bootstrapped_root)
        folder = bootstrapped_root / "rigs" / "Black Cobra"
        rig_xml = folder / "rig.xml"
        manifest = (folder / "SHA256SUMS").read_text("utf-8")
        digest, _, _ = manifest.partition("  ")
        assert digest == sha256_file(rig_xml)


# --------------------------------------------------------------------------- #
# list_rigs (R.2.0c.ii)
# --------------------------------------------------------------------------- #


class TestList:
    def test_empty_list_when_no_rigs(self, bootstrapped_root: Path):
        result = rig_service.list_rigs(bootstrapped_root, "default")
        assert result == []

    def test_no_rigs_folder_returns_empty(self, logbook_root: Path):
        # Pre-bootstrap: rigs/ doesn't exist. List must tolerate.
        result = rig_service.list_rigs(logbook_root, "default")
        assert result == []

    def test_lists_every_rig(self, bootstrapped_root: Path):
        a = _create_rig_with_seeded_components(bootstrapped_root, nickname="A")
        time.sleep(0.005)
        b = _create_rig_with_seeded_components(bootstrapped_root, nickname="B")
        result = rig_service.list_rigs(bootstrapped_root, "default")
        assert {r.id for r in result} == {a.id, b.id}

    def test_orders_by_display_order_ascending(
        self, bootstrapped_root: Path
    ):
        # D59 supersedes the pre-D59 "newest first by created_at"
        # contract. Each create stamps display_order = max+1, so the
        # first-added rig (display_order=0) is leftmost and the
        # second-added (display_order=1) sits to its right.
        a = _create_rig_with_seeded_components(
            bootstrapped_root, nickname="first"
        )
        time.sleep(0.005)
        b = _create_rig_with_seeded_components(
            bootstrapped_root, nickname="second"
        )
        result = rig_service.list_rigs(bootstrapped_root, "default")
        assert [r.id for r in result] == [a.id, b.id]
        # And the stamped values match the position.
        assert result[0].display_order == 0
        assert result[1].display_order == 1

    def test_limit_and_offset_apply(self, bootstrapped_root: Path):
        ids = []
        for i in range(3):
            r = _create_rig_with_seeded_components(
                bootstrapped_root, nickname=f"rig-{i}"
            )
            ids.append(r.id)
            time.sleep(0.005)
        # D59: list order matches create order (left-to-right is
        # first-added → newest). offset=1 + limit=1 returns the
        # middle rig.
        page = rig_service.list_rigs(
            bootstrapped_root, "default", limit=1, offset=1
        )
        assert [r.id for r in page] == [ids[1]]

    def test_skips_partial_create_stub_with_warning(
        self, bootstrapped_root: Path, caplog
    ):
        # A folder under rigs/ with no rig.xml is what a partial
        # create crash leaves behind. List must keep going past it
        # and log a warning.
        good = _create_rig_with_seeded_components(
            bootstrapped_root, nickname="good"
        )
        stub_folder = bootstrapped_root / "rigs" / "stub-rig"
        stub_folder.mkdir()
        caplog.set_level(logging.WARNING, logger="backend.services.rig")
        result = rig_service.list_rigs(bootstrapped_root, "default")
        assert [r.id for r in result] == [good.id]
        assert any(r.message == "rig_skip_invalid" for r in caplog.records)

    def test_skips_invalid_rig_xml_with_warning(
        self, bootstrapped_root: Path, caplog
    ):
        good = _create_rig_with_seeded_components(
            bootstrapped_root, nickname="good"
        )
        # Hand-corrupt a second rig folder.
        bad_folder = bootstrapped_root / "rigs" / "bad"
        bad_folder.mkdir()
        (bad_folder / "rig.xml").write_bytes(b"<rig>not valid</rig>")
        caplog.set_level(logging.WARNING, logger="backend.services.rig")
        result = rig_service.list_rigs(bootstrapped_root, "default")
        assert [r.id for r in result] == [good.id]
        assert any(r.message == "rig_skip_invalid" for r in caplog.records)


# --------------------------------------------------------------------------- #
# update_rig — metadata-only path
# --------------------------------------------------------------------------- #


class TestUpdate:
    def test_changes_jurisdiction(self, bootstrapped_root: Path):
        created = _create_rig_with_seeded_components(
            bootstrapped_root, jurisdiction=Jurisdiction.USPA
        )
        updated = rig_service.update_rig(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload_from(created, jurisdiction=Jurisdiction.BOTH),
        )
        assert updated.jurisdiction == Jurisdiction.BOTH
        # Round-trip from disk confirms the write took.
        fetched = rig_service.get_rig(
            bootstrapped_root, "default", created.id
        )
        assert fetched.jurisdiction == Jurisdiction.BOTH

    def test_preserves_id_and_created_at(self, bootstrapped_root: Path):
        created = _create_rig_with_seeded_components(bootstrapped_root)
        updated = rig_service.update_rig(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload_from(created, jurisdiction=Jurisdiction.CSPA),
        )
        assert updated.id == created.id
        assert updated.created_at == created.created_at

    def test_bumps_updated_at(self, bootstrapped_root: Path):
        created = _create_rig_with_seeded_components(bootstrapped_root)
        time.sleep(0.005)
        updated = rig_service.update_rig(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload_from(created, jurisdiction=Jurisdiction.CSPA),
        )
        assert updated.updated_at != created.updated_at
        assert updated.updated_at > created.created_at

    def test_preserves_repack_history_when_payload_empty(
        self, bootstrapped_root: Path,
    ):
        # D66: empty payload ``repack_history`` (the default from a
        # pre-D66 client that doesn't send the field) MUST preserve
        # the on-disk list rather than wipe it. This is the
        # backwards-compat guard described in D66 §Consequences.
        history = [
            RepackEntry(
                date=date(2025, 6, 1),
                rigger="J. Dupont",
                jurisdiction_seal=Jurisdiction.USPA,
            ),
        ]
        created = _create_rig_with_seeded_components(
            bootstrapped_root, repack_history=history
        )
        updated = rig_service.update_rig(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload_from(created, jurisdiction=Jurisdiction.CSPA),
        )
        assert updated.repack_history == history

    def test_repack_history_replaces_when_payload_supplies_list(
        self, bootstrapped_root: Path,
    ):
        # D66: a non-empty payload list replaces the on-disk list
        # verbatim. The EditRigModal sets this when the user
        # changes the latest repack date.
        original = [
            RepackEntry(
                date=date(2024, 9, 1),
                rigger="Joe Rigger",
                jurisdiction_seal=Jurisdiction.USPA,
            ),
        ]
        created = _create_rig_with_seeded_components(
            bootstrapped_root, repack_history=original
        )
        new_history = [
            *original,
            RepackEntry(
                date=date(2025, 3, 15),
                rigger="Sally Sealer",
                jurisdiction_seal=Jurisdiction.USPA,
            ),
        ]
        updated = rig_service.update_rig(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload_from(created, repack_history=new_history),
        )
        assert updated.repack_history == new_history

    def test_notes_log_replaces(self, bootstrapped_root: Path):
        # notes_log IS on RigUpdate (full-replace), so a PUT replaces
        # the whole log. Append-via-PUT is the documented client
        # pattern (read existing → add entry → PUT).
        created = _create_rig_with_seeded_components(bootstrapped_root)
        new_log = [
            NotesLogEntry(
                at="2026-04-28T15:00:00.000Z",
                text="Cleaning loop replaced",
            ),
        ]
        updated = rig_service.update_rig(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload_from(created, notes_log=new_log),
        )
        assert updated.notes_log == new_log

    def test_unknown_id_raises_not_found(self, bootstrapped_root: Path):
        with pytest.raises(NotFoundError):
            rig_service.update_rig(
                bootstrapped_root,
                "default",
                uuid4(),
                RigUpdate(
                    nickname="X",
                    jurisdiction=Jurisdiction.USPA,
                    current_main_id=uuid4(),
                    current_reserve_id=uuid4(),
                    current_aad_id=uuid4(),
                    current_container_id=uuid4(),
                ),
            )


class TestUpdateNicknameRename:
    """Folder-rename mechanics when the nickname changes."""

    def test_nickname_change_renames_folder(self, bootstrapped_root: Path):
        created = _create_rig_with_seeded_components(
            bootstrapped_root, nickname="Old"
        )
        old_folder = bootstrapped_root / "rigs" / "Old"
        new_folder = bootstrapped_root / "rigs" / "New"
        assert old_folder.is_dir()
        assert not new_folder.exists()

        rig_service.update_rig(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload_from(created, nickname="New"),
        )
        assert not old_folder.exists()
        assert new_folder.is_dir()
        assert (new_folder / "rig.xml").is_file()

    def test_nickname_unchanged_no_rename(self, bootstrapped_root: Path):
        created = _create_rig_with_seeded_components(
            bootstrapped_root, nickname="Same"
        )
        # Just change jurisdiction — no folder rename needed.
        rig_service.update_rig(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload_from(created, jurisdiction=Jurisdiction.CSPA),
        )
        assert (bootstrapped_root / "rigs" / "Same").is_dir()

    def test_nickname_collision_raises_409(self, bootstrapped_root: Path):
        # Two rigs (each with its own four components); rename the
        # first to clash with the second.
        a = _create_rig_with_seeded_components(
            bootstrapped_root, nickname="Alpha"
        )
        _create_rig_with_seeded_components(
            bootstrapped_root, nickname="Beta"
        )
        with pytest.raises(RigNicknameConflict) as info:
            rig_service.update_rig(
                bootstrapped_root,
                "default",
                a.id,
                _update_payload_from(a, nickname="Beta"),
            )
        assert info.value.code == "rig_nickname_conflict"
        # Both folders untouched.
        assert (bootstrapped_root / "rigs" / "Alpha").is_dir()
        assert (bootstrapped_root / "rigs" / "Beta").is_dir()

    def test_invalid_nickname_raises_422(self, bootstrapped_root: Path):
        created = _create_rig_with_seeded_components(bootstrapped_root)
        with pytest.raises(ValidationFailedError) as info:
            rig_service.update_rig(
                bootstrapped_root,
                "default",
                created.id,
                _update_payload_from(created, nickname="bad/name"),
            )
        assert info.value.errors is not None
        assert any(e.pointer == "#/nickname" for e in info.value.errors)


class TestUpdateSwapViaPutRejection:
    """D37: any change to a current_*_id ref via PUT is rejected.

    Each subtest pins the precise FieldError pointer so the UI
    surface can route on it. All four refs covered to guard against
    a future drift that allows one or two through.
    """

    def _attempt_swap(self, root: Path, rig, **swap):
        return rig_service.update_rig(
            root,
            "default",
            rig.id,
            _update_payload_from(rig, **swap),
        )

    def test_main_swap_via_put_raises_409(self, bootstrapped_root: Path):
        created = _create_rig_with_seeded_components(bootstrapped_root)
        with pytest.raises(RigComponentSwapUnsupported) as info:
            self._attempt_swap(
                bootstrapped_root, created, current_main_id=uuid4()
            )
        assert info.value.code == "rig_component_swap_unsupported"
        assert info.value.errors is not None
        pointers = [e.pointer for e in info.value.errors]
        assert "#/current_main_id" in pointers

    def test_reserve_swap_via_put_raises_409(self, bootstrapped_root: Path):
        created = _create_rig_with_seeded_components(bootstrapped_root)
        with pytest.raises(RigComponentSwapUnsupported) as info:
            self._attempt_swap(
                bootstrapped_root, created, current_reserve_id=uuid4()
            )
        pointers = [e.pointer for e in info.value.errors]
        assert "#/current_reserve_id" in pointers

    def test_aad_swap_via_put_raises_409(self, bootstrapped_root: Path):
        created = _create_rig_with_seeded_components(bootstrapped_root)
        with pytest.raises(RigComponentSwapUnsupported) as info:
            self._attempt_swap(
                bootstrapped_root, created, current_aad_id=uuid4()
            )
        pointers = [e.pointer for e in info.value.errors]
        assert "#/current_aad_id" in pointers

    def test_container_swap_via_put_raises_409(
        self, bootstrapped_root: Path
    ):
        created = _create_rig_with_seeded_components(bootstrapped_root)
        with pytest.raises(RigComponentSwapUnsupported) as info:
            self._attempt_swap(
                bootstrapped_root, created, current_container_id=uuid4()
            )
        pointers = [e.pointer for e in info.value.errors]
        assert "#/current_container_id" in pointers

    def test_multiple_swaps_reported_together(
        self, bootstrapped_root: Path
    ):
        # A body that swaps multiple refs in one PUT → all reported
        # in a single response so the user sees the full list.
        created = _create_rig_with_seeded_components(bootstrapped_root)
        with pytest.raises(RigComponentSwapUnsupported) as info:
            self._attempt_swap(
                bootstrapped_root,
                created,
                current_main_id=uuid4(),
                current_reserve_id=uuid4(),
            )
        pointers = {e.pointer for e in info.value.errors}
        assert pointers == {"#/current_main_id", "#/current_reserve_id"}

    def test_disk_unchanged_after_swap_rejection(
        self, bootstrapped_root: Path
    ):
        # The 409 must leave rig.xml untouched on disk.
        created = _create_rig_with_seeded_components(bootstrapped_root)
        original_bytes = (
            bootstrapped_root / "rigs" / "Black Cobra" / "rig.xml"
        ).read_bytes()
        with pytest.raises(RigComponentSwapUnsupported):
            self._attempt_swap(
                bootstrapped_root, created, current_main_id=uuid4()
            )
        after_bytes = (
            bootstrapped_root / "rigs" / "Black Cobra" / "rig.xml"
        ).read_bytes()
        assert original_bytes == after_bytes


# --------------------------------------------------------------------------- #
# delete_rig (R.2.0c.ii) — folder soft-delete to .trash/rigs/
# --------------------------------------------------------------------------- #


class TestDelete:
    def test_returns_trash_path_under_rigs_subdir(
        self, bootstrapped_root: Path
    ):
        created = _create_rig_with_seeded_components(bootstrapped_root)
        trashed = rig_service.delete_rig(
            bootstrapped_root, "default", created.id
        )
        assert trashed.is_dir()
        # Folder lands inside .trash/rigs/<ts>_<nickname>/, distinct
        # from the flat .trash/<ts>_<name>/ shape used by jumps.
        assert ".trash" in trashed.parts
        assert "rigs" in trashed.parts
        # The trashed folder still contains rig.xml + SHA256SUMS.
        assert (trashed / "rig.xml").is_file()
        assert (trashed / "SHA256SUMS").is_file()

    def test_subsequent_get_raises_not_found(self, bootstrapped_root: Path):
        created = _create_rig_with_seeded_components(bootstrapped_root)
        rig_service.delete_rig(bootstrapped_root, "default", created.id)
        with pytest.raises(NotFoundError):
            rig_service.get_rig(
                bootstrapped_root, "default", created.id
            )

    def test_subsequent_list_omits(self, bootstrapped_root: Path):
        a = _create_rig_with_seeded_components(
            bootstrapped_root, nickname="A"
        )
        b = _create_rig_with_seeded_components(
            bootstrapped_root, nickname="B"
        )
        rig_service.delete_rig(bootstrapped_root, "default", a.id)
        result = rig_service.list_rigs(bootstrapped_root, "default")
        assert [r.id for r in result] == [b.id]

    def test_unknown_id_raises_not_found(self, bootstrapped_root: Path):
        with pytest.raises(NotFoundError):
            rig_service.delete_rig(
                bootstrapped_root, "default", uuid4()
            )

    def test_no_active_rigs_folder_left_orphaned(
        self, bootstrapped_root: Path
    ):
        # The original folder under rigs/ is moved away; no empty
        # stub left behind.
        created = _create_rig_with_seeded_components(bootstrapped_root)
        rig_service.delete_rig(bootstrapped_root, "default", created.id)
        active_folders = list(
            (bootstrapped_root / "rigs").iterdir()
        )
        assert active_folders == []

    def test_d37_clears_assigned_rig_id_on_each_component(
        self, bootstrapped_root: Path
    ):
        # R.2.0c.iii.a cascade: every assigned component returns to
        # inventory with assigned_rig_id=None.
        components = _seed_components(bootstrapped_root)
        rig = rig_service.create_rig(
            bootstrapped_root,
            "default",
            _create_payload(components),
        )
        rig_service.delete_rig(bootstrapped_root, "default", rig.id)
        assert (
            main_service.get_main(
                bootstrapped_root, "default", components["main"]
            ).assigned_rig_id
            is None
        )
        assert (
            reserve_service.get_reserve(
                bootstrapped_root, "default", components["reserve"]
            ).assigned_rig_id
            is None
        )
        assert (
            aad_service.get_aad(
                bootstrapped_root, "default", components["aad"]
            ).assigned_rig_id
            is None
        )
        assert (
            container_service.get_container(
                bootstrapped_root, "default", components["container"]
            ).assigned_rig_id
            is None
        )

    def test_d37_cascade_tolerates_missing_component(
        self, bootstrapped_root: Path, caplog
    ):
        # If a component file is missing out-of-band (hand-edited
        # inventory), the cascade should log a WARNING and the rig
        # delete should still succeed.
        components = _seed_components(bootstrapped_root)
        rig = rig_service.create_rig(
            bootstrapped_root,
            "default",
            _create_payload(components),
        )
        # Out-of-band remove the AAD file.
        (
            bootstrapped_root
            / "inventory"
            / "aads"
            / f"{components['aad']}.xml"
        ).unlink()
        caplog.set_level(logging.WARNING, logger="backend.services.rig")
        rig_service.delete_rig(bootstrapped_root, "default", rig.id)
        # The rig is gone.
        with pytest.raises(NotFoundError):
            rig_service.get_rig(bootstrapped_root, "default", rig.id)
        # And we logged about the missing component.
        assert any(
            r.message == "rig_delete_component_missing"
            for r in caplog.records
        )


# --------------------------------------------------------------------------- #
# Structured logging — guard against extra={"name": ...} collisions
# --------------------------------------------------------------------------- #


class TestStructuredLoggingExtraKeys:
    """Same regression class as task #45 / dropzone hotfix: any
    structured-log call site must not collide with reserved
    ``LogRecord`` field names. Tests run under INFO logging so
    ``makeRecord`` actually validates the dict — without this the
    default WARNING level short-circuits ``isEnabledFor`` and the
    collision never fires until production."""

    def test_create_rig_log_has_no_collision(
        self, bootstrapped_root: Path, caplog
    ):
        # Seed components with logging silent to avoid the seeding
        # logs polluting the assertion below.
        components = _seed_components(bootstrapped_root)
        caplog.set_level(logging.INFO, logger="backend.services.rig")
        rig_service.create_rig(
            bootstrapped_root, "default", _create_payload(components)
        )
        assert any(r.message == "rig_created" for r in caplog.records)

    def test_update_rig_log_has_no_collision(
        self, bootstrapped_root: Path, caplog
    ):
        created = _create_rig_with_seeded_components(bootstrapped_root)
        caplog.set_level(logging.INFO, logger="backend.services.rig")
        rig_service.update_rig(
            bootstrapped_root,
            "default",
            created.id,
            _update_payload_from(created, jurisdiction=Jurisdiction.CSPA),
        )
        assert any(r.message == "rig_updated" for r in caplog.records)

    def test_delete_rig_log_has_no_collision(
        self, bootstrapped_root: Path, caplog
    ):
        created = _create_rig_with_seeded_components(bootstrapped_root)
        caplog.set_level(logging.INFO, logger="backend.services.rig")
        rig_service.delete_rig(bootstrapped_root, "default", created.id)
        assert any(r.message == "rig_deleted" for r in caplog.records)


# --------------------------------------------------------------------------- #
# swap_main — D37 jumper-facing canopy swap (S.1)
# --------------------------------------------------------------------------- #


def _seed_extra_main(
    root: Path,
    *,
    status: ComponentStatus = ComponentStatus.ACTIVE,
) -> UUID:
    """Create an additional, unassigned main and return its id.

    swap_main tests need a second main (the "swap target") on top
    of the rig's current main. Mirrors ``_seed_components`` but
    only mints a single fresh main.
    """
    main = main_service.create_main(
        root,
        "default",
        MainCreate(status=status, jump_count_initial=0),
    )
    return main.id


class TestSwapMain:
    """Cover happy path + every D37 rejection path swap_main owns."""

    def test_swaps_current_main_id_on_rig(self, bootstrapped_root: Path):
        rig = _create_rig_with_seeded_components(bootstrapped_root)
        new_main_id = _seed_extra_main(bootstrapped_root)
        old_main_id = rig.current_main_id

        result = rig_service.swap_main(
            bootstrapped_root, "default", rig.id, new_main_id
        )

        assert result.current_main_id == new_main_id
        assert old_main_id != new_main_id  # sanity
        # And the disk reflects the swap.
        on_disk = rig_service.get_rig(bootstrapped_root, "default", rig.id)
        assert on_disk.current_main_id == new_main_id

    def test_attaches_new_main_to_rig(self, bootstrapped_root: Path):
        # The new main's assigned_rig_id must point at the rig
        # post-swap, otherwise create_rig's D37 invariant breaks
        # (every component is on zero-or-one rigs).
        rig = _create_rig_with_seeded_components(bootstrapped_root)
        new_main_id = _seed_extra_main(bootstrapped_root)

        rig_service.swap_main(
            bootstrapped_root, "default", rig.id, new_main_id
        )

        new_main = main_service.get_main(
            bootstrapped_root, "default", new_main_id
        )
        assert new_main.assigned_rig_id == rig.id

    def test_detaches_old_main_from_rig(self, bootstrapped_root: Path):
        # Conversely, the old main returns to inventory: its
        # assigned_rig_id is cleared.
        rig = _create_rig_with_seeded_components(bootstrapped_root)
        new_main_id = _seed_extra_main(bootstrapped_root)
        old_main_id = rig.current_main_id

        rig_service.swap_main(
            bootstrapped_root, "default", rig.id, new_main_id
        )

        old_main = main_service.get_main(
            bootstrapped_root, "default", old_main_id
        )
        assert old_main.assigned_rig_id is None

    def test_bumps_updated_at(self, bootstrapped_root: Path):
        rig = _create_rig_with_seeded_components(bootstrapped_root)
        new_main_id = _seed_extra_main(bootstrapped_root)
        time.sleep(0.01)  # millisecond clock — guarantee a tick

        result = rig_service.swap_main(
            bootstrapped_root, "default", rig.id, new_main_id
        )

        assert result.updated_at is not None
        assert result.updated_at > rig.updated_at
        # created_at must NOT move.
        assert result.created_at == rig.created_at

    def test_preserves_other_three_refs(self, bootstrapped_root: Path):
        # Reserve / AAD / container refs must be untouched by a
        # main swap. Their assigned_rig_id likewise must not shift.
        rig = _create_rig_with_seeded_components(bootstrapped_root)
        new_main_id = _seed_extra_main(bootstrapped_root)

        result = rig_service.swap_main(
            bootstrapped_root, "default", rig.id, new_main_id
        )

        assert result.current_reserve_id == rig.current_reserve_id
        assert result.current_aad_id == rig.current_aad_id
        assert result.current_container_id == rig.current_container_id

        # And the components themselves still report this rig.
        for component_id, getter in (
            (rig.current_reserve_id, reserve_service.get_reserve),
            (rig.current_aad_id, aad_service.get_aad),
            (rig.current_container_id, container_service.get_container),
        ):
            c = getter(bootstrapped_root, "default", component_id)
            assert c.assigned_rig_id == rig.id

    def test_same_id_is_noop(self, bootstrapped_root: Path):
        # Picking the main that's already on the rig: no write, no
        # log line, return the rig unchanged. This makes swap_main
        # idempotent for client retries.
        rig = _create_rig_with_seeded_components(bootstrapped_root)
        before_xml = (
            bootstrapped_root / "rigs" / "Black Cobra" / "rig.xml"
        ).read_bytes()

        result = rig_service.swap_main(
            bootstrapped_root, "default", rig.id, rig.current_main_id
        )

        assert result.current_main_id == rig.current_main_id
        assert result.updated_at == rig.updated_at  # no bump
        after_xml = (
            bootstrapped_root / "rigs" / "Black Cobra" / "rig.xml"
        ).read_bytes()
        assert after_xml == before_xml

    def test_unknown_rig_raises_not_found(self, bootstrapped_root: Path):
        with pytest.raises(NotFoundError):
            rig_service.swap_main(
                bootstrapped_root, "default", uuid4(), uuid4()
            )

    def test_unknown_main_raises_validation_failed(
        self, bootstrapped_root: Path
    ):
        # A new_main_id that doesn't exist surfaces as 422 with a
        # field pointer at #/new_main_id, mirroring create_rig's
        # D37 component-missing posture.
        rig = _create_rig_with_seeded_components(bootstrapped_root)
        with pytest.raises(ValidationFailedError) as info:
            rig_service.swap_main(
                bootstrapped_root, "default", rig.id, uuid4()
            )
        assert info.value.errors is not None
        pointers = [e.pointer for e in info.value.errors]
        assert "#/new_main_id" in pointers

    def test_inactive_main_raises_validation_failed(
        self, bootstrapped_root: Path
    ):
        # A retired main can't be swapped in — D37 status check.
        rig = _create_rig_with_seeded_components(bootstrapped_root)
        retired_main_id = _seed_extra_main(
            bootstrapped_root, status=ComponentStatus.RETIRED
        )

        with pytest.raises(ValidationFailedError) as info:
            rig_service.swap_main(
                bootstrapped_root, "default", rig.id, retired_main_id
            )
        assert info.value.errors is not None
        pointers = [e.pointer for e in info.value.errors]
        assert "#/new_main_id" in pointers

    def test_main_on_other_rig_raises_409(self, bootstrapped_root: Path):
        # The most important rejection path: D37 says no main is on
        # two rigs at once. Try to swap into a main that's already
        # on a different rig → 409 ComponentAlreadyAssigned.
        rig_a = _create_rig_with_seeded_components(bootstrapped_root)
        # Seed a SECOND rig so its main is "the other rig's main".
        components_b = _seed_components(bootstrapped_root)
        rig_b = rig_service.create_rig(
            bootstrapped_root,
            "default",
            _create_payload(components_b, nickname="Red Hawk"),
        )

        with pytest.raises(ComponentAlreadyAssigned) as info:
            rig_service.swap_main(
                bootstrapped_root, "default", rig_a.id, rig_b.current_main_id
            )

        assert info.value.code == "component_already_assigned"
        pointers = [e.pointer for e in info.value.errors or []]
        assert "#/new_main_id" in pointers
        # The other rig's id must show up in the field detail so
        # the UI can render "this main is on rig X".
        details = " ".join(e.detail for e in info.value.errors or [])
        assert str(rig_b.id) in details

    def test_idempotent_retry_when_partial_swap_left_main_attached(
        self, bootstrapped_root: Path
    ):
        # Simulate the partial-crash recovery case described in the
        # swap_main docstring: the new main was already attached to
        # this rig (e.g. the previous attempt got past step 3 but
        # not back through this function's success log). Retrying
        # with the same new_main_id must converge — not 409 on the
        # "already on a rig" check.
        rig = _create_rig_with_seeded_components(bootstrapped_root)
        new_main_id = _seed_extra_main(bootstrapped_root)
        # Manually attach the new main to this rig — simulates
        # crash state where step 3 ran but rig.xml never got the
        # new id.
        main_service.set_assigned_rig_id(
            bootstrapped_root, new_main_id, rig.id
        )

        result = rig_service.swap_main(
            bootstrapped_root, "default", rig.id, new_main_id
        )

        # Convergence: rig now points at the new main, new main is
        # attached, old main is detached.
        assert result.current_main_id == new_main_id
        assert main_service.get_main(
            bootstrapped_root, "default", new_main_id
        ).assigned_rig_id == rig.id
        assert main_service.get_main(
            bootstrapped_root, "default", rig.current_main_id
        ).assigned_rig_id is None

    def test_logs_swap(self, bootstrapped_root: Path, caplog):
        rig = _create_rig_with_seeded_components(bootstrapped_root)
        new_main_id = _seed_extra_main(bootstrapped_root)
        caplog.set_level(logging.INFO, logger="backend.services.rig")

        rig_service.swap_main(
            bootstrapped_root, "default", rig.id, new_main_id
        )

        assert any(r.message == "rig_main_swapped" for r in caplog.records)
