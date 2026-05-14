"""Phase C.2 — Pure storage helpers for jumper attachments.

These tests cover the on-disk filename composition, the inverse
parse, and the streaming write / delete primitives that the C.3
service will compose with manifest regeneration and Pydantic-level
record management. Crash semantics for the streaming write itself
are owned by ``atomic_write_stream``'s test surface; here we
exercise the wrapper's path math + interaction with the real
``attachments/`` folder and confirm no stray ``.tmp`` files survive
a successful write.
"""
from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest

from backend.storage.filesystem import sanitize_filename
from backend.storage.jumper_attachments import (
    _ATTACHMENT_NAME_SEP,
    _USER_FILENAME_MAX,
    attachment_disk_path,
    compose_attachment_filename,
    delete_attachment_file,
    jumper_attachments_dir,
    parse_attachment_filename,
    write_attachment_stream,
)
from backend.storage.jumper_migration import ATTACHMENTS_DIRNAME

SAMPLE_UUID = UUID("11111111-1111-4111-8111-111111111111")


# --------------------------------------------------------------------- #
# Path math — pure helpers, no filesystem
# --------------------------------------------------------------------- #

class TestPathMath:
    def test_jumper_attachments_dir_appends_subfolder(self) -> None:
        folder = Path("/tmp/jumpers/abc")
        assert jumper_attachments_dir(folder) == folder / ATTACHMENTS_DIRNAME

    def test_compose_uses_uuid_prefix_and_separator(self) -> None:
        composed = compose_attachment_filename(SAMPLE_UUID, "card.pdf")
        assert composed == f"{SAMPLE_UUID}__card.pdf"

    def test_compose_sanitizes_user_filename(self) -> None:
        # Whitespace at edges is stripped (D4 rule). The filename
        # round-trips through sanitize_filename so the test asserts
        # equality with the expected sanitized form rather than
        # hand-coding the exact sanitization output.
        composed = compose_attachment_filename(
            SAMPLE_UUID, "  cspa-card-2026.pdf  "
        )
        expected = f"{SAMPLE_UUID}__{sanitize_filename('  cspa-card-2026.pdf  ')}"
        assert composed == expected

    @pytest.mark.parametrize(
        "bad_name",
        [
            "../escape.pdf",  # path separator
            "evil/name.pdf",  # forward slash
            "win\\bad.pdf",  # backslash
            "CON",  # Windows reserved
            "trailing.",  # trailing period (kept after strip; rejected)
            "",  # empty
        ],
    )
    def test_compose_rejects_invalid_user_filenames(self, bad_name: str) -> None:
        # ``sanitize_filename`` strips leading/trailing whitespace
        # before validating, so a trailing-space input is silently
        # cleaned (not rejected). Trailing-period survives strip and
        # is rejected by the Windows-reserved check.
        with pytest.raises(ValueError):
            compose_attachment_filename(SAMPLE_UUID, bad_name)

    def test_compose_caps_long_user_filename(self) -> None:
        # 218 chars > the 217 cap (255 - 36 - 2). Must reject.
        long_name = ("a" * 218) + ".pdf"
        with pytest.raises(ValueError):
            compose_attachment_filename(SAMPLE_UUID, long_name)

    def test_compose_user_filename_at_cap_works(self) -> None:
        # Exactly at the cap: should compose successfully and the
        # result should be exactly 255 chars.
        long_name = "a" * _USER_FILENAME_MAX
        composed = compose_attachment_filename(SAMPLE_UUID, long_name)
        assert len(composed) == 255

    def test_attachment_disk_path_combines_correctly(self) -> None:
        folder = Path("/tmp/jumpers/abc")
        path = attachment_disk_path(folder, SAMPLE_UUID, "card.pdf")
        assert path == folder / ATTACHMENTS_DIRNAME / f"{SAMPLE_UUID}__card.pdf"


# --------------------------------------------------------------------- #
# parse_attachment_filename — inverse of compose
# --------------------------------------------------------------------- #

class TestParseAttachmentFilename:
    def test_simple_round_trip(self) -> None:
        composed = compose_attachment_filename(SAMPLE_UUID, "card.pdf")
        attachment_id, user_part = parse_attachment_filename(composed)
        assert attachment_id == SAMPLE_UUID
        assert user_part == "card.pdf"

    def test_user_filename_with_double_underscore_round_trips(self) -> None:
        # parse must split on the FIRST __ only — a user filename
        # that contains __ should survive intact.
        composed = compose_attachment_filename(
            SAMPLE_UUID, "my__weird__name.pdf"
        )
        attachment_id, user_part = parse_attachment_filename(composed)
        assert attachment_id == SAMPLE_UUID
        assert user_part == "my__weird__name.pdf"

    def test_missing_separator_raises(self) -> None:
        with pytest.raises(ValueError, match="missing"):
            parse_attachment_filename("nofilename.pdf")

    def test_invalid_uuid_prefix_raises(self) -> None:
        bad = f"not-a-uuid{_ATTACHMENT_NAME_SEP}card.pdf"
        with pytest.raises(ValueError, match="UUID prefix"):
            parse_attachment_filename(bad)


# --------------------------------------------------------------------- #
# write_attachment_stream — happy path + edge cases
# --------------------------------------------------------------------- #

class TestWriteAttachmentStream:
    def _jumper_folder(self, tmp_path: Path) -> Path:
        # A bare jumper folder without bootstrap — write_attachment
        # should auto-create the attachments/ subfolder.
        folder = tmp_path / "jumpers" / "11111111-1111-4111-8111-111111111111"
        folder.mkdir(parents=True)
        return folder

    def test_writes_file_at_expected_path(self, tmp_path: Path) -> None:
        folder = self._jumper_folder(tmp_path)
        result = write_attachment_stream(
            folder, SAMPLE_UUID, "card.pdf", [b"PDF bytes"]
        )
        expected_path = (
            folder / ATTACHMENTS_DIRNAME / f"{SAMPLE_UUID}__card.pdf"
        )
        assert expected_path.is_file()
        assert expected_path.read_bytes() == b"PDF bytes"
        assert result.size == len(b"PDF bytes")

    def test_returns_correct_sha256(self, tmp_path: Path) -> None:
        import hashlib

        folder = self._jumper_folder(tmp_path)
        payload = b"some attachment bytes for hashing"
        expected_sha = hashlib.sha256(payload).hexdigest()
        result = write_attachment_stream(
            folder, SAMPLE_UUID, "card.pdf", [payload]
        )
        assert result.sha256 == expected_sha

    def test_auto_creates_attachments_subfolder(self, tmp_path: Path) -> None:
        # Bare jumper folder — no attachments/ yet. Write succeeds
        # and the subfolder is created.
        folder = tmp_path / "jumpers" / "abc"
        folder.mkdir(parents=True)
        assert not (folder / ATTACHMENTS_DIRNAME).exists()
        write_attachment_stream(
            folder, SAMPLE_UUID, "card.pdf", [b"data"]
        )
        assert (folder / ATTACHMENTS_DIRNAME).is_dir()

    def test_streams_multiple_chunks(self, tmp_path: Path) -> None:
        # The whole point of streaming: the upload arrives in pieces
        # and we don't buffer the lot in RAM.
        folder = self._jumper_folder(tmp_path)
        chunks = [b"chunk1 ", b"chunk2 ", b"chunk3"]
        result = write_attachment_stream(
            folder, SAMPLE_UUID, "card.pdf", chunks
        )
        path = folder / ATTACHMENTS_DIRNAME / f"{SAMPLE_UUID}__card.pdf"
        assert path.read_bytes() == b"chunk1 chunk2 chunk3"
        assert result.size == len(b"chunk1 chunk2 chunk3")

    def test_zero_byte_attachment(self, tmp_path: Path) -> None:
        # Empty file is legal; sha256 of "" is the well-known value.
        folder = self._jumper_folder(tmp_path)
        result = write_attachment_stream(
            folder, SAMPLE_UUID, "empty.pdf", []
        )
        assert result.size == 0
        assert (
            result.sha256
            == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )
        path = folder / ATTACHMENTS_DIRNAME / f"{SAMPLE_UUID}__empty.pdf"
        assert path.is_file()
        assert path.read_bytes() == b""

    def test_no_tmp_file_remains_after_successful_write(
        self, tmp_path: Path
    ) -> None:
        # atomic_write_stream cleans up its .tmp on success; assert
        # the attachments/ folder doesn't carry a stray tmp.
        folder = self._jumper_folder(tmp_path)
        write_attachment_stream(
            folder, SAMPLE_UUID, "card.pdf", [b"data"]
        )
        attachments = folder / ATTACHMENTS_DIRNAME
        tmp_files = [p for p in attachments.iterdir() if p.suffix == ".tmp"]
        assert tmp_files == []

    def test_two_attachments_with_distinct_ids_coexist(
        self, tmp_path: Path
    ) -> None:
        # Two different attachments uploaded with the SAME user
        # filename ("card.pdf") still produce distinct on-disk paths
        # because their UUID prefixes differ.
        folder = self._jumper_folder(tmp_path)
        a_id = uuid4()
        b_id = uuid4()
        write_attachment_stream(folder, a_id, "card.pdf", [b"AAA"])
        write_attachment_stream(folder, b_id, "card.pdf", [b"BBB"])
        a_path = folder / ATTACHMENTS_DIRNAME / f"{a_id}__card.pdf"
        b_path = folder / ATTACHMENTS_DIRNAME / f"{b_id}__card.pdf"
        assert a_path.read_bytes() == b"AAA"
        assert b_path.read_bytes() == b"BBB"

    def test_rejects_invalid_user_filename(self, tmp_path: Path) -> None:
        folder = self._jumper_folder(tmp_path)
        with pytest.raises(ValueError):
            write_attachment_stream(
                folder, SAMPLE_UUID, "../escape.pdf", [b"data"]
            )


# --------------------------------------------------------------------- #
# delete_attachment_file
# --------------------------------------------------------------------- #

class TestDeleteAttachmentFile:
    def _jumper_folder_with_attachment(
        self, tmp_path: Path
    ) -> tuple[Path, UUID, str]:
        folder = tmp_path / "jumpers" / "abc"
        folder.mkdir(parents=True)
        attachment_id = SAMPLE_UUID
        filename = "card.pdf"
        write_attachment_stream(
            folder, attachment_id, filename, [b"content"]
        )
        return folder, attachment_id, filename

    def test_removes_existing_file(self, tmp_path: Path) -> None:
        folder, aid, name = self._jumper_folder_with_attachment(tmp_path)
        path = folder / ATTACHMENTS_DIRNAME / f"{aid}__{name}"
        assert path.is_file()
        delete_attachment_file(folder, aid, name)
        assert not path.exists()

    def test_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        folder = tmp_path / "jumpers" / "abc"
        folder.mkdir(parents=True)
        with pytest.raises(FileNotFoundError):
            delete_attachment_file(folder, SAMPLE_UUID, "missing.pdf")

    def test_does_not_remove_other_attachments(self, tmp_path: Path) -> None:
        # Delete one of two attachments — the other must survive.
        folder = tmp_path / "jumpers" / "abc"
        folder.mkdir(parents=True)
        a_id = uuid4()
        b_id = uuid4()
        write_attachment_stream(folder, a_id, "card.pdf", [b"AAA"])
        write_attachment_stream(folder, b_id, "card.pdf", [b"BBB"])
        delete_attachment_file(folder, a_id, "card.pdf")
        a_path = folder / ATTACHMENTS_DIRNAME / f"{a_id}__card.pdf"
        b_path = folder / ATTACHMENTS_DIRNAME / f"{b_id}__card.pdf"
        assert not a_path.exists()
        assert b_path.is_file()
        assert b_path.read_bytes() == b"BBB"

    def test_rejects_invalid_user_filename(self, tmp_path: Path) -> None:
        folder = tmp_path / "jumpers" / "abc"
        folder.mkdir(parents=True)
        # Sanitization rejects "../escape.pdf" — surfaces as ValueError
        # before any unlink happens.
        with pytest.raises(ValueError):
            delete_attachment_file(folder, SAMPLE_UUID, "../escape.pdf")
