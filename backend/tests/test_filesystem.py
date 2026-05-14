"""Tests for filesystem primitives."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.storage import filesystem as fs
from backend.storage.filesystem import (
    atomic_write,
    atomic_write_stream,
    jump_folder_name,
    normalize_nfc,
    safe_join,
    sanitize_filename,
    sanitize_folder_name,
)


class TestJumpFolderName:
    def test_title_present(self):
        # D4 post-2026-04-23 shape: [<jump#>] <title>.
        assert jump_folder_name(851, "Glacier jump") == "[851] Glacier jump"

    def test_title_absent(self):
        # Optional title → bare [<jump#>] prefix.
        assert jump_folder_name(851) == "[851]"

    def test_title_empty_string(self):
        assert jump_folder_name(851, "") == "[851]"

    def test_title_whitespace_only(self):
        # Whitespace-only title is treated as empty so the folder
        # doesn't end up named "[851]   " (which sanitize would
        # strip to "[851]" anyway, but explicit is better than
        # implicit).
        assert jump_folder_name(851, "   ") == "[851]"

    def test_title_unicode_accepted(self):
        # D4's ASCII-only rule was dropped on 2026-04-23 — Unicode
        # in titles is now allowed (modern macOS / Windows / Linux
        # filesystems all handle it, and NFC normalization keeps
        # cross-machine sync deterministic).
        out = jump_folder_name(1, "Première chute en wingsuit")
        assert out == "[1] Première chute en wingsuit"

    def test_title_nfc_normalized(self):
        # NFD "é" (e + U+0301) normalizes to NFC "é" (U+00E9) before
        # it becomes part of the folder name, so the same jump
        # entered on a Mac (NFD filename quirks) and on Windows
        # (NFC default) produces byte-identical folder names.
        nfd_title = "caf" + "e\u0301"
        out = jump_folder_name(2, nfd_title)
        assert out == "[2] caf\u00e9"

    def test_title_with_forbidden_char_raises(self):
        # Title passes through sanitize_folder_name on the full
        # [<N>] <title> string — a `/` anywhere triggers the same
        # ValueError that protects against path traversal in the
        # service layer.
        with pytest.raises(ValueError):
            jump_folder_name(3, "jump/with/slashes")

    def test_title_with_control_char_raises(self):
        with pytest.raises(ValueError):
            jump_folder_name(3, "bad\x00title")

    def test_title_trailing_period_raises(self):
        # Windows filename rule: no trailing period. sanitize
        # enforces it; tests that a title like "so good." fails.
        with pytest.raises(ValueError):
            jump_folder_name(4, "so good.")


class TestSanitizeFolderName:
    def test_accepts_ascii(self):
        assert sanitize_folder_name("[851] 2026-04-22") == "[851] 2026-04-22"

    @pytest.mark.parametrize("bad", ["", ".", ".."])
    def test_rejects_reserved(self, bad):
        with pytest.raises(ValueError):
            sanitize_folder_name(bad)

    @pytest.mark.parametrize("bad", ["a/b", "a\\b", "a:b", "a*b", "a?b", 'a"b', "a<b", "a>b", "a|b"])
    def test_rejects_forbidden_chars(self, bad):
        with pytest.raises(ValueError):
            sanitize_folder_name(bad)

    def test_rejects_control_chars(self):
        with pytest.raises(ValueError):
            sanitize_folder_name("tab\there")

    def test_normalizes_to_nfc(self):
        # NFD "é" = "e" + U+0301 combining acute. Should become single-codepoint NFC "é".
        nfd = "Dropzone\u00e9"  # already NFC for the assertion, but we test transform
        nfc_input = "Dropzone" + "e\u0301"  # NFD form
        out = sanitize_folder_name(nfc_input)
        assert out == nfd == normalize_nfc(nfc_input)

    # B2 + Q2: Windows reserved names and trailing space/period.
    @pytest.mark.parametrize("bad", [
        "CON", "con", "PRN", "aux", "NUL", "nul",
        "COM1", "COM9", "LPT1", "LPT9",
        "CON.txt", "nul.log",  # reserved even with extension
    ])
    def test_rejects_windows_reserved_names(self, bad):
        with pytest.raises(ValueError):
            sanitize_folder_name(bad)

    @pytest.mark.parametrize("bad", ["Sky.", "Sky . ", "Dropzone."])
    def test_rejects_trailing_dot(self, bad):
        # "Sky . " strips trailing whitespace -> "Sky ." -> still ends in "."
        # Pure trailing whitespace is tolerated (see separate test below).
        with pytest.raises(ValueError):
            sanitize_folder_name(bad)

    def test_trailing_whitespace_is_tolerant_but_period_still_rejected(self):
        # "Sky " strips to "Sky" — accepted. (UI tolerance.)
        assert sanitize_folder_name("Elsinore   ") == "Elsinore"

    def test_accepts_common_dropzone_names(self):
        for name in ["Elsinore", "Skydive Arizona", "Perris Valley", "Mile-Hi"]:
            assert sanitize_folder_name(name) == name

    def test_accepts_unicode(self):
        # D4 revised 2026-04-23: folder names may contain Unicode.
        # Sanitize still runs forbidden-char and reserved-name checks;
        # the only thing relaxed is the prose-level "ASCII-only" norm.
        for name in [
            "Première chute",
            "スカイダイブ",
            "[42] 🪂 first wingsuit",
            "Zurück zur Alm",
        ]:
            # Output is NFC-normalized; the input may already be NFC,
            # so equality after sanitize is the right check.
            assert sanitize_folder_name(name) == normalize_nfc(name)

    def test_rejects_oversize_byte_length(self):
        """CODE-4 (audit 2026-04-29): cap on UTF-8 byte length, not
        codepoint count. Without this cap, an emoji-dense title can
        push past 255 bytes while ``len()`` reports a sub-100 char
        count, and ``mkdir`` then fails with a platform-specific
        OSError instead of a clean ValueError.
        """
        # Each 🪂 (U+1FA82) is 4 UTF-8 bytes. 100 emojis = 400 bytes,
        # 100 codepoints. Byte cap (255) fires; codepoint cap (255)
        # would not.
        emoji_heavy = "🪂" * 100
        assert len(emoji_heavy) == 100
        assert len(emoji_heavy.encode("utf-8")) == 400
        with pytest.raises(ValueError, match="UTF-8 bytes"):
            sanitize_folder_name(emoji_heavy)

    def test_allows_boundary_byte_length(self):
        """255 bytes of ASCII is exactly at the cap and passes."""
        ok = "a" * 255
        assert sanitize_folder_name(ok) == ok

    def test_rejects_just_past_byte_boundary(self):
        with pytest.raises(ValueError, match="UTF-8 bytes"):
            sanitize_folder_name("a" * 256)

    def test_max_bytes_parameter_honoured(self):
        """The cap is parameterisable so the caller can be stricter
        (e.g. for systems with a smaller component cap, or for an
        input field with a UI-imposed lower limit).
        """
        with pytest.raises(ValueError, match="UTF-8 bytes"):
            sanitize_folder_name("hello world", max_bytes=5)
        # And under the cap still works.
        assert sanitize_folder_name("hi", max_bytes=5) == "hi"


class TestSanitizeFilename:
    def test_accepts_normal_file(self):
        assert sanitize_filename("flysight.csv") == "flysight.csv"

    def test_accepts_video(self):
        assert sanitize_filename("IMG_1234.MP4") == "IMG_1234.MP4"

    @pytest.mark.parametrize("bad", [
        "a/b.txt", "a\\b.txt", "a:b.txt", "a*b.txt", "a?b.txt",
        'a"b.txt', "a<b.txt", "a>b.txt", "a|b.txt",
    ])
    def test_rejects_forbidden_chars(self, bad):
        with pytest.raises(ValueError):
            sanitize_filename(bad)

    @pytest.mark.parametrize("bad", ["CON.txt", "con.txt", "NUL", "aux.log"])
    def test_rejects_windows_reserved(self, bad):
        with pytest.raises(ValueError):
            sanitize_filename(bad)

    @pytest.mark.parametrize("bad", ["file.", "file. "])
    def test_rejects_trailing_dot(self, bad):
        with pytest.raises(ValueError):
            sanitize_filename(bad)

    def test_rejects_oversize(self):
        with pytest.raises(ValueError):
            sanitize_filename("a" * 300)

    def test_allows_boundary_length(self):
        # 255 bytes ASCII = 255 chars; should succeed.
        assert sanitize_filename("a" * 255) == "a" * 255


class TestSafeJoin:
    def test_joins_under_root(self, tmp_path: Path):
        out = safe_join(tmp_path, "jumps", "[1] 2026-04-22")
        assert out.is_relative_to(tmp_path.resolve())
        assert out.name == "[1] 2026-04-22"

    def test_rejects_traversal(self, tmp_path: Path):
        with pytest.raises(ValueError):
            safe_join(tmp_path, "..")

    def test_rejects_forbidden_chars_in_parts(self, tmp_path: Path):
        with pytest.raises(ValueError):
            safe_join(tmp_path, "evil/../escape")


class TestAtomicWrite:
    def test_writes_bytes(self, tmp_path: Path):
        target = tmp_path / "out.bin"
        atomic_write(target, b"hello")
        assert target.read_bytes() == b"hello"

    def test_replaces_existing_file(self, tmp_path: Path):
        target = tmp_path / "out.bin"
        target.write_bytes(b"old")
        atomic_write(target, b"new")
        assert target.read_bytes() == b"new"

    def test_creates_parent_dirs(self, tmp_path: Path):
        target = tmp_path / "deep" / "nested" / "file.txt"
        atomic_write(target, b"x")
        assert target.read_bytes() == b"x"

    def test_no_temp_left_on_success(self, tmp_path: Path):
        target = tmp_path / "out.bin"
        atomic_write(target, b"x")
        assert not (tmp_path / "out.bin.tmp").exists()


class TestAtomicWriteStream:
    """D30's streaming sibling of atomic_write.

    Each test is self-contained; no mocking of filesystem or hashlib
    (CLAUDE.md §7 — prefer real temp dirs over mocks for storage
    primitives). Hashes are verified against an independent
    ``hashlib.sha256(bytes).hexdigest()`` on the same bytes so a bug
    in the streaming path that miscounts chunks would surface.
    """

    def test_writes_bytes_from_list(self, tmp_path: Path):
        target = tmp_path / "out.bin"
        result = atomic_write_stream(target, [b"hello ", b"world"])
        assert target.read_bytes() == b"hello world"
        assert result.size == 11
        assert result.sha256 == hashlib.sha256(b"hello world").hexdigest()

    def test_writes_bytes_from_generator(self, tmp_path: Path):
        # Real uploads arrive as a generator/iterator, not a list.
        # Verify the function doesn't depend on len() / sequence
        # indexing of the chunks argument.
        def chunks():
            yield b"abc"
            yield b"defg"

        target = tmp_path / "out.bin"
        result = atomic_write_stream(target, chunks())
        assert target.read_bytes() == b"abcdefg"
        assert result.sha256 == hashlib.sha256(b"abcdefg").hexdigest()

    def test_zero_bytes_is_valid(self, tmp_path: Path):
        # D21 doesn't forbid empty uploads; sha256 of empty string is
        # the conventional ``e3b0c442...`` digest. Matters because
        # some users might attach a FlySight file that hasn't started
        # recording, and we shouldn't choke.
        target = tmp_path / "empty.bin"
        result = atomic_write_stream(target, [])
        assert target.read_bytes() == b""
        assert result.size == 0
        assert result.sha256 == hashlib.sha256(b"").hexdigest()

    def test_empty_chunks_are_skipped_not_hashed(self, tmp_path: Path):
        # Some iterators emit a trailing empty bytes() as EOF marker.
        # Those must be tolerated without affecting the hash.
        target = tmp_path / "out.bin"
        result = atomic_write_stream(target, [b"hello", b"", b" world", b""])
        assert target.read_bytes() == b"hello world"
        assert result.sha256 == hashlib.sha256(b"hello world").hexdigest()

    def test_large_file_streaming(self, tmp_path: Path):
        # 5 MiB through 64-KiB chunks. Verifies incremental hashing
        # produces the same digest as a single-shot hash of the
        # concatenated bytes — which is the whole point of incremental
        # hashing. Also verifies no "all bytes in one bytearray"
        # allocation is needed in the implementation (size is much
        # larger than a single chunk).
        chunk = b"x" * 65536
        chunks = [chunk] * 80  # 5 MiB
        target = tmp_path / "big.bin"
        result = atomic_write_stream(target, chunks)
        assert result.size == 5 * 1024 * 1024
        expected = hashlib.sha256(b"".join(chunks)).hexdigest()
        assert result.sha256 == expected
        assert target.stat().st_size == result.size

    def test_replaces_existing_file(self, tmp_path: Path):
        target = tmp_path / "out.bin"
        target.write_bytes(b"old bytes")
        atomic_write_stream(target, [b"new"])
        assert target.read_bytes() == b"new"

    def test_creates_parent_dirs(self, tmp_path: Path):
        target = tmp_path / "deep" / "nested" / "file.txt"
        result = atomic_write_stream(target, [b"x"])
        assert target.read_bytes() == b"x"
        assert result.sha256 == hashlib.sha256(b"x").hexdigest()

    def test_no_temp_left_on_success(self, tmp_path: Path):
        target = tmp_path / "out.bin"
        atomic_write_stream(target, [b"x"])
        assert not (tmp_path / "out.bin.tmp").exists()

    def test_no_temp_left_on_failure(self, tmp_path: Path):
        # Simulate a mid-stream failure. The tmp file must be cleaned
        # up so a retry doesn't see stale bytes, and the destination
        # must not exist (os.replace never ran).
        target = tmp_path / "out.bin"

        def explodes():
            yield b"first chunk"
            raise RuntimeError("upload connection dropped")

        with pytest.raises(RuntimeError, match="connection dropped"):
            atomic_write_stream(target, explodes())

        assert not target.exists()
        assert not (tmp_path / "out.bin.tmp").exists()

    def test_failure_leaves_existing_destination_intact(self, tmp_path: Path):
        # Same crash-during-write but there's already a file at the
        # destination. Key D10 invariant: os.replace happens last, so
        # the original bytes are still there after a mid-write
        # failure.
        target = tmp_path / "out.bin"
        target.write_bytes(b"original")

        def explodes():
            yield b"new"
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            atomic_write_stream(target, explodes())

        assert target.read_bytes() == b"original"
        assert not (tmp_path / "out.bin.tmp").exists()

    def test_returned_size_matches_on_disk_size(self, tmp_path: Path):
        # Belt-and-braces: the size field must match stat().st_size.
        # A bug that double-counted chunks would fail this.
        target = tmp_path / "out.bin"
        result = atomic_write_stream(target, [b"a", b"bc", b"def"])
        assert result.size == target.stat().st_size == 6


# --------------------------------------------------------------------------- #
# Durability primitives — Darwin F_FULLFSYNC + parent-directory fsync (CODE-2)
# --------------------------------------------------------------------------- #

class TestFullFsync:
    """Cross-platform fsync that flushes through the drive's cache.

    Audit CODE-2 (2026-04-29). On Darwin, BSD ``fsync(2)`` only
    commits to the OS buffer cache; ``F_FULLFSYNC`` is required to
    flush the drive's internal write cache. SQLite's ``fullfsync``
    pragma cites this gap.
    """

    def test_full_fsync_calls_os_fsync_on_non_darwin(self, tmp_path: Path):
        # Pin the non-Darwin path: plain os.fsync, no fcntl call.
        target = tmp_path / "f.txt"
        target.write_bytes(b"hi")
        with target.open("rb") as f:
            with (
                patch.object(fs, "sys") as fake_sys,
                patch.object(fs.os, "fsync") as fake_fsync,
            ):
                fake_sys.platform = "linux"
                fs._full_fsync(f.fileno())
            fake_fsync.assert_called_once_with(f.fileno())

    @pytest.mark.skipif(
        sys.platform != "darwin",
        reason="F_FULLFSYNC only exists on Darwin",
    )
    def test_full_fsync_calls_fcntl_on_darwin(self, tmp_path: Path):
        # On a real Mac, the call goes through fcntl(F_FULLFSYNC) and
        # returns without raising. Most CI cells aren't Darwin so this
        # is a Mac-only assertion; the cross-platform fallback test
        # above covers Linux + Windows.
        import fcntl
        target = tmp_path / "f.txt"
        target.write_bytes(b"hi")
        with target.open("rb") as f:
            # Should complete without raising. We don't mock here —
            # the real fcntl call exercises the kernel path.
            fs._full_fsync(f.fileno())
            # Sanity check: the constant exists where we expect it.
            assert hasattr(fcntl, "F_FULLFSYNC")


class TestFsyncDir:
    """Persist directory metadata so a fresh ``rename`` survives a crash.

    Audit CODE-2 (2026-04-29). POSIX ``rename(2)`` is atomic but the
    new entry's *durability* requires an explicit fsync of the parent
    directory. Windows handles this via NTFS's transaction log so the
    helper is a no-op there.
    """

    def test_fsync_dir_no_op_on_windows(self, tmp_path: Path):
        # Pin the no-op: even with a non-existent path, we don't try
        # to open it on Windows.
        with patch.object(fs, "sys") as fake_sys:
            fake_sys.platform = "win32"
            # Should return without error even though the path is bogus
            # (we never call os.open in the win32 branch).
            fs._fsync_dir(tmp_path / "does-not-exist")

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX-only: directory fsync via os.open + os.fsync",
    )
    def test_fsync_dir_runs_on_posix(self, tmp_path: Path):
        # Should complete without raising. We can't easily verify the
        # syscall hit the device, but exercising the code path catches
        # bugs like a missing close (FD leak) or bad mode.
        fs._fsync_dir(tmp_path)
        # Repeat to confirm no FD leak / state corruption.
        fs._fsync_dir(tmp_path)

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX-only path",
    )
    def test_atomic_write_calls_fsync_dir_after_replace(
        self, tmp_path: Path
    ):
        # End-to-end pin: atomic_write must invoke _fsync_dir after
        # os.replace so the rename is durable per POSIX semantics.
        target = tmp_path / "out.bin"
        with patch.object(fs, "_fsync_dir") as fake_dir_fsync:
            atomic_write(target, b"payload")
            fake_dir_fsync.assert_called_once_with(target.parent)
        # Sanity check: the actual file write still happened.
        assert target.read_bytes() == b"payload"

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX-only path",
    )
    def test_atomic_write_stream_calls_fsync_dir_after_replace(
        self, tmp_path: Path
    ):
        # Same end-to-end pin for the streaming sibling.
        target = tmp_path / "out.bin"
        with patch.object(fs, "_fsync_dir") as fake_dir_fsync:
            atomic_write_stream(target, [b"payload"])
            fake_dir_fsync.assert_called_once_with(target.parent)
        assert target.read_bytes() == b"payload"
