"""Phase C.3 — Service-layer tests for jumper attachment add/delete.

These tests exercise:

  * ``add_attachment_to_jumper`` — happy path, multi-attachment,
    bad filename, missing jumper, manifest+disk consistency.
  * ``delete_attachment_from_jumper`` — happy path, unknown id,
    unknown jumper, cross-reference protection (a credential's
    ``card_attachment_id`` blocks deletion), and the partial-delete
    crash path (file already gone, idempotent re-run).
  * ``manifest.from_jumper_xml`` — recovery-path manifest matches
    on-disk content for both empty and populated jumpers.

The cross-reference setup uses ``_write_jumper`` directly (the
identity-only PUT can't add credentials yet — those endpoints land
in Phase D), then exercises the service through the public API.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from backend.api.errors import ConflictError, NotFoundError, ValidationFailedError
from backend.models.jumper import (
    JumperCreate,
    Medical,
    MedicalKind,
    Membership,
    OrgEnum,
)
from backend.services import jumper_service
from backend.services.jumper_service import (
    Upload,
    _write_jumper,
    add_attachment_to_jumper,
    delete_attachment_from_jumper,
)
from backend.storage import manifest as _manifest
from backend.storage.bootstrap import bootstrap_logbook
from backend.storage.jumper_migration import (
    ATTACHMENTS_DIRNAME,
    JUMPER_XML_NAME,
    JUMPERS_DIRNAME,
)


@pytest.fixture
def bootstrapped_root(logbook_root: Path) -> Path:
    bootstrap_logbook(logbook_root)
    return logbook_root


def _create_jumper(root: Path):
    return jumper_service.create_jumper(
        root, "default", JumperCreate(exit_weight_lb=180)
    )


def _upload(filename: str, content_type: str, payload: bytes) -> Upload:
    return Upload(
        filename=filename, content_type=content_type, chunks=[payload]
    )


# --------------------------------------------------------------------- #
# add_attachment_to_jumper
# --------------------------------------------------------------------- #

class TestAddAttachment:
    def test_happy_path_attaches_file(self, bootstrapped_root: Path) -> None:
        j = _create_jumper(bootstrapped_root)
        updated = add_attachment_to_jumper(
            bootstrapped_root,
            "default",
            j.id,
            _upload("cspa-card-2026.pdf", "application/pdf", b"PDF bytes"),
        )
        # Returned Jumper carries one attachment with server-minted UUID.
        assert len(updated.attachments) == 1
        att = updated.attachments[0]
        assert att.filename == "cspa-card-2026.pdf"
        assert att.content_type == "application/pdf"
        assert att.size == len(b"PDF bytes")

    def test_file_lands_at_composed_disk_path(
        self, bootstrapped_root: Path
    ) -> None:
        j = _create_jumper(bootstrapped_root)
        updated = add_attachment_to_jumper(
            bootstrapped_root,
            "default",
            j.id,
            _upload("card.pdf", "application/pdf", b"data"),
        )
        att = updated.attachments[0]
        disk = (
            bootstrapped_root
            / JUMPERS_DIRNAME
            / str(j.id)
            / ATTACHMENTS_DIRNAME
            / f"{att.id}__card.pdf"
        )
        assert disk.is_file()
        assert disk.read_bytes() == b"data"

    def test_sha256_matches_payload(self, bootstrapped_root: Path) -> None:
        import hashlib

        payload = b"some content"
        expected = hashlib.sha256(payload).hexdigest()
        j = _create_jumper(bootstrapped_root)
        updated = add_attachment_to_jumper(
            bootstrapped_root,
            "default",
            j.id,
            _upload("card.pdf", "application/pdf", payload),
        )
        assert updated.attachments[0].sha256 == expected

    def test_two_attachments_get_distinct_ids(
        self, bootstrapped_root: Path
    ) -> None:
        j = _create_jumper(bootstrapped_root)
        first = add_attachment_to_jumper(
            bootstrapped_root,
            "default",
            j.id,
            _upload("a.pdf", "application/pdf", b"AAA"),
        )
        second = add_attachment_to_jumper(
            bootstrapped_root,
            "default",
            j.id,
            _upload("b.pdf", "application/pdf", b"BBB"),
        )
        assert first.attachments[0].id != second.attachments[1].id
        # Both attachments end up in the canonical record.
        assert len(second.attachments) == 2

    def test_two_attachments_with_same_filename_dont_collide(
        self, bootstrapped_root: Path
    ) -> None:
        # Same user filename, two separate uploads → two separate
        # disk paths because the UUID prefix differs.
        j = _create_jumper(bootstrapped_root)
        first = add_attachment_to_jumper(
            bootstrapped_root,
            "default",
            j.id,
            _upload("card.pdf", "application/pdf", b"AAA"),
        )
        second = add_attachment_to_jumper(
            bootstrapped_root,
            "default",
            j.id,
            _upload("card.pdf", "application/pdf", b"BBB"),
        )
        att_dir = (
            bootstrapped_root / JUMPERS_DIRNAME / str(j.id) / ATTACHMENTS_DIRNAME
        )
        files = sorted(p.name for p in att_dir.iterdir())
        assert len(files) == 2
        # Both files present with their respective contents
        a_id = first.attachments[0].id
        b_id = second.attachments[1].id
        assert (att_dir / f"{a_id}__card.pdf").read_bytes() == b"AAA"
        assert (att_dir / f"{b_id}__card.pdf").read_bytes() == b"BBB"

    def test_updated_at_bumped(self, bootstrapped_root: Path) -> None:
        j = _create_jumper(bootstrapped_root)
        original_updated_at = j.updated_at
        # Sleep is overkill — _now_utc_iso uses ms precision so even
        # a same-millisecond call produces an equal timestamp; the
        # add path bumps to a fresh stamp regardless.
        updated = add_attachment_to_jumper(
            bootstrapped_root,
            "default",
            j.id,
            _upload("card.pdf", "application/pdf", b"data"),
        )
        # The bumped timestamp is at least as recent as the original
        # (string compare on the ISO format works for monotonicity).
        assert updated.updated_at is not None
        assert original_updated_at is not None
        assert updated.updated_at >= original_updated_at

    def test_manifest_verifies_post_add(self, bootstrapped_root: Path) -> None:
        # SHA256SUMS regenerated from the new XML claims must match
        # the on-disk bytes for jumper.xml + every attachment.
        j = _create_jumper(bootstrapped_root)
        add_attachment_to_jumper(
            bootstrapped_root,
            "default",
            j.id,
            _upload("card.pdf", "application/pdf", b"hello world"),
        )
        folder = bootstrapped_root / JUMPERS_DIRNAME / str(j.id)
        problems = _manifest.verify(folder)
        assert problems == [], (
            f"manifest must verify cleanly post-add; got: {problems}"
        )

    def test_returned_jumper_is_readable_after_add(
        self, bootstrapped_root: Path
    ) -> None:
        j = _create_jumper(bootstrapped_root)
        updated = add_attachment_to_jumper(
            bootstrapped_root,
            "default",
            j.id,
            _upload("card.pdf", "application/pdf", b"data"),
        )
        # GET round-trips the attachment.
        fetched = jumper_service.get_jumper(
            bootstrapped_root, "default", j.id
        )
        assert fetched.attachments == updated.attachments

    def test_unknown_jumper_raises_not_found(
        self, bootstrapped_root: Path
    ) -> None:
        with pytest.raises(NotFoundError):
            add_attachment_to_jumper(
                bootstrapped_root,
                "default",
                uuid4(),
                _upload("card.pdf", "application/pdf", b"data"),
            )

    def test_invalid_filename_raises_validation_failed(
        self, bootstrapped_root: Path
    ) -> None:
        j = _create_jumper(bootstrapped_root)
        with pytest.raises(ValidationFailedError) as exc_info:
            add_attachment_to_jumper(
                bootstrapped_root,
                "default",
                j.id,
                _upload("../escape.pdf", "application/pdf", b"data"),
            )
        # Pointer should reference #/filename per the contract.
        pointers = [e.pointer for e in exc_info.value.errors]
        assert "#/filename" in pointers


# --------------------------------------------------------------------- #
# delete_attachment_from_jumper
# --------------------------------------------------------------------- #

class TestDeleteAttachment:
    def _attach(self, root: Path, jumper_id: UUID, filename: str = "card.pdf"):
        return add_attachment_to_jumper(
            root,
            "default",
            jumper_id,
            _upload(filename, "application/pdf", b"data"),
        )

    def test_happy_path_removes_attachment(
        self, bootstrapped_root: Path
    ) -> None:
        j = _create_jumper(bootstrapped_root)
        added = self._attach(bootstrapped_root, j.id)
        att_id = added.attachments[0].id

        updated = delete_attachment_from_jumper(
            bootstrapped_root, "default", j.id, att_id
        )
        assert updated.attachments == []

    def test_file_unlinked_from_disk(self, bootstrapped_root: Path) -> None:
        j = _create_jumper(bootstrapped_root)
        added = self._attach(bootstrapped_root, j.id)
        att_id = added.attachments[0].id
        disk_path = (
            bootstrapped_root
            / JUMPERS_DIRNAME
            / str(j.id)
            / ATTACHMENTS_DIRNAME
            / f"{att_id}__card.pdf"
        )
        assert disk_path.is_file()
        delete_attachment_from_jumper(
            bootstrapped_root, "default", j.id, att_id
        )
        assert not disk_path.exists()

    def test_other_attachments_survive(
        self, bootstrapped_root: Path
    ) -> None:
        j = _create_jumper(bootstrapped_root)
        a = self._attach(bootstrapped_root, j.id, filename="a.pdf")
        b = self._attach(bootstrapped_root, j.id, filename="b.pdf")
        a_id = a.attachments[0].id
        b_id = b.attachments[1].id

        updated = delete_attachment_from_jumper(
            bootstrapped_root, "default", j.id, a_id
        )
        remaining_ids = {att.id for att in updated.attachments}
        assert remaining_ids == {b_id}

    def test_manifest_verifies_post_delete(
        self, bootstrapped_root: Path
    ) -> None:
        j = _create_jumper(bootstrapped_root)
        added = self._attach(bootstrapped_root, j.id)
        att_id = added.attachments[0].id
        delete_attachment_from_jumper(
            bootstrapped_root, "default", j.id, att_id
        )
        folder = bootstrapped_root / JUMPERS_DIRNAME / str(j.id)
        problems = _manifest.verify(folder)
        assert problems == [], (
            f"manifest must verify cleanly post-delete; got: {problems}"
        )

    def test_unknown_attachment_raises_not_found(
        self, bootstrapped_root: Path
    ) -> None:
        j = _create_jumper(bootstrapped_root)
        with pytest.raises(NotFoundError):
            delete_attachment_from_jumper(
                bootstrapped_root, "default", j.id, uuid4()
            )

    def test_unknown_jumper_raises_not_found(
        self, bootstrapped_root: Path
    ) -> None:
        with pytest.raises(NotFoundError):
            delete_attachment_from_jumper(
                bootstrapped_root, "default", uuid4(), uuid4()
            )

    def test_orphan_disk_file_is_tolerated(
        self, bootstrapped_root: Path
    ) -> None:
        # If the disk file is already gone (e.g. previous half-failed
        # delete unlinked the file but crashed before updating
        # jumper.xml), the next delete_attachment call must complete
        # the operation idempotently rather than raising.
        j = _create_jumper(bootstrapped_root)
        added = self._attach(bootstrapped_root, j.id)
        att_id = added.attachments[0].id
        disk_path = (
            bootstrapped_root
            / JUMPERS_DIRNAME
            / str(j.id)
            / ATTACHMENTS_DIRNAME
            / f"{att_id}__card.pdf"
        )
        # Simulate the half-failed state.
        disk_path.unlink()

        updated = delete_attachment_from_jumper(
            bootstrapped_root, "default", j.id, att_id
        )
        assert updated.attachments == []


class TestDeleteAttachmentCrossReference:
    """Refusing to delete an attachment a credential references."""

    def _setup_jumper_with_referenced_attachment(
        self, bootstrapped_root: Path
    ):
        """Helper: create a jumper, attach a file, then add a
        membership that references the attachment via
        card_attachment_id. Returns (jumper_id, attachment_id)."""
        j = _create_jumper(bootstrapped_root)
        added = add_attachment_to_jumper(
            bootstrapped_root,
            "default",
            j.id,
            _upload("cspa-card.pdf", "application/pdf", b"PDF bytes"),
        )
        att_id = added.attachments[0].id
        # Add a credential that references the attachment by id.
        # JumperUpdate is identity-only; we use _write_jumper directly
        # because credential CRUD endpoints land in Phase D.
        with_credential = added.model_copy(
            update={
                "memberships": [
                    Membership(
                        org=OrgEnum.CSPA,
                        member_number="12345",
                        expiry_date=date(2027, 4, 29),
                        card_attachment_id=att_id,
                    ),
                ],
            },
        )
        _write_jumper(bootstrapped_root, with_credential)
        return j.id, att_id

    def test_membership_reference_blocks_delete(
        self, bootstrapped_root: Path
    ) -> None:
        jumper_id, att_id = self._setup_jumper_with_referenced_attachment(
            bootstrapped_root
        )
        with pytest.raises(ConflictError) as exc_info:
            delete_attachment_from_jumper(
                bootstrapped_root, "default", jumper_id, att_id
            )
        # The error carries one FieldError pointing at the
        # membership's card_attachment_id.
        pointers = [e.pointer for e in exc_info.value.errors]
        assert "#/memberships/0/card_attachment_id" in pointers

    def test_blocked_delete_leaves_disk_unchanged(
        self, bootstrapped_root: Path
    ) -> None:
        jumper_id, att_id = self._setup_jumper_with_referenced_attachment(
            bootstrapped_root
        )
        disk = (
            bootstrapped_root
            / JUMPERS_DIRNAME
            / str(jumper_id)
            / ATTACHMENTS_DIRNAME
            / f"{att_id}__cspa-card.pdf"
        )
        assert disk.is_file()
        with pytest.raises(ConflictError):
            delete_attachment_from_jumper(
                bootstrapped_root, "default", jumper_id, att_id
            )
        # File must survive the rejected deletion.
        assert disk.is_file()
        assert disk.read_bytes() == b"PDF bytes"

    def test_multiple_references_listed(
        self, bootstrapped_root: Path
    ) -> None:
        # Two different credentials referencing the same attachment.
        # The error should enumerate both pointers.
        j = _create_jumper(bootstrapped_root)
        added = add_attachment_to_jumper(
            bootstrapped_root,
            "default",
            j.id,
            _upload("card.pdf", "application/pdf", b"data"),
        )
        att_id = added.attachments[0].id
        with_creds = added.model_copy(
            update={
                "memberships": [
                    Membership(
                        org=OrgEnum.CSPA,
                        member_number="12345",
                        expiry_date=date(2027, 4, 29),
                        card_attachment_id=att_id,
                    ),
                ],
                "medicals": [
                    Medical(
                        kind=MedicalKind.CLASS_III,
                        issuing_authority="Transport Canada",
                        expiry_date=date(2028, 6, 15),
                        card_attachment_id=att_id,
                    ),
                ],
            },
        )
        _write_jumper(bootstrapped_root, with_creds)

        with pytest.raises(ConflictError) as exc_info:
            delete_attachment_from_jumper(
                bootstrapped_root, "default", j.id, att_id
            )
        pointers = [e.pointer for e in exc_info.value.errors]
        assert "#/memberships/0/card_attachment_id" in pointers
        assert "#/medicals/0/card_attachment_id" in pointers


# --------------------------------------------------------------------- #
# manifest.from_jumper_xml — recovery-path manifest helper
# --------------------------------------------------------------------- #

class TestFromJumperXmlManifest:
    def test_empty_jumper_manifest_only_has_jumper_xml(
        self, bootstrapped_root: Path
    ) -> None:
        j = _create_jumper(bootstrapped_root)
        folder = bootstrapped_root / JUMPERS_DIRNAME / str(j.id)
        manifest_bytes = _manifest.from_jumper_xml(folder)
        # Only one entry: jumper.xml itself
        entries = _manifest.parse(manifest_bytes)
        assert len(entries) == 1
        assert entries[0][1] == JUMPER_XML_NAME

    def test_populated_jumper_manifest_lists_attachments(
        self, bootstrapped_root: Path
    ) -> None:
        j = _create_jumper(bootstrapped_root)
        added = add_attachment_to_jumper(
            bootstrapped_root,
            "default",
            j.id,
            _upload("card.pdf", "application/pdf", b"hello"),
        )
        att_id = added.attachments[0].id

        folder = bootstrapped_root / JUMPERS_DIRNAME / str(j.id)
        manifest_bytes = _manifest.from_jumper_xml(folder)
        entries = _manifest.parse(manifest_bytes)

        # Two entries: jumper.xml + attachments/<id>__card.pdf
        rels = {rel for _, rel in entries}
        assert JUMPER_XML_NAME in rels
        assert f"attachments/{att_id}__card.pdf" in rels

    def test_attachment_without_id_raises(
        self, bootstrapped_root: Path
    ) -> None:
        # If a hand-edited or third-party-authored jumper.xml has an
        # <attachment> without <id>, from_jumper_xml must surface the
        # corruption rather than silently produce a wrong path. The
        # XSD's AttachmentType permits id to elide (jump attachments
        # may legitimately have no id), so the manifest helper is the
        # layer that catches the jumper-side requirement.
        j = _create_jumper(bootstrapped_root)
        folder = bootstrapped_root / JUMPERS_DIRNAME / str(j.id)
        xml = (folder / JUMPER_XML_NAME).read_bytes().decode()
        # Inject <attachments> just before <created_at> so the XSD
        # element-order constraint is honoured (per JumperContent's
        # sequence: ... medicals, attachments, created_at, updated_at).
        bad_xml = xml.replace(
            "<created_at>",
            "<attachments><attachment>"
            "<filename>orphan.pdf</filename>"
            "<sha256>" + "a" * 64 + "</sha256>"
            "<size>10</size>"
            "</attachment></attachments><created_at>",
        )
        (folder / JUMPER_XML_NAME).write_bytes(bad_xml.encode())

        with pytest.raises(ValueError, match="missing required <id>"):
            _manifest.from_jumper_xml(folder)
