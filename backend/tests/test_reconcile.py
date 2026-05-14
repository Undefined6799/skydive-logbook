"""Tests for D25 ``folder_reconcile``.

Contract under test:

  * Missing or stale SHA256SUMS → regenerated from jump.xml's claims
    (D25 §"On-open reconciliation").
  * In-sync SHA256SUMS → left alone, function returns False.
  * Structural comparison — line order and the ``<hash>  <path>`` vs
    ``<hash> *<path>`` space convention are tolerated; only a real
    change in ``(digest, rel)`` tuples triggers regeneration.
  * Broken jump.xml (missing, malformed, XSD-invalid) → propagates
    the underlying error. Reconcile does not paper over "not a valid
    jump" per D25.
  * Writes go through ``atomic_write``; an INFO log record lands on
    regeneration; no log on no-op.
  * Idempotent: a second call on a reconciled folder is a no-op.

These tests exercise ``folder_reconcile`` in isolation by
hand-constructing folder states. The subprocess SIGKILL harness D25
calls for is scoped to ``create_jump``'s crash-path tests (a future
slice); for reconcile itself, direct state construction is equivalent
and far simpler.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

from backend.models.jump import Attachment, Jump
from backend.observability.logging import JsonFormatter, configure_logging
from backend.storage import manifest
from backend.storage.manifest import JUMP_XML_NAME, MANIFEST_NAME
from backend.storage.reconcile import folder_reconcile
from backend.xml.serialize import jump_to_bytes
from backend.xml.validator import XMLMalformed, XSDValidationError

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _build_folder(
    folder: Path,
    *,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> None:
    """Build a valid jump folder at ``folder`` with optional attachments.

    Each attachment tuple: ``(filename, bytes_on_disk, claimed_hash)``.
    """
    folder.mkdir(parents=True, exist_ok=True)
    model_attachments: list[Attachment] = []
    for filename, data, claimed_hash in attachments or []:
        (folder / filename).write_bytes(data)
        model_attachments.append(
            Attachment(
                filename=filename,
                sha256=claimed_hash,
                size=len(data),
                content_type="application/octet-stream",
            )
        )
    jump = Jump(
        id=uuid4(),
        jump_number=851,
        date=date(2026, 4, 22),
        dropzone="Skydive Elsinore",
        exit_altitude_m=4000,
        deployment_altitude_m=900,
        attachments=model_attachments,
    )
    (folder / JUMP_XML_NAME).write_bytes(jump_to_bytes(jump))


@pytest.fixture
def log_buffer():
    """Capture root-logger output through an in-memory buffer.

    Same pattern as ``test_observability_events``: configure_logging
    mutates global state; we restore handlers / level / uvicorn.access
    in ``finally`` so reconcile tests can assert on JSON lines without
    polluting unrelated tests.
    """
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    access_disabled = logging.getLogger("uvicorn.access").disabled
    try:
        configure_logging("INFO")
        import sys as _sys

        handler = next(
            h
            for h in root.handlers
            if isinstance(h, logging.StreamHandler) and h.stream is _sys.stderr
        )
        buf = io.StringIO()
        handler.stream = buf
        # Belt-and-braces: confirm the JsonFormatter is the one we installed.
        assert isinstance(handler.formatter, JsonFormatter)
        yield buf
    finally:
        root.handlers = saved_handlers
        root.level = saved_level
        logging.getLogger("uvicorn.access").disabled = access_disabled


def _log_records(buf: io.StringIO) -> list[dict]:
    raw = buf.getvalue().strip()
    return [json.loads(line) for line in raw.splitlines() if line]


# --------------------------------------------------------------------------- #
# Regeneration cases
# --------------------------------------------------------------------------- #

class TestRegenerates:
    def test_missing_manifest_is_written(self, tmp_path: Path):
        _build_folder(tmp_path)
        assert not (tmp_path / MANIFEST_NAME).exists()
        changed = folder_reconcile(tmp_path)
        assert changed is True
        assert (tmp_path / MANIFEST_NAME).is_file()

    def test_stale_manifest_is_replaced(self, tmp_path: Path):
        att_data = b"flysight"
        att_hash = hashlib.sha256(att_data).hexdigest()
        _build_folder(
            tmp_path,
            attachments=[("flysight.csv", att_data, att_hash)],
        )
        # Plant a manifest with a bogus (but syntactically valid) entry.
        bogus = (
            "0" * 64 + "  flysight.csv\n"
            + "0" * 64 + f"  {JUMP_XML_NAME}\n"
        ).encode()
        (tmp_path / MANIFEST_NAME).write_bytes(bogus)

        changed = folder_reconcile(tmp_path)
        assert changed is True

        written = (tmp_path / MANIFEST_NAME).read_bytes()
        assert written != bogus
        # And the written content carries the correct attachment hash.
        assert att_hash.encode() in written

    def test_malformed_existing_manifest_is_replaced(self, tmp_path: Path):
        # A corrupted on-disk manifest (bad bytes, wrong format) must
        # not block reconcile. The D25 contract is "regenerate if not
        # in sync"; an unparseable manifest is trivially not in sync.
        _build_folder(tmp_path)
        (tmp_path / MANIFEST_NAME).write_bytes(b"not a manifest at all\n")
        changed = folder_reconcile(tmp_path)
        assert changed is True
        # New content parses cleanly.
        entries = manifest.parse((tmp_path / MANIFEST_NAME).read_bytes())
        assert entries  # non-empty


# --------------------------------------------------------------------------- #
# No-op cases
# --------------------------------------------------------------------------- #

class TestInSyncIsNoop:
    def test_in_sync_manifest_left_alone(self, tmp_path: Path):
        _build_folder(tmp_path)
        # Build the right manifest the first time.
        folder_reconcile(tmp_path)
        before_bytes = (tmp_path / MANIFEST_NAME).read_bytes()
        before_mtime = (tmp_path / MANIFEST_NAME).stat().st_mtime_ns

        changed = folder_reconcile(tmp_path)
        assert changed is False
        # Byte-identical and untouched (mtime unchanged because we did
        # not atomic_write).
        assert (tmp_path / MANIFEST_NAME).read_bytes() == before_bytes
        assert (tmp_path / MANIFEST_NAME).stat().st_mtime_ns == before_mtime

    def test_idempotent_across_three_calls(self, tmp_path: Path):
        _build_folder(tmp_path)
        first = folder_reconcile(tmp_path)
        second = folder_reconcile(tmp_path)
        third = folder_reconcile(tmp_path)
        assert (first, second, third) == (True, False, False)

    def test_structurally_equivalent_but_reordered_is_noop(self, tmp_path: Path):
        # manifest.parse is order-agnostic: if the same (digest, path)
        # tuples are present, reconcile treats them as equivalent. This
        # guards against a regression where we'd do byte-exact compare
        # and spuriously regenerate on editor-reformatted files.
        att_data = b"x"
        att_hash = hashlib.sha256(att_data).hexdigest()
        _build_folder(
            tmp_path,
            attachments=[("a.bin", att_data, att_hash)],
        )
        expected = manifest.from_jump_xml(tmp_path)
        entries = manifest.parse(expected)
        # Write the same entries but in reverse order.
        reversed_bytes = "".join(
            f"{digest}  {rel}\n" for digest, rel in reversed(entries)
        ).encode()
        (tmp_path / MANIFEST_NAME).write_bytes(reversed_bytes)
        # And a trailing blank line — still structurally equivalent per
        # manifest.parse's tolerance.
        (tmp_path / MANIFEST_NAME).write_bytes(reversed_bytes + b"\n")

        changed = folder_reconcile(tmp_path)
        assert changed is False

    def test_text_mode_star_notation_is_tolerated(self, tmp_path: Path):
        # GNU shasum text-mode output uses ``<hash> *<path>``. Our
        # parse() accepts it; reconcile should not regenerate just
        # because an external tool used the other flavour.
        _build_folder(tmp_path)
        folder_reconcile(tmp_path)  # first pass writes the binary-mode file
        canonical = manifest.parse((tmp_path / MANIFEST_NAME).read_bytes())
        # Rewrite in star-notation.
        starred = "".join(
            f"{digest} *{rel}\n" for digest, rel in canonical
        ).encode()
        (tmp_path / MANIFEST_NAME).write_bytes(starred)

        changed = folder_reconcile(tmp_path)
        assert changed is False


# --------------------------------------------------------------------------- #
# Error propagation
# --------------------------------------------------------------------------- #

class TestBrokenJumpXml:
    def test_missing_jump_xml_raises(self, tmp_path: Path):
        # Per D25: "Absence of jump.xml means the folder is not a valid
        # jump." reconcile propagates; ``verify`` is the tool that
        # reports it.
        with pytest.raises(FileNotFoundError):
            folder_reconcile(tmp_path)

    def test_malformed_jump_xml_raises(self, tmp_path: Path):
        (tmp_path / JUMP_XML_NAME).write_bytes(b"<broken<")
        with pytest.raises(XMLMalformed):
            folder_reconcile(tmp_path)

    def test_xsd_invalid_jump_xml_raises(self, tmp_path: Path):
        (tmp_path / JUMP_XML_NAME).write_bytes(
            b'<?xml version="1.0"?>'
            b'<jump xmlns="https://skydive-logbook.org/schema/v1"/>'
        )
        with pytest.raises(XSDValidationError):
            folder_reconcile(tmp_path)

    def test_raise_leaves_existing_manifest_untouched(self, tmp_path: Path):
        # A broken jump.xml must not cause reconcile to delete or
        # overwrite a pre-existing SHA256SUMS. Preserve evidence.
        (tmp_path / MANIFEST_NAME).write_bytes(b"pre-existing content\n")
        (tmp_path / JUMP_XML_NAME).write_bytes(b"<busted<")
        with pytest.raises(XMLMalformed):
            folder_reconcile(tmp_path)
        assert (tmp_path / MANIFEST_NAME).read_bytes() == b"pre-existing content\n"


# --------------------------------------------------------------------------- #
# Observability
# --------------------------------------------------------------------------- #

class TestLogging:
    def test_logs_regenerated_on_write(self, tmp_path: Path, log_buffer):
        _build_folder(tmp_path)
        changed = folder_reconcile(tmp_path)
        assert changed is True

        events = [
            e
            for e in _log_records(log_buffer)
            if e["message"] == "manifest_regenerated"
        ]
        assert len(events) == 1
        event = events[0]
        assert event["level"] == "INFO"
        assert event["logger"] == "backend.storage.reconcile"
        assert event["folder"] == str(tmp_path)

    def test_no_log_on_noop(self, tmp_path: Path, log_buffer):
        _build_folder(tmp_path)
        folder_reconcile(tmp_path)  # regenerates → logs
        # Clear the buffer; second call must not log.
        log_buffer.truncate(0)
        log_buffer.seek(0)

        changed = folder_reconcile(tmp_path)
        assert changed is False
        assert log_buffer.getvalue() == ""


# --------------------------------------------------------------------------- #
# Atomic-write hygiene
# --------------------------------------------------------------------------- #

class TestAtomicWriteHygiene:
    def test_no_tmp_file_remains(self, tmp_path: Path):
        # atomic_write leaves no ``<name>.tmp`` after a successful
        # write. A dangling .tmp would trip up the next reconcile
        # (manifest.parse would get malformed bytes from the .tmp if
        # anything enumerated it).
        _build_folder(tmp_path)
        folder_reconcile(tmp_path)
        assert not (tmp_path / f"{MANIFEST_NAME}.tmp").exists()
