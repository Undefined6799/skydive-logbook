"""Tests for the D25 verify command.

What these tests pin down:

  * Clean happy path: a freshly-bootstrapped logbook with a
    reconciled jump folder has ``clean = True`` and zero issues.
  * Each issue kind is surfaced with the right ``kind`` string and a
    detail containing enough context to diagnose: ``invalid_folder``,
    ``invalid_xml``, ``missing_attachment``, ``attachment_mismatch``,
    ``stale_manifest``, ``orphan_file``, ``duplicate_jump_number``.
  * Cross-folder duplicate detection reports once per *extra*
    claimant (not once per involved folder) so the issue count
    reflects "how many folders need manual resolution."
  * ``.trash/`` folders are walked for per-folder checks but don't
    participate in duplicate detection (D19 + D23).
  * ``folders_scanned`` counts both active and trashed folders.
  * Non-existent ``jumps/`` directory is tolerated (empty logbook).
"""
from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from uuid import uuid4

from backend.models.jump import Attachment, Jump
from backend.storage.bootstrap import bootstrap_logbook
from backend.storage.manifest import (
    JUMP_XML_NAME,
    MANIFEST_NAME,
    from_jump_xml,
)
from backend.storage.verify import VerifyIssue, verify_logbook
from backend.xml.serialize import jump_to_bytes

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _logbook(tmp_path: Path) -> Path:
    """A bootstrapped logbook root ready to hold jumps.

    Bootstrap is called so every ``SCHEMA.v*.xsd`` is in place per D18.
    verify validates against the logbook-local schema copy; the tests
    want that copy to exist so they're exercising the real code path,
    not the app-shipped fallback.
    """
    root = tmp_path / "logbook"
    bootstrap_logbook(root)
    return root


def _write_jump_folder(
    logbook_root: Path,
    *,
    jump_number: int,
    jump_id=None,
    dzone: str = "Skydive Elsinore",
    attachments: list[tuple[str, bytes, str | None]] | None = None,
) -> Path:
    """Build a valid jump folder under ``<root>/jumps/`` and reconcile it.

    ``attachments``: ``(filename, bytes_on_disk, claimed_hash_or_none)``.
    ``claimed_hash_or_none=None`` means "use the correct hash of the
    bytes." Tests that want disk-vs-claim divergence pass the claim
    explicitly.

    Returns the absolute folder path.
    """
    folder = logbook_root / "jumps" / f"[{jump_number}] 2026-01-01"
    folder.mkdir(parents=True, exist_ok=True)

    model_attachments: list[Attachment] = []
    for filename, data, claimed in attachments or []:
        (folder / filename).write_bytes(data)
        hash_to_record = claimed if claimed is not None else hashlib.sha256(data).hexdigest()
        model_attachments.append(
            Attachment(
                filename=filename,
                sha256=hash_to_record,
                size=len(data),
                content_type="application/octet-stream",
            )
        )

    jump = Jump(
        id=jump_id or uuid4(),
        jump_number=jump_number,
        date=date(2026, 1, 1),
        dropzone=dzone,
        exit_altitude_m=4000,
        deployment_altitude_m=900,
        attachments=model_attachments,
    )
    (folder / JUMP_XML_NAME).write_bytes(jump_to_bytes(jump))

    # Write a correct SHA256SUMS so the jump is "fully set up". Tests
    # that want stale manifests overwrite this file afterward.
    (folder / MANIFEST_NAME).write_bytes(from_jump_xml(folder, logbook_root=logbook_root))
    return folder


def _issues_of_kind(issues: list[VerifyIssue], kind: str) -> list[VerifyIssue]:
    return [i for i in issues if i.kind == kind]


# --------------------------------------------------------------------------- #
# Clean case
# --------------------------------------------------------------------------- #

class TestClean:
    def test_empty_logbook_is_clean(self, tmp_path: Path):
        # Empty logbook (no jumps/) has nothing to check; verify
        # returns clean. folders_scanned == 0.
        root = _logbook(tmp_path)
        report = verify_logbook(root)
        assert report.clean is True
        assert report.issues == []
        assert report.folders_scanned == 0

    def test_single_valid_jump_is_clean(self, tmp_path: Path):
        root = _logbook(tmp_path)
        _write_jump_folder(root, jump_number=1)
        report = verify_logbook(root)
        assert report.clean is True
        assert report.folders_scanned == 1

    def test_multiple_valid_jumps_are_clean(self, tmp_path: Path):
        root = _logbook(tmp_path)
        _write_jump_folder(root, jump_number=1)
        _write_jump_folder(root, jump_number=2)
        _write_jump_folder(
            root,
            jump_number=3,
            attachments=[("flysight.csv", b"data", None)],
        )
        report = verify_logbook(root)
        assert report.clean is True
        assert report.folders_scanned == 3

    def test_missing_jumps_dir_is_tolerated(self, tmp_path: Path):
        # A logbook that hasn't been bootstrapped at all (no jumps/
        # directory yet) is not an error — verify reports zero folders
        # scanned and clean.
        root = tmp_path / "bare"
        root.mkdir()
        report = verify_logbook(root)
        assert report.clean is True
        assert report.folders_scanned == 0


# --------------------------------------------------------------------------- #
# Per-folder failure modes
# --------------------------------------------------------------------------- #

class TestInvalidXml:
    def test_missing_jump_xml_reported(self, tmp_path: Path):
        root = _logbook(tmp_path)
        folder = root / "jumps" / "[1] 2026-01-01"
        folder.mkdir(parents=True)
        # Folder exists but has no jump.xml.
        report = verify_logbook(root)
        assert not report.clean
        invalid = _issues_of_kind(report.issues, "invalid_folder")
        assert len(invalid) == 1
        assert "missing jump.xml" in invalid[0].detail

    def test_malformed_xml_reported(self, tmp_path: Path):
        root = _logbook(tmp_path)
        folder = root / "jumps" / "[1] 2026-01-01"
        folder.mkdir(parents=True)
        (folder / JUMP_XML_NAME).write_bytes(b"<broken<")
        report = verify_logbook(root)
        invalid = _issues_of_kind(report.issues, "invalid_xml")
        assert len(invalid) == 1

    def test_xsd_invalid_xml_reported(self, tmp_path: Path):
        root = _logbook(tmp_path)
        folder = root / "jumps" / "[1] 2026-01-01"
        folder.mkdir(parents=True)
        (folder / JUMP_XML_NAME).write_bytes(
            b'<?xml version="1.0"?>'
            b'<jump xmlns="https://skydive-logbook.org/schema/v1"/>'
        )
        report = verify_logbook(root)
        assert _issues_of_kind(report.issues, "invalid_xml")


class TestAttachmentIntegrity:
    def test_attachment_byte_mismatch_reported(self, tmp_path: Path):
        root = _logbook(tmp_path)
        folder = _write_jump_folder(
            root,
            jump_number=1,
            attachments=[("flysight.csv", b"original", None)],
        )
        # Silently tamper with the bytes after the folder is set up.
        (folder / "flysight.csv").write_bytes(b"tampered")

        report = verify_logbook(root)
        mismatches = _issues_of_kind(report.issues, "attachment_mismatch")
        assert len(mismatches) == 1
        assert "flysight.csv" in mismatches[0].detail

    def test_missing_attachment_reported(self, tmp_path: Path):
        root = _logbook(tmp_path)
        folder = _write_jump_folder(
            root,
            jump_number=1,
            attachments=[("video.mp4", b"bytes", None)],
        )
        (folder / "video.mp4").unlink()

        report = verify_logbook(root)
        missing = _issues_of_kind(report.issues, "missing_attachment")
        assert len(missing) == 1
        assert "video.mp4" in missing[0].detail


class TestManifestStaleness:
    def test_missing_manifest_reported(self, tmp_path: Path):
        root = _logbook(tmp_path)
        folder = _write_jump_folder(root, jump_number=1)
        (folder / MANIFEST_NAME).unlink()

        report = verify_logbook(root)
        stale = _issues_of_kind(report.issues, "stale_manifest")
        assert len(stale) == 1
        assert "missing" in stale[0].detail.lower()

    def test_stale_manifest_reported(self, tmp_path: Path):
        root = _logbook(tmp_path)
        folder = _write_jump_folder(root, jump_number=1)
        (folder / MANIFEST_NAME).write_bytes(b"0" * 64 + b"  bogus\n")

        report = verify_logbook(root)
        stale = _issues_of_kind(report.issues, "stale_manifest")
        assert len(stale) == 1

    def test_malformed_manifest_reported(self, tmp_path: Path):
        root = _logbook(tmp_path)
        folder = _write_jump_folder(root, jump_number=1)
        (folder / MANIFEST_NAME).write_bytes(b"not a manifest line\n")

        report = verify_logbook(root)
        stale = _issues_of_kind(report.issues, "stale_manifest")
        assert len(stale) == 1
        assert "malformed" in stale[0].detail.lower()


class TestOrphanFiles:
    def test_top_level_orphan_reported(self, tmp_path: Path):
        root = _logbook(tmp_path)
        folder = _write_jump_folder(root, jump_number=1)
        # Drop a file the jump doesn't reference.
        (folder / "stray.txt").write_bytes(b"hi")

        report = verify_logbook(root)
        orphans = _issues_of_kind(report.issues, "orphan_file")
        assert any("stray.txt" in o.detail for o in orphans)

    def test_subdirectory_file_is_orphan(self, tmp_path: Path):
        # Attachment filenames are single-segment per D4; anything in a
        # subdirectory is inherently unreferenced and therefore an
        # orphan. verify reports it so the user can decide to keep or
        # trash.
        root = _logbook(tmp_path)
        folder = _write_jump_folder(root, jump_number=1)
        (folder / "photos").mkdir()
        (folder / "photos" / "me.jpg").write_bytes(b"img")

        report = verify_logbook(root)
        orphans = _issues_of_kind(report.issues, "orphan_file")
        assert any("photos/me.jpg" in o.detail for o in orphans)

    def test_summary_md_is_not_orphan(self, tmp_path: Path):
        # summary.md is derived (D5). It legitimately appears in a jump
        # folder without being referenced by jump.xml and must not be
        # flagged as orphan.
        root = _logbook(tmp_path)
        folder = _write_jump_folder(root, jump_number=1)
        (folder / "summary.md").write_bytes(b"# jump 1\n")

        report = verify_logbook(root)
        assert _issues_of_kind(report.issues, "orphan_file") == []

    def test_referenced_attachment_is_not_orphan(self, tmp_path: Path):
        root = _logbook(tmp_path)
        _write_jump_folder(
            root,
            jump_number=1,
            attachments=[("flysight.csv", b"data", None)],
        )
        report = verify_logbook(root)
        # Attachment must not appear in orphan reports.
        orphans = _issues_of_kind(report.issues, "orphan_file")
        assert all("flysight.csv" not in o.detail for o in orphans)

    def test_os_noise_files_are_not_orphans(self, tmp_path: Path):
        # Per audit CODE-5: OS-generated metadata that shows up after
        # the user browses the logbook in Finder / Explorer / nautilus
        # is not user data and should not surface as ``orphan_file``.
        # The noise has two shapes: exact-match names and the
        # AppleDouble ``._<filename>`` prefix family.
        root = _logbook(tmp_path)
        folder = _write_jump_folder(
            root,
            jump_number=1,
            attachments=[("flysight.csv", b"data", None)],
        )
        # macOS Finder
        (folder / ".DS_Store").write_bytes(b"\x00\x00")
        # AppleDouble pair for the attachment (older filesystems)
        (folder / "._flysight.csv").write_bytes(b"\x00")
        # AppleDouble pair for jump.xml itself
        (folder / "._jump.xml").write_bytes(b"\x00")
        # Windows Explorer thumbnail cache
        (folder / "Thumbs.db").write_bytes(b"\x00")
        # Windows folder customization
        (folder / "desktop.ini").write_bytes(b"")

        report = verify_logbook(root)
        orphans = _issues_of_kind(report.issues, "orphan_file")
        # None of the OS-noise filenames should appear in any orphan
        # detail; the manifest check should still report a stale
        # manifest because the new files exist on disk but aren't in
        # SHA256SUMS — but that's the manifest layer's concern, not
        # ours. Filter to orphan_file only.
        assert all(".DS_Store" not in o.detail for o in orphans)
        assert all("Thumbs.db" not in o.detail for o in orphans)
        assert all("desktop.ini" not in o.detail for o in orphans)
        assert all("._flysight.csv" not in o.detail for o in orphans)
        assert all("._jump.xml" not in o.detail for o in orphans)

    def test_dotfile_attachment_still_orphan_when_unreferenced(
        self, tmp_path: Path
    ):
        # Belt-and-braces: the ``._`` prefix exemption is narrow.
        # A user-placed dotfile with a different prefix (e.g.
        # ``.gitignore``, ``.env``) is NOT OS noise and should still
        # surface as an orphan when not in jump.xml's <attachments>.
        # If a user wants to attach a dotfile, the right path is the
        # tracked-attachment flow (D41 ``track`` endpoint), which
        # would then list it in <attachments> and verify accepts it.
        root = _logbook(tmp_path)
        folder = _write_jump_folder(root, jump_number=1)
        (folder / ".gitignore").write_bytes(b"node_modules/\n")

        report = verify_logbook(root)
        orphans = _issues_of_kind(report.issues, "orphan_file")
        assert any(".gitignore" in o.detail for o in orphans)


# --------------------------------------------------------------------------- #
# Cross-folder: duplicate jump_number
# --------------------------------------------------------------------------- #

class TestDuplicateJumpNumber:
    def test_two_folders_same_number_reports_one_issue(self, tmp_path: Path):
        root = _logbook(tmp_path)
        _write_jump_folder(root, jump_number=42)
        # Second folder with same jump_number lives under a different
        # folder name to avoid the mkdir collision that ``create_jump``
        # would hit at write time — we're simulating the "how did this
        # happen" scenario verify exists to detect (D23 corruption
        # case: manual edit, cloud-sync conflict, restored backup).
        folder = root / "jumps" / "[42] 2026-02-02"
        folder.mkdir()
        jump = Jump(
            id=uuid4(),
            jump_number=42,  # same!
            date=date(2026, 2, 2),
            dropzone="Perris",
            exit_altitude_m=4000,
            deployment_altitude_m=900,
            attachments=[],
        )
        (folder / JUMP_XML_NAME).write_bytes(jump_to_bytes(jump))
        (folder / MANIFEST_NAME).write_bytes(from_jump_xml(folder, logbook_root=root))

        report = verify_logbook(root)
        dupes = _issues_of_kind(report.issues, "duplicate_jump_number")
        # Expect ONE issue (on the second claimant), not two — issue
        # count tracks folders needing manual resolution.
        assert len(dupes) == 1

    def test_three_folders_same_number_reports_two_issues(self, tmp_path: Path):
        root = _logbook(tmp_path)
        _write_jump_folder(root, jump_number=42)
        for i, dz in enumerate(["Perris", "Eloy"], start=1):
            folder = root / "jumps" / f"[42] 2026-0{i+1}-01"
            folder.mkdir()
            jump = Jump(
                id=uuid4(),
                jump_number=42,
                date=date(2026, i + 1, 1),
                dropzone=dz,
                exit_altitude_m=4000,
                deployment_altitude_m=900,
                attachments=[],
            )
            (folder / JUMP_XML_NAME).write_bytes(jump_to_bytes(jump))
            (folder / MANIFEST_NAME).write_bytes(
                from_jump_xml(folder, logbook_root=root)
            )

        report = verify_logbook(root)
        dupes = _issues_of_kind(report.issues, "duplicate_jump_number")
        assert len(dupes) == 2  # two extra claimants

    def test_different_numbers_are_not_duplicates(self, tmp_path: Path):
        root = _logbook(tmp_path)
        _write_jump_folder(root, jump_number=1)
        _write_jump_folder(root, jump_number=2)
        report = verify_logbook(root)
        assert _issues_of_kind(report.issues, "duplicate_jump_number") == []


# --------------------------------------------------------------------------- #
# Trash folder treatment (D19 + D23)
# --------------------------------------------------------------------------- #

class TestTrashFolder:
    def test_trash_folders_are_scanned(self, tmp_path: Path):
        root = _logbook(tmp_path)
        # Build a valid jump under jumps/, then move it to .trash/.
        active = _write_jump_folder(root, jump_number=1)
        trashed = root / ".trash" / "20260423T000000_[1] 2026-01-01"
        trashed.parent.mkdir(exist_ok=True)
        active.rename(trashed)

        report = verify_logbook(root)
        # folders_scanned counts BOTH jumps/ and .trash/ entries — the
        # single folder is now under .trash/ and still scanned.
        assert report.folders_scanned == 1
        assert report.clean is True

    def test_trash_does_not_participate_in_duplicate_check(self, tmp_path: Path):
        # A trashed folder with jump_number=42 and an active folder
        # with jump_number=42 must NOT be flagged as duplicate — the
        # trash namespace is deliberately disjoint from active.
        root = _logbook(tmp_path)
        _write_jump_folder(root, jump_number=42)
        trashed = root / ".trash" / "20260423T000000_[42] 2025-12-01"
        trashed.mkdir(parents=True)
        jump = Jump(
            id=uuid4(),
            jump_number=42,
            date=date(2025, 12, 1),
            dropzone="Perris",
            exit_altitude_m=4000,
            deployment_altitude_m=900,
            attachments=[],
        )
        (trashed / JUMP_XML_NAME).write_bytes(jump_to_bytes(jump))
        (trashed / MANIFEST_NAME).write_bytes(
            from_jump_xml(trashed, logbook_root=root)
        )

        report = verify_logbook(root)
        assert _issues_of_kind(report.issues, "duplicate_jump_number") == []

    def test_trash_per_folder_checks_apply(self, tmp_path: Path):
        # A trashed folder with a stale manifest is still reported.
        root = _logbook(tmp_path)
        active = _write_jump_folder(root, jump_number=1)
        trashed = root / ".trash" / "20260423T000000_[1] 2026-01-01"
        trashed.parent.mkdir(exist_ok=True)
        active.rename(trashed)
        # Tamper after move.
        (trashed / MANIFEST_NAME).write_bytes(b"0" * 64 + b"  bogus\n")

        report = verify_logbook(root)
        stale = _issues_of_kind(report.issues, "stale_manifest")
        assert len(stale) == 1
        assert stale[0].folder.startswith(".trash/")


# --------------------------------------------------------------------------- #
# D62 — trash gets parse-only validation; non-jump subdirs are skipped
# --------------------------------------------------------------------------- #

class TestTrashSchemaTolerance:
    """Trashed jumps written under older schemas (D57 removed
    ``landing_direction`` / ``group_size``; D61 renamed ``fun_jump``
    in-place) must not be flagged as invalid_xml. Verify runs
    parse-only in ``.trash/`` so historical drift is tolerated while
    truly corrupt files still surface.
    """

    def test_trashed_jump_with_legacy_field_does_not_fail_verify(
        self, tmp_path: Path
    ):
        # Write a valid current-schema jump, move to .trash/, then
        # splice in a D57-removed ``<landing_direction>`` element
        # before ``<created_at>``. Mirrors what an upgrade-after-D57
        # logbook looks like for a jump trashed pre-D57. The bytes
        # parse cleanly but no longer XSD-validate.
        root = _logbook(tmp_path)
        active = _write_jump_folder(root, jump_number=1)
        trashed = root / ".trash" / "20260423T000000Z_[1] 2026-01-01"
        trashed.parent.mkdir(exist_ok=True)
        active.rename(trashed)
        jump_xml = trashed / JUMP_XML_NAME
        raw = jump_xml.read_bytes()
        # Splice before </jump> — the serializer doesn't emit
        # <created_at> by default, but </jump> is always last.
        legacy = b"  <landing_direction>overshoot</landing_direction>\n</jump>"
        injected = raw.replace(b"</jump>", legacy, 1)
        assert injected != raw, "splice precondition failed"
        jump_xml.write_bytes(injected)
        # Regenerate SHA256SUMS post-splice. The splice changed
        # jump.xml's bytes, so the pre-splice manifest's jump.xml
        # hash is stale — without this, verify would (correctly)
        # report stale_manifest and the test would pass for the
        # wrong reason. Pass validate_xsd=False so the helper
        # mirrors what verify itself does in trash (D62).
        (trashed / MANIFEST_NAME).write_bytes(
            from_jump_xml(trashed, logbook_root=root, validate_xsd=False)
        )

        report = verify_logbook(root)
        assert _issues_of_kind(report.issues, "invalid_xml") == []
        # Also confirm the manifest check stays clean — the whole
        # point of D62 is that legacy-schema trash is silent.
        assert _issues_of_kind(report.issues, "stale_manifest") == []

    def test_live_jump_with_legacy_field_still_fails_verify(
        self, tmp_path: Path
    ):
        # Regression guard: parse-only is .trash/ ONLY. Live jumps
        # in jumps/ must still XSD-validate, so a D57-removed field
        # surfaces as ``invalid_xml`` rather than going silent.
        root = _logbook(tmp_path)
        folder = _write_jump_folder(root, jump_number=1)
        jump_xml = folder / JUMP_XML_NAME
        raw = jump_xml.read_bytes()
        legacy = b"  <landing_direction>overshoot</landing_direction>\n</jump>"
        injected = raw.replace(b"</jump>", legacy, 1)
        assert injected != raw, "splice precondition failed"
        jump_xml.write_bytes(injected)

        report = verify_logbook(root)
        invalid = _issues_of_kind(report.issues, "invalid_xml")
        assert len(invalid) == 1
        assert invalid[0].folder.startswith("jumps/")

    def test_trashed_jump_with_corrupt_xml_still_fails(self, tmp_path: Path):
        # Parse-only is not silent-mode: genuinely broken XML in
        # .trash/ (unparseable bytes) is still reported. Otherwise
        # corruption would hide there indefinitely.
        root = _logbook(tmp_path)
        trashed = root / ".trash" / "20260423T000000Z_[1] broken"
        trashed.mkdir(parents=True)
        (trashed / JUMP_XML_NAME).write_bytes(b"<not-xml<")

        report = verify_logbook(root)
        invalid = _issues_of_kind(report.issues, "invalid_xml")
        assert len(invalid) == 1
        assert invalid[0].folder.startswith(".trash/")


class TestTrashSubdirClassification:
    """``.trash/`` hosts namespace subdirs for non-jump entities
    (D33 rigs, D44 dropzones, D54 people, plus jumpers and
    inventory). Verify recognises these and skips them in v0.1;
    anything else is reported as ``unknown_trash_entry``.
    """

    def test_rigs_subdir_is_skipped_not_treated_as_jump(self, tmp_path: Path):
        # A trashed rig lives at .trash/rigs/<ts>_<nickname>/rig.xml
        # (no jump.xml). Pre-D62 this surfaced as
        # ``invalid_folder · missing jump.xml`` — false positive.
        root = _logbook(tmp_path)
        trashed_rig = (
            root / ".trash" / "rigs" / "20260513T044201.488Z_just a test"
        )
        trashed_rig.mkdir(parents=True)
        (trashed_rig / "rig.xml").write_bytes(b"<rig/>")  # contents don't matter

        report = verify_logbook(root)
        # The whole .trash/rigs/ tree is skipped — no invalid_folder
        # for the namespace dir, none for the inner timestamped dir.
        assert _issues_of_kind(report.issues, "invalid_folder") == []
        assert _issues_of_kind(report.issues, "invalid_xml") == []

    def test_known_namespace_subdirs_all_skipped(self, tmp_path: Path):
        # Pin the full skip set so a future namespace addition
        # (or accidental removal) breaks this test loudly.
        root = _logbook(tmp_path)
        trash = root / ".trash"
        for namespace in ("rigs", "dropzones", "inventory", "people", "jumpers"):
            (trash / namespace).mkdir(parents=True)

        report = verify_logbook(root)
        assert report.issues == []
        # folders_scanned counts trashed-JUMP folders, not namespace dirs.
        assert report.folders_scanned == 0

    def test_unknown_trash_direct_child_is_reported(self, tmp_path: Path):
        # Something that's neither a trashed jump (no <ts>_ prefix)
        # nor a recognised namespace shouldn't pass silently — it
        # likely indicates a bug or manual filesystem tampering.
        root = _logbook(tmp_path)
        weird = root / ".trash" / "definitely_not_a_jump_folder"
        weird.mkdir(parents=True)

        report = verify_logbook(root)
        unknown = _issues_of_kind(report.issues, "unknown_trash_entry")
        assert len(unknown) == 1
        assert "definitely_not_a_jump_folder" in unknown[0].folder

    def test_trashed_jump_with_z_suffix_is_recognised(self, tmp_path: Path):
        # Real-world trash names from ``_now_utc_basic_iso`` include
        # the ``Z`` suffix and (optionally) milliseconds:
        # ``20260513T013442.780Z_[2236]``. Pin both shapes so the
        # regex stays in sync with what storage/trash.py writes.
        root = _logbook(tmp_path)
        for name in (
            "20260513T013442Z_[1] old-shape",
            "20260513T013442.780Z_[2] new-shape",
        ):
            d = root / ".trash" / name
            d.mkdir(parents=True)
            jump = Jump(
                id=uuid4(),
                jump_number=int(name.split("[")[1].split("]")[0]),
                date=date(2026, 1, 1),
                dropzone="Test DZ",
                exit_altitude_m=4000,
                deployment_altitude_m=900,
                attachments=[],
            )
            (d / JUMP_XML_NAME).write_bytes(jump_to_bytes(jump))
            (d / MANIFEST_NAME).write_bytes(
                from_jump_xml(d, logbook_root=root)
            )

        report = verify_logbook(root)
        assert report.clean is True
        assert report.folders_scanned == 2


# --------------------------------------------------------------------------- #
# Report shape
# --------------------------------------------------------------------------- #

class TestReportShape:
    def test_clean_property_matches_issue_list(self, tmp_path: Path):
        root = _logbook(tmp_path)
        _write_jump_folder(root, jump_number=1)
        clean_report = verify_logbook(root)
        assert clean_report.clean == (clean_report.issues == [])

    def test_folder_paths_are_posix_relative(self, tmp_path: Path):
        # Reported folder strings are POSIX-style and relative to the
        # logbook root so they're stable across Windows/macOS/Linux
        # output.
        root = _logbook(tmp_path)
        folder = _write_jump_folder(root, jump_number=1)
        (folder / "stray.txt").write_bytes(b"x")
        report = verify_logbook(root)
        orphans = _issues_of_kind(report.issues, "orphan_file")
        assert len(orphans) == 1
        assert "\\" not in orphans[0].folder  # POSIX slashes only
        assert orphans[0].folder.startswith("jumps/")

    def test_unrecoverable_root_raises_oserror(self, tmp_path: Path):
        # If ``logbook_root`` points at a plain file (not a directory),
        # behavior is implementation-defined: we expect either an
        # OSError on iterdir or a clean zero-folders report. Today's
        # implementation silently returns clean because ``is_dir()``
        # is False for a file — that's acceptable per D25 (verify is
        # read-only, surfacing whatever state exists). This test pins
        # the current behavior; a future tightening to "raise" would
        # flip it and the CLI would need to catch.
        victim = tmp_path / "not-a-folder"
        victim.write_bytes(b"oops")
        report = verify_logbook(victim)
        assert report.folders_scanned == 0
        assert report.clean is True


# --------------------------------------------------------------------------- #
# Cross-entity reference check (D54)
# --------------------------------------------------------------------------- #

class TestDanglingReferences:
    """A jump may reference rigs, dropzones, packers (Person), and
    group members (Persons). Verify reports any reference that doesn't
    resolve to an active (non-trashed) entity as
    ``kind="dangling_reference"``. Service-layer resolution stays soft
    per D54 — these aren't blocking errors — but verify surfaces them
    so the user notices accidental deletes / partial restores."""

    def _write_jump_with_refs(
        self,
        logbook_root: Path,
        *,
        jump_number: int,
        rig_id=None,
        dropzone_id=None,
        packed_by=None,
        group_members: list | None = None,
    ) -> Path:
        from datetime import date

        from backend.models.jump import Jump
        from backend.storage.manifest import (
            JUMP_XML_NAME,
            MANIFEST_NAME,
            from_jump_xml,
        )
        from backend.xml.serialize import jump_to_bytes

        folder = logbook_root / "jumps" / f"[{jump_number}] 2026-01-01"
        folder.mkdir(parents=True, exist_ok=True)
        jump = Jump(
            id=uuid4(),
            jump_number=jump_number,
            date=date(2026, 1, 1),
            dropzone="Skydive Elsinore",
            exit_altitude_m=4000,
            deployment_altitude_m=900,
            rig_id=rig_id,
            dropzone_id=dropzone_id,
            packed_by=packed_by,
            group_members=group_members or [],
        )
        (folder / JUMP_XML_NAME).write_bytes(jump_to_bytes(jump))
        (folder / MANIFEST_NAME).write_bytes(
            from_jump_xml(folder, logbook_root=logbook_root)
        )
        return folder

    def test_unreferenced_jump_has_no_dangling_issues(self, tmp_path: Path):
        # A jump without any cross-entity references — the most common
        # v0.1 shape — produces zero ``dangling_reference`` issues.
        root = _logbook(tmp_path)
        _write_jump_folder(root, jump_number=1)
        report = verify_logbook(root)
        assert _issues_of_kind(report.issues, "dangling_reference") == []

    def test_jump_referencing_nonexistent_rig_is_reported(self, tmp_path: Path):
        # rig_id points at a UUID that has no rig.xml on disk anywhere
        # in rigs/. The dangling reference is reported once with the
        # rig UUID in the detail string so a user can grep for it.
        root = _logbook(tmp_path)
        missing = uuid4()
        self._write_jump_with_refs(root, jump_number=1, rig_id=missing)
        report = verify_logbook(root)
        dangling = _issues_of_kind(report.issues, "dangling_reference")
        assert len(dangling) == 1
        assert "rig_id" in dangling[0].detail
        assert str(missing) in dangling[0].detail

    def test_jump_referencing_existing_rig_is_clean(self, tmp_path: Path):
        # The rig.xml at rigs/<nickname>/rig.xml declares <id>X</id>,
        # so a jump referencing X is *not* dangling. Verify must parse
        # the rig.xml — not just trust the folder name — because the
        # folder is named by sanitized nickname, not by UUID.
        from backend.models.rig import Jurisdiction, Rig
        from backend.xml.serialize import rig_to_bytes

        root = _logbook(tmp_path)
        rig_id = uuid4()
        rig = Rig(
            id=rig_id,
            nickname="my rig",
            jurisdiction=Jurisdiction.USPA,
            current_main_id=uuid4(),
            current_reserve_id=uuid4(),
            current_aad_id=uuid4(),
            current_container_id=uuid4(),
        )
        rig_dir = root / "rigs" / "my-rig"
        rig_dir.mkdir(parents=True)
        (rig_dir / "rig.xml").write_bytes(rig_to_bytes(rig))

        self._write_jump_with_refs(root, jump_number=1, rig_id=rig_id)
        report = verify_logbook(root)
        assert _issues_of_kind(report.issues, "dangling_reference") == []

    def test_jump_referencing_nonexistent_dropzone_is_reported(
        self, tmp_path: Path
    ):
        root = _logbook(tmp_path)
        missing = uuid4()
        self._write_jump_with_refs(root, jump_number=1, dropzone_id=missing)
        report = verify_logbook(root)
        dangling = _issues_of_kind(report.issues, "dangling_reference")
        assert len(dangling) == 1
        assert "dropzone_id" in dangling[0].detail
        assert str(missing) in dangling[0].detail

    def test_jump_referencing_existing_dropzone_is_clean(self, tmp_path: Path):
        # Dropzones use a flat-file layout (D44): the filename stem IS
        # the UUID, so the check is a pure path walk with no parse.
        root = _logbook(tmp_path)
        dz_id = uuid4()
        dz_dir = root / "dropzones"
        dz_dir.mkdir(parents=True, exist_ok=True)
        (dz_dir / f"{dz_id}.xml").write_bytes(b"<dropzone/>")
        self._write_jump_with_refs(root, jump_number=1, dropzone_id=dz_id)
        report = verify_logbook(root)
        assert _issues_of_kind(report.issues, "dangling_reference") == []

    def test_jump_referencing_nonexistent_packer_is_reported(
        self, tmp_path: Path
    ):
        # packed_by → Person. Same flat-file layout as dropzones.
        root = _logbook(tmp_path)
        missing = uuid4()
        self._write_jump_with_refs(root, jump_number=1, packed_by=missing)
        report = verify_logbook(root)
        dangling = _issues_of_kind(report.issues, "dangling_reference")
        assert len(dangling) == 1
        assert "packed_by" in dangling[0].detail

    def test_each_dangling_group_member_is_a_separate_issue(
        self, tmp_path: Path
    ):
        # group_members is a list — each unresolved UUID is its own
        # ``dangling_reference`` so the user sees the full picture.
        root = _logbook(tmp_path)
        missing_a, missing_b = uuid4(), uuid4()
        self._write_jump_with_refs(
            root, jump_number=1, group_members=[missing_a, missing_b]
        )
        report = verify_logbook(root)
        dangling = _issues_of_kind(report.issues, "dangling_reference")
        assert len(dangling) == 2
        details = " ".join(i.detail for i in dangling)
        assert str(missing_a) in details
        assert str(missing_b) in details

    def test_partial_group_member_resolution(self, tmp_path: Path):
        # One member exists, one doesn't — only the missing one is
        # flagged. Confirms the check resolves member-by-member, not
        # all-or-nothing.
        root = _logbook(tmp_path)
        existing, missing = uuid4(), uuid4()
        ppl_dir = root / "people"
        ppl_dir.mkdir(parents=True, exist_ok=True)
        (ppl_dir / f"{existing}.xml").write_bytes(b"<person/>")
        self._write_jump_with_refs(
            root, jump_number=1, group_members=[existing, missing]
        )
        report = verify_logbook(root)
        dangling = _issues_of_kind(report.issues, "dangling_reference")
        assert len(dangling) == 1
        assert str(missing) in dangling[0].detail
        assert str(existing) not in dangling[0].detail

    def test_trashed_entity_is_treated_as_dangling(self, tmp_path: Path):
        # Soft-deleting a dropzone moves its file under .trash/dropzones/.
        # A jump that still references it after the delete is dangling
        # — that's exactly the "user deleted a DZ, forgot about the jumps
        # referencing it" case verify exists to surface.
        root = _logbook(tmp_path)
        dz_id = uuid4()
        trash_dir = root / ".trash" / "dropzones"
        trash_dir.mkdir(parents=True, exist_ok=True)
        (trash_dir / f"{dz_id}.xml").write_bytes(b"<dropzone/>")
        self._write_jump_with_refs(root, jump_number=1, dropzone_id=dz_id)
        report = verify_logbook(root)
        dangling = _issues_of_kind(report.issues, "dangling_reference")
        assert len(dangling) == 1
        assert str(dz_id) in dangling[0].detail
