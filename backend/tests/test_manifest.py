"""Tests for the SHA256SUMS manifest (D5, D25)."""
from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

from backend.models.jump import Attachment, Jump
from backend.storage.manifest import (
    JUMP_XML_NAME,
    MANIFEST_NAME,
    from_jump_xml,
    generate,
    parse,
    sha256_bytes,
    sha256_file,
    verify,
)
from backend.xml.serialize import jump_to_bytes
from backend.xml.validator import XMLMalformed, XSDValidationError


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


class TestSha256File:
    def test_matches_hashlib(self, tmp_path: Path):
        p = tmp_path / "f.bin"
        p.write_bytes(b"hello world")
        assert sha256_file(p) == hashlib.sha256(b"hello world").hexdigest()


class TestGenerate:
    def test_includes_all_files_sorted(self, tmp_path: Path):
        _write(tmp_path / "b.bin", b"B")
        _write(tmp_path / "a.bin", b"A")
        _write(tmp_path / "sub" / "c.bin", b"C")

        out = generate(tmp_path).decode()
        lines = out.strip().split("\n")
        # GNU format: <hash>  <relpath>
        rel_paths = [line.split("  ", 1)[1] for line in lines]
        assert rel_paths == ["a.bin", "b.bin", "sub/c.bin"]

    def test_excludes_manifest_itself(self, tmp_path: Path):
        _write(tmp_path / "a.bin", b"A")
        _write(tmp_path / MANIFEST_NAME, b"stale")
        out = generate(tmp_path).decode()
        assert MANIFEST_NAME not in out

    def test_excludes_summary_md(self, tmp_path: Path):
        """summary.md is derived (D5) and must not appear in the manifest."""
        _write(tmp_path / "jump.xml", b"<jump/>")
        _write(tmp_path / "summary.md", b"# Jump 851")
        out = generate(tmp_path).decode()
        assert "summary.md" not in out
        assert "jump.xml" in out


class TestParse:
    def test_roundtrip(self, tmp_path: Path):
        _write(tmp_path / "a.bin", b"A")
        _write(tmp_path / "b.bin", b"B")
        manifest = generate(tmp_path)
        entries = parse(manifest)
        assert {rel for _, rel in entries} == {"a.bin", "b.bin"}

    def test_rejects_malformed(self):
        with pytest.raises(ValueError):
            parse(b"not a manifest\n")

    def test_rejects_bad_digest(self):
        with pytest.raises(ValueError):
            parse(b"zz  file\n")


class TestVerify:
    def test_clean_folder_returns_empty(self, tmp_path: Path):
        _write(tmp_path / "a.bin", b"A")
        _write(tmp_path / "b.bin", b"B")
        (tmp_path / MANIFEST_NAME).write_bytes(generate(tmp_path))
        assert verify(tmp_path) == []

    def test_detects_modified_file(self, tmp_path: Path):
        _write(tmp_path / "a.bin", b"A")
        (tmp_path / MANIFEST_NAME).write_bytes(generate(tmp_path))
        (tmp_path / "a.bin").write_bytes(b"tampered")
        problems = verify(tmp_path)
        assert len(problems) == 1
        assert problems[0][0] == "a.bin"
        assert "hash mismatch" in problems[0][1]

    def test_detects_missing_file(self, tmp_path: Path):
        _write(tmp_path / "a.bin", b"A")
        (tmp_path / MANIFEST_NAME).write_bytes(generate(tmp_path))
        (tmp_path / "a.bin").unlink()
        problems = verify(tmp_path)
        assert problems == [("a.bin", "missing")]

    def test_detects_missing_manifest(self, tmp_path: Path):
        _write(tmp_path / "a.bin", b"A")
        assert verify(tmp_path) == [(MANIFEST_NAME, "missing")]


# --------------------------------------------------------------------------- #
# D25: from_jump_xml — recovery-path manifest source
# --------------------------------------------------------------------------- #

def _make_jump_folder(
    folder: Path,
    *,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> Jump:
    """Build a jump folder with jump.xml and optional attachments.

    ``attachments`` is a list of ``(filename, bytes_on_disk, claimed_sha256)``
    tuples. The claimed hash goes into jump.xml; the bytes go to disk.
    Tests use this to create "on-disk matches claim" and "on-disk
    diverges from claim" scenarios in a single helper.
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
    return jump


class TestFromJumpXml:
    def test_matches_generate_when_bytes_agree(self, tmp_path: Path):
        # Happy path: disk bytes and jump.xml claims agree. In that
        # case from_jump_xml and generate must produce byte-identical
        # output — the two sources are consistent.
        att_data = b"flysight timing..."
        att_hash = hashlib.sha256(att_data).hexdigest()
        _make_jump_folder(
            tmp_path,
            attachments=[("flysight.csv", att_data, att_hash)],
        )
        assert from_jump_xml(tmp_path) == generate(tmp_path)

    def test_uses_claimed_hashes_not_disk_bytes(self, tmp_path: Path):
        # Core D25 safety property: if attachment bytes on disk have
        # rotted, from_jump_xml still emits the original claimed hash
        # — preserving jump.xml as the authoritative witness. generate
        # would silently bless the corruption.
        correct_data = b"original flysight"
        correct_hash = hashlib.sha256(correct_data).hexdigest()
        _make_jump_folder(
            tmp_path,
            attachments=[("flysight.csv", correct_data, correct_hash)],
        )
        # Silently rot the disk bytes.
        (tmp_path / "flysight.csv").write_bytes(b"ROTTED")

        claim_based = from_jump_xml(tmp_path)
        disk_based = generate(tmp_path)

        # Manifests diverge: claim-based points at the original hash;
        # disk-based would sign off on the rotted bytes.
        assert claim_based != disk_based
        # The claim-based output still carries the *original* hash.
        assert correct_hash.encode() in claim_based

    def test_includes_jump_xml_hash(self, tmp_path: Path):
        # D25: "plus a freshly-computed hash of jump.xml itself".
        _make_jump_folder(tmp_path)
        out = from_jump_xml(tmp_path).decode()
        assert JUMP_XML_NAME in out
        # The listed hash matches what sha256_bytes produces on the
        # same file we just wrote.
        jump_bytes = (tmp_path / JUMP_XML_NAME).read_bytes()
        expected_hash = sha256_bytes(jump_bytes)
        assert f"{expected_hash}  {JUMP_XML_NAME}" in out

    def test_paths_sorted_alphabetically(self, tmp_path: Path):
        # Matches generate()'s output shape so a downstream tool can
        # diff two manifests without worrying about ordering.
        _make_jump_folder(
            tmp_path,
            attachments=[
                ("flysight.csv", b"1", hashlib.sha256(b"1").hexdigest()),
                ("video.mp4", b"2", hashlib.sha256(b"2").hexdigest()),
                ("audio.m4a", b"3", hashlib.sha256(b"3").hexdigest()),
            ],
        )
        out = from_jump_xml(tmp_path).decode()
        rel_paths = [line.split("  ", 1)[1] for line in out.strip().split("\n")]
        # Alphabetical, with jump.xml at its natural position.
        assert rel_paths == sorted(rel_paths)
        assert rel_paths == ["audio.m4a", "flysight.csv", "jump.xml", "video.mp4"]

    def test_line_count_equals_attachments_plus_one(self, tmp_path: Path):
        _make_jump_folder(
            tmp_path,
            attachments=[
                ("flysight.csv", b"1", hashlib.sha256(b"1").hexdigest()),
                ("video.mp4", b"2", hashlib.sha256(b"2").hexdigest()),
            ],
        )
        out = from_jump_xml(tmp_path).decode()
        lines = [ln for ln in out.strip().split("\n") if ln]
        # 2 attachments + jump.xml itself.
        assert len(lines) == 3

    def test_zero_attachments(self, tmp_path: Path):
        # A legitimate minimal jump has no attachments; the manifest is
        # still a valid single-line (jump.xml only) document.
        _make_jump_folder(tmp_path)
        out = from_jump_xml(tmp_path).decode()
        lines = [ln for ln in out.strip().split("\n") if ln]
        assert len(lines) == 1
        assert lines[0].endswith(f"  {JUMP_XML_NAME}")

    def test_missing_jump_xml_raises(self, tmp_path: Path):
        # Per D25: folder without jump.xml is "not a valid jump" and is
        # out of reconcile / from_jump_xml's scope.
        with pytest.raises(FileNotFoundError):
            from_jump_xml(tmp_path)

    def test_malformed_xml_raises(self, tmp_path: Path):
        (tmp_path / JUMP_XML_NAME).write_bytes(b"<not-xml<")
        with pytest.raises(XMLMalformed):
            from_jump_xml(tmp_path)

    def test_xsd_invalid_xml_raises(self, tmp_path: Path):
        # Syntactically valid XML, semantically wrong shape — missing
        # required elements per the XSD. Must raise rather than emit a
        # manifest that tacitly blesses the broken document.
        (tmp_path / JUMP_XML_NAME).write_bytes(
            b'<?xml version="1.0"?>'
            b'<jump xmlns="https://skydive-logbook.org/schema/v1"/>'
        )
        with pytest.raises(XSDValidationError):
            from_jump_xml(tmp_path)

    def test_output_parses_as_a_valid_manifest(self, tmp_path: Path):
        # The bytes we emit must round-trip through manifest.parse —
        # the whole point is that SHA256SUMS consumers can read them.
        att_data = b"x"
        _make_jump_folder(
            tmp_path,
            attachments=[("a.bin", att_data, hashlib.sha256(att_data).hexdigest())],
        )
        out = from_jump_xml(tmp_path)
        entries = parse(out)
        assert len(entries) == 2
        rel_paths = {rel for _, rel in entries}
        assert rel_paths == {JUMP_XML_NAME, "a.bin"}
