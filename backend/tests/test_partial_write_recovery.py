"""Slice 9 — partial-write crash-recovery matrix for service writes.

A targeted matrix of monkeypatch-injected crashes across the
multi-step write paths the audit named as uncovered:

  * ``add_attachments`` (deep-dive §8.2 / audit §5.1 sibling).
  * ``delete_attachment`` (D43 §"Crash semantics").
  * ``delete_rig`` D37 cascade (Slice 7's create complement).
  * ``update_rig`` folder-rename (mirrors jump rename in
    ``test_crash_recovery.py``).
  * ``track_files`` size/hash race (deep-dive §8.3).
  * ``migrate_all_jumpers`` monkeypatch-crash (audit §2.3 — the
    existing ``test_jumper_migration.py::TestCrashHarness`` pins
    each documented intermediate state by *constructing* it, but
    no test reaches that state via an actual crash injection).

For each scenario the test asserts:

  (a) the on-disk state matches the documented partial shape;
  (b) the corresponding reconcile / next-public-read converges to
      a coherent state without raising;
  (c) where applicable, ``reindex_from_xml`` or
      ``folder_reconcile_rigs`` heals the index / cross-entity
      invariant.

These tests are pure in-process monkeypatch — much faster and more
debuggable than the SIGKILL subprocess harness in
``test_crash_recovery.py``, and complementary to it. SIGKILL is
the right tool when an uncatchable-at-Python termination matters
(``atexit`` ordering, file-handle leaks); monkeypatch raise is the
right tool when the question is "if step N+1 fails after step N
succeeded, what does the next call observe?"
"""
from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from backend.models._component_base import ComponentStatus, NotesLogEntry
from backend.models.aad import AADCreate
from backend.models.container import ContainerCreate
from backend.models.jump import JumpCreate
from backend.models.main import MainCreate
from backend.models.reserve import ReserveCreate
from backend.models.rig import Jurisdiction, RigCreate, RigUpdate
from backend.services import (
    aad_service,
    container_service,
    jump_service,
    main_service,
    reserve_service,
    rig_reconcile_service,
    rig_service,
)
from backend.services.jump_service import Upload
from backend.services.reindex_service import reindex_from_xml
from backend.storage.bootstrap import bootstrap_logbook
from backend.storage.index import open_index
from backend.storage.jumper_migration import (
    ATTACHMENTS_DIRNAME,
    JUMPER_XML_NAME,
    JUMPERS_DIRNAME,
    migrate_all_jumpers,
)
from backend.storage.verify import verify_logbook

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def bootstrapped_root(logbook_root: Path) -> Path:
    bootstrap_logbook(logbook_root)
    result = open_index(logbook_root)
    result.conn.close()
    return logbook_root


def _minimal_jump_payload(**overrides) -> JumpCreate:
    from datetime import date as _date

    base = dict(
        jump_number=1,
        date=_date(2026, 4, 22),
        dropzone="Skydive Elsinore",
        exit_altitude_m=4000,
        deployment_altitude_m=900,
    )
    base.update(overrides)
    return JumpCreate(**base)


def _upload(filename: str, data: bytes, content_type: str | None = "text/plain") -> Upload:
    return Upload(filename=filename, content_type=content_type, chunks=[data])


def _seed_rig_components(root: Path) -> dict[str, UUID]:
    main = main_service.create_main(
        root, "default",
        MainCreate(status=ComponentStatus.ACTIVE, jump_count_initial=0),
    )
    reserve = reserve_service.create_reserve(
        root, "default",
        ReserveCreate(
            status=ComponentStatus.ACTIVE,
            repack_count_initial=0,
            ride_count_initial=0,
        ),
    )
    aad = aad_service.create_aad(
        root, "default",
        AADCreate(
            status=ComponentStatus.ACTIVE,
            jump_count_initial=0,
            fire_count_initial=0,
        ),
    )
    container = container_service.create_container(
        root, "default",
        ContainerCreate(status=ComponentStatus.ACTIVE, jump_count_initial=0),
    )
    return {
        "main": main.id,
        "reserve": reserve.id,
        "aad": aad.id,
        "container": container.id,
    }


def _build_rig(components: dict[str, UUID], **overrides) -> RigCreate:
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


# --------------------------------------------------------------------------- #
# add_attachments crash boundaries
# --------------------------------------------------------------------------- #

class TestAddAttachmentsCrash:
    """Crash points inside ``add_attachments`` (D42).

    Step ordering (jump_service.py): sanitize → stream-write each
    upload to disk → rebuild Jump → atomic_write(jump.xml) →
    atomic_write(SHA256SUMS) → index UPDATE updated_at.
    """

    def test_crash_between_attachment_write_and_jump_xml_leaves_orphan(
        self,
        bootstrapped_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        # Seed a jump with no attachments.
        jump = jump_service.create_jump(
            bootstrapped_root, "default", _minimal_jump_payload(title="FS"),
        )
        folder = bootstrapped_root / "jumps" / "[1] FS"

        # Inject a crash AFTER the attachment stream-write but BEFORE
        # jump.xml gets the new entry. ``_write_jump_and_manifest`` is
        # the next thing called after the stream-write loop.
        def boom(*args, **kwargs):
            raise RuntimeError("simulated crash after attachment write")

        monkeypatch.setattr(jump_service, "_write_jump_and_manifest", boom)

        with pytest.raises(RuntimeError, match="simulated crash"):
            jump_service.add_attachments(
                bootstrapped_root, "default", jump.id,
                uploads=[_upload("orphan.txt", b"hello")],
            )

        monkeypatch.undo()

        # On-disk state: the file landed but jump.xml doesn't claim it.
        assert (folder / "orphan.txt").is_file()
        refetched = jump_service.get_jump(
            bootstrapped_root, "default", jump.id
        )
        assert refetched.attachments == []

        # ``verify_logbook`` flags this as an orphan_file.
        report = verify_logbook(bootstrapped_root)
        extras = [
            i for i in report.issues
            if i.kind == "orphan_file" and "orphan.txt" in i.detail
        ]
        assert extras, (
            f"expected orphan_file, got: {[i.kind for i in report.issues]}"
        )

    def test_crash_between_jump_xml_and_manifest_is_healed_by_reconcile(
        self,
        bootstrapped_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        # Seed and then crash the SHA256SUMS write only. We do this by
        # patching the manifest helper that ``_write_jump_and_manifest``
        # calls AFTER the jump.xml write.
        jump = jump_service.create_jump(
            bootstrapped_root, "default", _minimal_jump_payload(title="FS"),
        )
        folder = bootstrapped_root / "jumps" / "[1] FS"
        original_manifest = (folder / "SHA256SUMS").read_bytes()

        # Inject a failure AFTER jump.xml gets rewritten but BEFORE
        # the SHA256SUMS atomic_write completes. ``from_jump_xml`` is
        # the helper that builds the new manifest bytes and is the
        # last call before the second atomic_write — patching it
        # captures the "jump.xml on disk, manifest stale" state.
        def boom(*args, **kwargs):
            raise RuntimeError("simulated crash mid-manifest")

        monkeypatch.setattr(jump_service, "from_jump_xml", boom)

        with pytest.raises(RuntimeError, match="simulated crash"):
            jump_service.add_attachments(
                bootstrapped_root, "default", jump.id,
                uploads=[_upload("note.txt", b"abc")],
            )

        monkeypatch.undo()

        # jump.xml has the new claim but SHA256SUMS still matches the
        # old (zero-attachment) state.
        post_xml = (folder / "jump.xml").read_bytes()
        assert b"note.txt" in post_xml
        assert (folder / "SHA256SUMS").read_bytes() == original_manifest

        # ``get_jump`` triggers folder_reconcile, which rewrites
        # SHA256SUMS to match the new XML claims.
        refetched = jump_service.get_jump(
            bootstrapped_root, "default", jump.id
        )
        assert any(a.filename == "note.txt" for a in refetched.attachments)
        # Manifest now reflects the new state.
        assert (folder / "SHA256SUMS").read_bytes() != original_manifest


# --------------------------------------------------------------------------- #
# delete_attachment crash boundaries
# --------------------------------------------------------------------------- #

class TestDeleteAttachmentCrash:
    """D43 step-7-then-step-8 boundary explicitly named in the
    ``delete_attachment`` docstring."""

    def test_crash_between_jump_xml_and_file_unlink_leaves_orphan(
        self,
        bootstrapped_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        # Seed a jump with one attachment, then crash AFTER jump.xml
        # rewrite (which removes the entry) but BEFORE the file unlink.
        # Result: file orphaned on disk, jump.xml internally consistent.
        jump = jump_service.create_jump(
            bootstrapped_root, "default", _minimal_jump_payload(title="FS"),
            uploads=[_upload("doomed.txt", b"goodbye")],
        )
        folder = bootstrapped_root / "jumps" / "[1] FS"
        assert (folder / "doomed.txt").is_file()
        assert any(a.filename == "doomed.txt" for a in jump.attachments)

        # The unlink happens after _write_jump_and_manifest. Patch
        # Path.unlink at the call site by replacing the target
        # filesystem call.
        orig_unlink = Path.unlink

        def selective_boom(self: Path, *args, **kwargs):
            if self.name == "doomed.txt":
                raise RuntimeError("simulated crash before unlink")
            return orig_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", selective_boom)

        with pytest.raises(RuntimeError, match="simulated crash"):
            jump_service.delete_attachment(
                bootstrapped_root, "default", jump.id, "doomed.txt",
            )

        monkeypatch.undo()

        # File still on disk, but jump.xml has removed the entry.
        assert (folder / "doomed.txt").is_file()
        refetched = jump_service.get_jump(
            bootstrapped_root, "default", jump.id
        )
        assert refetched.attachments == []

        # verify_logbook reports the orphan.
        report = verify_logbook(bootstrapped_root)
        orphans = [
            i for i in report.issues
            if i.kind == "orphan_file" and "doomed.txt" in i.detail
        ]
        assert orphans

        # Reindex converges (no schema work needed; just that the
        # index doesn't crash on the partial state).
        reindex_report = reindex_from_xml(bootstrapped_root)
        assert reindex_report.aborted is None


# --------------------------------------------------------------------------- #
# delete_rig D37 cascade crash boundary
# --------------------------------------------------------------------------- #

class TestDeleteRigCascadeCrash:
    """Crash during the four-component clear loop in ``delete_rig``.

    Per D37: delete_rig clears assigned_rig_id on each ref BEFORE
    soft-deleting the rig folder. A crash mid-loop leaves some
    components cleared, others still bound, and the rig still in
    place. Recovery: the user retries the delete (idempotent —
    cleared components stay cleared), or the next boot reconcile
    runs.

    Note: ``folder_reconcile_rigs`` is forward-complete (D70). If
    the rig still has a folder on disk after a partial delete, the
    reconcile will RE-BIND the cleared components — that's the
    documented trade-off in D70 §"Alternatives considered" for the
    retry-the-delete recovery path.
    """

    def test_crash_after_one_component_cleared_leaves_others_bound(
        self,
        bootstrapped_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        components = _seed_rig_components(bootstrapped_root)
        rig = rig_service.create_rig(
            bootstrapped_root, "default", _build_rig(components),
        )

        # Crash the reserve write inside set_assigned_rig_id. The
        # loop runs main → reserve → aad → container; main gets
        # cleared, reserve trips.
        def boom(*args, **kwargs):
            raise RuntimeError("simulated cascade crash")

        monkeypatch.setattr(reserve_service, "_write_reserve", boom)

        with pytest.raises(RuntimeError, match="simulated cascade crash"):
            rig_service.delete_rig(bootstrapped_root, "default", rig.id)

        monkeypatch.undo()

        # Main got cleared, reserve / AAD / container still bound, rig
        # still on disk.
        assert (
            main_service.get_main(
                bootstrapped_root, "default", components["main"]
            ).assigned_rig_id
            is None
        )
        for getter, key in (
            (reserve_service.get_reserve, "reserve"),
            (aad_service.get_aad, "aad"),
            (container_service.get_container, "container"),
        ):
            assert getter(
                bootstrapped_root, "default", components[key]
            ).assigned_rig_id == rig.id
        assert (bootstrapped_root / "rigs" / "Black Cobra" / "rig.xml").is_file()

        # Retry the delete — idempotent on the already-cleared main.
        rig_service.delete_rig(bootstrapped_root, "default", rig.id)

        for kind, getter, key in (
            ("main", main_service.get_main, "main"),
            ("reserve", reserve_service.get_reserve, "reserve"),
            ("aad", aad_service.get_aad, "aad"),
            ("container", container_service.get_container, "container"),
        ):
            comp = getter(bootstrapped_root, "default", components[key])
            assert comp.assigned_rig_id is None, (
                f"{kind} should be cleared after the delete retry"
            )
        assert not (
            bootstrapped_root / "rigs" / "Black Cobra"
        ).exists()

    def test_partial_delete_then_boot_reconcile_rebinds_per_d70(
        self,
        bootstrapped_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        # D70 §"Alternatives considered" notes the trade-off: a
        # partial delete (some components cleared, rig still on disk)
        # gets the cleared components re-bound by reconcile because
        # rig.xml is authoritative. This test pins that documented
        # behavior so a future policy shift surfaces here.
        components = _seed_rig_components(bootstrapped_root)
        rig = rig_service.create_rig(
            bootstrapped_root, "default", _build_rig(components),
        )

        def boom(*args, **kwargs):
            raise RuntimeError("simulated cascade crash")

        monkeypatch.setattr(aad_service, "_write_aad", boom)

        with pytest.raises(RuntimeError):
            rig_service.delete_rig(bootstrapped_root, "default", rig.id)

        monkeypatch.undo()

        # Main + reserve cleared, AAD + container still bound.
        assert main_service.get_main(
            bootstrapped_root, "default", components["main"]
        ).assigned_rig_id is None
        assert reserve_service.get_reserve(
            bootstrapped_root, "default", components["reserve"]
        ).assigned_rig_id is None

        # Boot reconcile sees rig.xml referencing all four and
        # forward-completes: main + reserve get re-bound.
        report = rig_reconcile_service.folder_reconcile_rigs(bootstrapped_root)
        assert report.components_forward_completed == 2

        for getter, key in (
            (main_service.get_main, "main"),
            (reserve_service.get_reserve, "reserve"),
        ):
            assert getter(
                bootstrapped_root, "default", components[key]
            ).assigned_rig_id == rig.id


# --------------------------------------------------------------------------- #
# update_rig folder-rename crash
# --------------------------------------------------------------------------- #

class TestUpdateRigRenameCrash:
    """Mirror of ``TestUpdateAfterRename`` in test_crash_recovery.py
    but for rigs (no SIGKILL — monkeypatch suffices since rigs have
    no SQLite index to drift)."""

    def test_crash_after_xml_before_rename_leaves_old_folder_with_new_content(
        self,
        bootstrapped_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        components = _seed_rig_components(bootstrapped_root)
        rig = rig_service.create_rig(
            bootstrapped_root, "default", _build_rig(components, nickname="Old Name"),
        )
        old_folder = bootstrapped_root / "rigs" / "Old Name"
        new_folder = bootstrapped_root / "rigs" / "New Name"
        assert old_folder.is_dir()

        # Inject failure at the os.rename call inside rig_service.
        def boom(*args, **kwargs):
            raise RuntimeError("simulated rename crash")

        monkeypatch.setattr(rig_service.os, "rename", boom)

        payload = RigUpdate(
            nickname="New Name",
            jurisdiction=rig.jurisdiction,
            current_main_id=rig.current_main_id,
            current_reserve_id=rig.current_reserve_id,
            current_aad_id=rig.current_aad_id,
            current_container_id=rig.current_container_id,
            notes_log=[NotesLogEntry(at="2026-05-15T00:00:00.000Z", text="x")],
        )
        with pytest.raises(RuntimeError):
            rig_service.update_rig(bootstrapped_root, "default", rig.id, payload)

        monkeypatch.undo()

        # Old folder still exists but its rig.xml now claims the new
        # nickname. New folder does not exist.
        assert old_folder.is_dir()
        assert not new_folder.exists()
        post_xml = (old_folder / "rig.xml").read_bytes()
        assert b"<nickname>New Name</nickname>" in post_xml

        # The next list_rigs / get_rig sees the new nickname and the
        # old folder location — service tolerates the mismatch (it
        # looks up by id, not folder name).
        fetched = rig_service.get_rig(bootstrapped_root, "default", rig.id)
        assert fetched.nickname == "New Name"


# --------------------------------------------------------------------------- #
# track_files size/hash race (deep-dive §8.3)
# --------------------------------------------------------------------------- #

class TestTrackFilesSizeHashRace:
    """Deep-dive §8.3: the SHA-256 is computed from a streaming read;
    ``path.stat().st_size`` is queried separately afterwards. If the
    file is mutated in-between, the recorded size doesn't match the
    bytes that produced the hash.

    Reproducing the race in-process means injecting bytes between
    the hash loop's last ``f.read()`` and the subsequent stat call.
    We do it by wrapping ``Path.open`` to return a file wrapper
    whose ``__exit__`` appends bytes — the wrapper closes after the
    hash loop completes (``with`` block exits), then ``stat`` sees
    the larger size.

    Current behaviour pins the documented NIT finding: the
    Attachment lands with mismatched ``size`` (post-extension) and
    ``sha256`` (over the pre-extension bytes). It is not a
    corruption bug — ``jump.xml``'s claims are self-consistent in
    the moment of writing — but the next ``verify`` re-hashes the
    file and surfaces the drift.
    """

    def test_size_changes_between_hash_and_stat(
        self,
        bootstrapped_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        jump = jump_service.create_jump(
            bootstrapped_root, "default", _minimal_jump_payload(title="FS"),
        )
        folder = bootstrapped_root / "jumps" / "[1] FS"
        target = folder / "drop.bin"
        original_bytes = b"x" * 100
        target.write_bytes(original_bytes)

        # Inject the race by wrapping Path.open for the target only.
        # Return a proxy file that, on ``__exit__`` (when the hash
        # loop's ``with`` block ends), appends bytes to the
        # underlying file. The next ``path.stat()`` call then sees
        # the larger size while the hash already ran over the
        # original 100 bytes. ``with`` statements look up __exit__
        # on the class, not the instance, so a proxy class is
        # required (per-instance __exit__ assignment doesn't take).
        orig_open = Path.open
        triggered = {"n": 0}

        class _AppendingFileProxy:
            def __init__(self, real, path: Path):
                self._real = real
                self._path = path

            def __enter__(self):
                self._real.__enter__()
                return self._real

            def __exit__(self, exc_type, exc, tb):
                result = self._real.__exit__(exc_type, exc, tb)
                with orig_open(self._path, "ab") as appender:
                    appender.write(b"y" * 50)
                return result

            def read(self, *a, **k):
                return self._real.read(*a, **k)

        def racing_open(self: Path, *args, **kwargs):
            real_file = orig_open(self, *args, **kwargs)
            if self == target and triggered["n"] == 0:
                triggered["n"] = 1
                return _AppendingFileProxy(real_file, target)
            return real_file

        monkeypatch.setattr(Path, "open", racing_open)

        updated = jump_service.track_files(
            bootstrapped_root, "default", jump.id, ["drop.bin"],
        )

        monkeypatch.undo()

        # The Attachment landed with a size that reflects the
        # POST-extension stat (150), not the pre-hash 100.
        tracked = next(a for a in updated.attachments if a.filename == "drop.bin")
        assert tracked.size == 150
        # The hash is over the original 100 bytes (the bytes the
        # streaming loop actually consumed).
        import hashlib

        expected_hash = hashlib.sha256(original_bytes).hexdigest()
        assert tracked.sha256 == expected_hash

        # Verify catches the drift because the on-disk file (now 150
        # bytes) doesn't match the claimed (100-byte) hash.
        report = verify_logbook(bootstrapped_root)
        mismatches = [
            i for i in report.issues
            if i.kind in {"attachment_mismatch", "missing_attachment"}
        ]
        assert mismatches, (
            "expected a verify issue surfacing the race; "
            f"got {[i.kind for i in report.issues]}"
        )


# --------------------------------------------------------------------------- #
# jumper_migration crash injection (audit §2.3)
# --------------------------------------------------------------------------- #

class TestJumperMigrationMonkeypatchCrash:
    """``test_jumper_migration.py::TestCrashHarness`` pins the
    documented intermediate states by hand-constructing them. The
    audit §2.3 gap is that no test reaches those states via an
    actual crash injection — these tests close that gap.
    """

    def _make_legacy(self, root: Path, jid: UUID) -> bytes:
        # Build a minimal valid legacy jumper.xml. The migration is
        # bytes-verbatim so the content just needs to XSD-validate.
        from backend.models.jumper import Jumper
        from backend.xml.serialize import jumper_to_bytes

        prof = Jumper(
            id=jid,
            name="Test Jumper",
            exit_weight_lb=180.0,
            created_at="2026-04-22T10:00:00.000Z",
            updated_at="2026-04-22T10:00:00.000Z",
        )
        legacy_bytes = jumper_to_bytes(prof)
        legacy_path = root / JUMPERS_DIRNAME / f"{jid}.xml"
        legacy_path.write_bytes(legacy_bytes)
        return legacy_bytes

    def test_crash_after_folder_xml_before_manifest_recovers(
        self,
        bootstrapped_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        # Crash inside migrate_one_jumper between the jumper.xml
        # write and the manifest write. Then re-run migrate_all and
        # assert recovery.
        jid = UUID("11111111-1111-4111-8111-111111111111")
        self._make_legacy(bootstrapped_root, jid)

        from backend.storage import jumper_migration as migmod

        # Replace the manifest generator. atomic_write of jumper.xml
        # has already completed at this point in the flow.
        orig_generate = migmod._manifest.generate
        call_count = {"n": 0}

        def boom_once(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated crash mid-migration")
            return orig_generate(*args, **kwargs)

        monkeypatch.setattr(migmod._manifest, "generate", boom_once)

        with pytest.raises(RuntimeError):
            migrate_all_jumpers(bootstrapped_root)

        # Partial state: folder + jumper.xml exist, manifest doesn't,
        # legacy file still on disk.
        folder = bootstrapped_root / JUMPERS_DIRNAME / str(jid)
        assert (folder / JUMPER_XML_NAME).is_file()
        assert not (folder / "SHA256SUMS").is_file()
        assert (bootstrapped_root / JUMPERS_DIRNAME / f"{jid}.xml").is_file()

        # Restore and re-run. ``migrate_one`` enters Case 1
        # (folder_xml exists) and unlinks the legacy. The manifest
        # is regenerated by the service layer on next jumper read,
        # not by migrate.
        monkeypatch.undo()
        changes = migrate_all_jumpers(bootstrapped_root)
        assert changes == 1
        assert not (
            bootstrapped_root / JUMPERS_DIRNAME / f"{jid}.xml"
        ).exists()
        assert (folder / JUMPER_XML_NAME).is_file()
        assert (folder / ATTACHMENTS_DIRNAME).is_dir()

    def test_crash_after_manifest_before_legacy_unlink_recovers(
        self,
        bootstrapped_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        # Crash at the legacy-file unlink step. On retry, migrate
        # enters Case 1 (folder migrated) and unlinks the legacy.
        jid = UUID("22222222-2222-4222-8222-222222222222")
        self._make_legacy(bootstrapped_root, jid)

        orig_unlink = Path.unlink

        def selective_boom(self: Path, *args, **kwargs):
            if (
                self.parent.name == JUMPERS_DIRNAME
                and self.suffix == ".xml"
                and self.stem == str(jid)
            ):
                raise RuntimeError("simulated unlink crash")
            return orig_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", selective_boom)

        with pytest.raises(RuntimeError):
            migrate_all_jumpers(bootstrapped_root)

        # Both legacy AND folder exist (folder is fully built;
        # manifest is on disk; only the unlink failed).
        folder = bootstrapped_root / JUMPERS_DIRNAME / str(jid)
        assert (folder / JUMPER_XML_NAME).is_file()
        assert (folder / "SHA256SUMS").is_file()
        legacy = bootstrapped_root / JUMPERS_DIRNAME / f"{jid}.xml"
        assert legacy.is_file()

        # Restore and re-run.
        monkeypatch.undo()
        changes = migrate_all_jumpers(bootstrapped_root)
        assert changes == 1
        assert not legacy.exists()
        assert (folder / JUMPER_XML_NAME).is_file()


# --------------------------------------------------------------------------- #
# Reindex convergence over partial states
# --------------------------------------------------------------------------- #

class TestReindexConvergesOverPartialStates:
    """Regardless of what crash leaves on disk, ``reindex_from_xml``
    must converge without raising — that's the D26 / D3 contract."""

    def test_reindex_clean_after_add_attachments_crash(
        self,
        bootstrapped_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        jump = jump_service.create_jump(
            bootstrapped_root, "default", _minimal_jump_payload(title="FS"),
        )

        def boom(*args, **kwargs):
            raise RuntimeError("crash")

        monkeypatch.setattr(jump_service, "_write_jump_and_manifest", boom)
        with pytest.raises(RuntimeError):
            jump_service.add_attachments(
                bootstrapped_root, "default", jump.id,
                uploads=[_upload("o.bin", b"abc")],
            )
        monkeypatch.undo()

        # Reindex finishes cleanly — the orphan attachment file is
        # invisible to reindex (jump.xml is the source of truth) and
        # the index row is unchanged.
        report = reindex_from_xml(bootstrapped_root)
        assert report.aborted is None
        assert report.jumps_indexed == 1

    def test_reindex_clean_after_delete_rig_partial_cascade(
        self,
        bootstrapped_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        # delete_rig only touches inventory + rigs folders (no jump-
        # index rows). Reindex is jump-scoped, so a crash here cannot
        # leave the jumps index inconsistent. Pin that.
        jump_service.create_jump(
            bootstrapped_root, "default",
            _minimal_jump_payload(title="FS"),
        )

        components = _seed_rig_components(bootstrapped_root)
        rig = rig_service.create_rig(
            bootstrapped_root, "default", _build_rig(components),
        )

        def boom(*args, **kwargs):
            raise RuntimeError("simulated cascade crash")

        monkeypatch.setattr(reserve_service, "_write_reserve", boom)
        with pytest.raises(RuntimeError):
            rig_service.delete_rig(bootstrapped_root, "default", rig.id)
        monkeypatch.undo()

        # Reindex still finishes cleanly and finds the (one) jump.
        report = reindex_from_xml(bootstrapped_root)
        assert report.aborted is None
        assert report.jumps_indexed == 1
