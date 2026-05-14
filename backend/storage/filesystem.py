"""Filesystem primitives: atomic writes, path safety, name hygiene (D4, D10).

Every file write in the backend goes through `atomic_write` (or its
streaming sibling `atomic_write_stream` for attachment uploads, per
D21+D30). Every path derived from user input goes through `safe_join`
+ `sanitize_folder_name`. Attachment filenames go through
`sanitize_filename` (Q5).
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import sys
import unicodedata
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

# Platform-specific durability primitives — see ``_full_fsync`` and
# ``_fsync_dir`` below. Imported here so the module-level constants
# document the platform check at the top of the file.
if sys.platform == "darwin":  # pragma: no cover - import-time platform check
    import fcntl  # noqa: F401  # used inside _full_fsync

# D4: forbidden characters on any platform. Reserved by Windows and
# inadmissible in POSIX paths (``/``), also rejected elsewhere for
# portability.
#
# Ref: https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file
_FORBIDDEN_CHARS = set('/\\:*?"<>|')

# Current / parent / empty are never valid names.
_RESERVED_NAMES = {".", "..", ""}

# Windows reserved device names — case-insensitive, and reserved
# *regardless of extension* ("CON.txt" is still invalid). The check is
# against the stem (portion before the first dot).
#
# Ref: https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file
# ("Do not use the following reserved names for the name of a file.")
_WINDOWS_RESERVED_STEMS = frozenset({
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
})


def normalize_nfc(s: str) -> str:
    """Normalize text to Unicode NFC (D4).

    macOS stores filenames as NFD on HFS+ and as whatever-you-wrote on
    APFS; Windows stores as NFC. Normalizing on write means the same
    jump produces the same folder name on every OS, which matters for
    cloud sync and for deterministic SHA256 manifests.
    """
    return unicodedata.normalize("NFC", s)


def _reject_forbidden_chars(name: str, label: str) -> None:
    for ch in name:
        if ch in _FORBIDDEN_CHARS:
            raise ValueError(f"forbidden character {ch!r} in {label}: {name!r}")
        if ord(ch) < 32 or ord(ch) == 0x7F:
            raise ValueError(f"control character in {label}: {name!r}")


def _reject_windows_reserved(name: str, label: str) -> None:
    """Reject Windows reserved device names (CON, NUL, COM1, ...) and
    names that would be illegal on Windows filesystems (trailing space,
    trailing dot). See Microsoft's naming-a-file guide.

    The check is against the portion of the name before the first dot
    because Windows treats ``CON.txt`` the same as ``CON``.
    """
    # Windows disallows names ending in ``.`` or space — even outside
    # reserved names. (The shell silently trims them, which breaks
    # round-tripping.)
    if name.endswith(" ") or name.endswith("."):
        raise ValueError(
            f"{label} ends in space or period (forbidden on Windows): {name!r}"
        )
    stem = name.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED_STEMS:
        raise ValueError(f"reserved Windows device name in {label}: {name!r}")


# 255 bytes is the most restrictive single-component cap across the
# filesystems this app targets:
#   - ext4 / xfs / btrfs (Linux): 255 bytes
#   - APFS (macOS): 255 UTF-8 bytes
#   - NTFS (Windows): 255 UTF-16 code units (Unicode chars in the BMP
#     are 2 bytes, so for non-BMP-heavy strings ext4's 255-byte cap is
#     stricter than NTFS's 255-UTF-16-unit cap)
# Capping in UTF-8 bytes guards every target. ASCII titles get a 255-
# char ceiling; emoji-dense titles get a 63-character ceiling at worst
# (each non-BMP emoji is 4 bytes UTF-8) — both well above any plausible
# UI title length, but a regression bug or pasted blob can still hit it.
_MAX_FOLDER_NAME_BYTES = 255


def sanitize_folder_name(
    name: str, max_bytes: int = _MAX_FOLDER_NAME_BYTES
) -> str:
    """Normalize and validate a folder name (D4, Q2).

    Returns the cleaned name. Raises ``ValueError`` on:

    - forbidden characters (``/\\:*?"<>|``) or control characters
    - empty / ``.`` / ``..``
    - Windows reserved device names (CON, PRN, AUX, NUL, COM1..9, LPT1..9),
      case-insensitive, regardless of extension
    - names ending in space or period (illegal on Windows)
    - UTF-8 byte length exceeding ``max_bytes`` (default 255 — the
      single-component cap on ext4/APFS/NTFS). Audit CODE-4: a long
      emoji-dense title can blow past 255 bytes while ``len()``
      reports a sub-100 character count, and ``mkdir`` then surfaces
      a platform-specific OSError instead of a clean validation
      failure.

    Leading/trailing whitespace is stripped first (UI tolerance).

    Does not touch the filesystem.
    """
    name = normalize_nfc(name).strip()
    if name in _RESERVED_NAMES:
        raise ValueError(f"invalid folder name: {name!r}")
    _reject_forbidden_chars(name, "folder name")
    _reject_windows_reserved(name, "folder name")
    nbytes = len(name.encode("utf-8"))
    if nbytes > max_bytes:
        raise ValueError(
            f"folder name too long ({nbytes} > {max_bytes} UTF-8 bytes): {name!r}"
        )
    return name


# Per Q5: attachment filenames use the same D4 character rules as folder
# names. The file extension is preserved (interior dots are fine); only
# a trailing dot is rejected.
_MAX_FILENAME_LEN = 255  # Windows + most POSIX filesystems.


def sanitize_filename(name: str, max_length: int = _MAX_FILENAME_LEN) -> str:
    """Normalize and validate a filename including extension (D4, Q5, S4).

    Same rules as ``sanitize_folder_name`` plus a length cap (255 by
    default, matching Windows NTFS / most POSIX filesystems). Interior
    dots are allowed (for extensions like ``flysight.csv``).

    Does not touch the filesystem.
    """
    name = normalize_nfc(name).strip()
    if name in _RESERVED_NAMES:
        raise ValueError(f"invalid filename: {name!r}")
    if len(name) > max_length:
        raise ValueError(
            f"filename too long ({len(name)} > {max_length}): {name!r}"
        )
    _reject_forbidden_chars(name, "filename")
    _reject_windows_reserved(name, "filename")
    return name


def jump_folder_name(jump_number: int, title: str | None = None) -> str:
    """Canonical jump folder name per D4.

    Shape:
        * ``[<jump#>] <title>`` when ``title`` is provided and non-empty.
        * ``[<jump#>]`` otherwise.

    The full result is run through ``sanitize_folder_name`` so callers
    can use the return value as a path component without additional
    validation. That means a ``title`` containing forbidden characters
    (``/``, control chars, Windows reserved names, trailing period/
    space, etc.) raises ``ValueError`` here — the service layer
    translates that into a 422 validation error for the API consumer.

    Uniqueness note: the ``[<N>]`` prefix — not the title — is what
    D23 uses as the collision key. Two jumps with the same number
    would produce DIFFERENT folder names under the new D4 shape
    (title varies), so D23's duplicate-number check scans the parent
    for the prefix ``[<N>]`` or ``[<N>] ``, not just for an exact
    folder-name match.
    """
    if title is None or not title.strip():
        return sanitize_folder_name(f"[{jump_number}]")
    return sanitize_folder_name(f"[{jump_number}] {title}")


def safe_join(root: Path, *parts: str) -> Path:
    """Join parts under root, rejecting any attempt to escape.

    Each part is sanitized first. The final resolved path must remain
    within root; otherwise ValueError.
    """
    root = Path(root).resolve()
    candidate = root
    for part in parts:
        candidate = candidate / sanitize_folder_name(part)
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"path escapes root: {candidate} vs {root}")
    return resolved


def _full_fsync(fd: int) -> None:
    """Force the bytes through the OS write cache **and** the drive.

    On Linux and Windows, ``os.fsync`` is sufficient — both flush
    through the storage stack to the device. On Darwin (macOS,
    iOS-derived), BSD ``fsync(2)`` only commits to the OS buffer
    cache; the drive's own write cache is not flushed unless the
    code requests ``fcntl(fd, F_FULLFSYNC)`` explicitly. SQLite
    documents this gap and ships with ``fullfsync`` enabled by
    default on Darwin precisely because of it.

    Without this, a kernel panic or hard power loss after
    ``atomic_write`` returns but before the drive completes its own
    cache flush can lose the just-written bytes — the bytes never
    reached the platter / NAND. Likelihood is low on a modern Mac
    SSD and very low on internal drives, but real on bus-powered
    USB drives and some external SSDs.

    Refs:
      - Apple ``fsync(2)`` manual + ``F_FULLFSYNC`` note:
        https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/fsync.2.html
      - SQLite ``PRAGMA fullfsync``:
        https://www.sqlite.org/pragma.html#pragma_fullfsync
    """
    if sys.platform == "darwin" and hasattr(fcntl, "F_FULLFSYNC"):
        fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
    else:
        os.fsync(fd)


def _fsync_dir(directory: Path) -> None:
    """Persist a directory's metadata so a fresh ``rename`` survives a crash.

    POSIX ``rename(2)`` is atomic at the kernel level: on success,
    the new directory entry exists; on a crash before the rename
    returns, the old entry still exists. But the *durability* of the
    new entry — the guarantee that the rename survives a power loss
    that follows the rename's return — requires an explicit
    ``fsync`` on the parent directory. Without it, modern filesystems
    (ext4, XFS, btrfs, APFS) can in theory replay a crash boot to a
    state where the new file's bytes exist on disk but the directory
    entry pointing at them does not — the "0-byte file after crash"
    Firefox bug class.

    POSIX-only — ``os.open`` of a directory is not portable to
    Windows, where directory metadata persistence is handled by
    NTFS's transaction log automatically and the equivalent
    primitive doesn't exist in the public Win32 API. On Windows the
    function is a no-op.

    Refs:
      - LWN — ext4 and data loss:
        https://lwn.net/Articles/322823/
      - POSIX ``rename(2)`` durability:
        https://pubs.opengroup.org/onlinepubs/9699919799/functions/rename.html
    """
    if sys.platform == "win32":
        return
    fd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically and durably (D10).

    Writes to ``<path>.tmp`` in the same directory, ``F_FULLFSYNC``s
    the file (or ``os.fsync`` on non-Darwin platforms), ``os.replace()``s
    into place, then ``fsync``s the parent directory so the rename
    itself is durable. ``os.replace`` is POSIX ``rename(2)`` (atomic
    at kernel level) on Unix, and ``MoveFileExW`` with
    ``MOVEFILE_REPLACE_EXISTING`` on Windows.

    Crash semantics:

    - The source file at ``path`` is left untouched until the
      ``os.replace`` call succeeds, so a crash *before* the rename
      leaves the old version intact.
    - On Darwin, ``F_FULLFSYNC`` flushes the drive's internal write
      cache, not just the OS buffer cache — so a power loss after
      this call returns will not lose the bytes (see ``_full_fsync``).
    - The parent-directory fsync after ``os.replace`` ensures the
      new directory entry is durable. Without it, modern filesystems
      can in theory replay a crash boot to a state where the new
      bytes exist but the entry pointing at them doesn't (see
      ``_fsync_dir``). On Windows, NTFS's transaction log handles
      this without an explicit user-space call.
    - Caveat: on Windows, ``MoveFileExW`` is usually atomic but may,
      under uncommon conditions (cross-volume, some network shares),
      fall back to a copy+delete that is not atomic. We accept the
      risk because XML is the source of truth (D3); a partial write
      would be caught by ``SHA256SUMS`` verify on next open and the
      DB row is rebuildable.

    Refs:
      - https://docs.python.org/3/library/os.html#os.replace
      - https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"{path.name}.tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            _full_fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    except Exception:
        # Best effort cleanup; the original file (if any) is untouched
        # because os.replace happens last.
        if tmp.exists():
            with contextlib.suppress(OSError):
                tmp.unlink()
        raise


class StreamWriteResult(NamedTuple):
    """Return shape of :func:`atomic_write_stream`.

    ``sha256`` is the 64-char lowercase hex digest of every byte that
    reached the tmp file before ``os.replace`` landed. ``size`` is the
    byte count. Both are computed during the streaming pass — callers
    never need a second read.
    """

    sha256: str
    size: int


def atomic_write_stream(
    path: Path, chunks: Iterable[bytes]
) -> StreamWriteResult:
    """Stream ``chunks`` to ``path`` atomically, returning sha256+size (D10, D21, D30).

    Streaming sibling of :func:`atomic_write`. Consumes ``chunks`` one
    at a time — bounded memory regardless of total size, which is the
    D21 requirement for attachment uploads (a multi-gigabyte jump
    video must not buffer fully in RAM). The SHA-256 is computed
    incrementally during the write so the caller gets
    ``<attachment>/<sha256>`` without a second pass over the bytes —
    this is what D25 step 2 means by "the hash and the bytes are
    committed together".

    Crash semantics mirror :func:`atomic_write`:

    - bytes are written to ``<path>.tmp``, then ``F_FULLFSYNC``ed
      (Darwin) or ``fsync``ed (other POSIX / Windows), then
      ``os.replace``d into place, then the parent directory is
      ``fsync``ed (POSIX-only) so the rename itself is durable;
    - on any exception during the stream, the tmp file is cleaned up
      best-effort and the exception propagates unchanged;
    - the destination ``path`` is untouched until ``os.replace``
      succeeds, so a crash mid-stream leaves no partial destination
      file.

    Zero-byte input is legal and produces the SHA-256 of the empty
    string (``e3b0c442...``) with ``size=0``.

    Parameters
    ----------
    path:
        Destination path. Parent directory is created if missing,
        matching ``atomic_write`` behaviour.
    chunks:
        Any iterable of ``bytes`` objects. Empty chunks are skipped
        for write economy but do not affect the running hash. A
        generator, a file's ``iter(lambda: f.read(65536), b"")``, or
        a plain ``list[bytes]`` all work.

    Returns
    -------
    StreamWriteResult:
        ``(sha256, size)`` NamedTuple covering exactly the bytes
        ``os.replace``d into place.

    Refs:
      - hashlib incremental update API:
        https://docs.python.org/3/library/hashlib.html#hashlib.hash.update
      - ``os.fsync`` semantics:
        https://docs.python.org/3/library/os.html#os.fsync
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"{path.name}.tmp"
    hasher = hashlib.sha256()
    size = 0
    try:
        with open(tmp, "wb") as f:
            for chunk in chunks:
                if not chunk:
                    # Tolerate empty chunks — some framework iterators
                    # (e.g. asyncio StreamReaders unwrapped to sync)
                    # emit a terminal empty ``b""`` as EOF signal.
                    continue
                hasher.update(chunk)
                f.write(chunk)
                size += len(chunk)
            f.flush()
            _full_fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    except Exception:
        if tmp.exists():
            with contextlib.suppress(OSError):
                tmp.unlink()
        raise
    return StreamWriteResult(sha256=hasher.hexdigest(), size=size)
