"""Tests for ``create_jump`` service-layer behaviour (Phases 3.0, 3.3).

Contracts under test, directly mapped to D-entries:

  * D2/D18: ``jump.xml`` on disk parses through the hardened parser
    and validates against the v1 XSD.
  * D4: folder name is ``[<jump#>] <title>`` (or bare ``[<jump#>]``
    when title is absent). Unicode titles round-trip; forbidden
    characters in title raise ``ValidationFailedError``.
  * D5/D10: ``jump.xml`` and ``SHA256SUMS`` are both present on disk
    via atomic writes. ``SHA256SUMS`` matches what
    ``from_jump_xml`` would produce (recovery-path shape — D25).
  * D21/D30: attachments arrive as ``Upload`` objects, stream through
    ``atomic_write_stream``, and land with ``<sha256>`` computed by
    the server during the write. No claimed hash from the client.
  * D23: duplicate ``(user_id, jump_number)`` raises
    ``JumpNumberConflict`` with ``code == "jump_number_conflict"``,
    caught by all three of the prefix-scan + mkdir + SQLite UNIQUE
    layers depending on which fires first.
  * D25 step 2: attachments are written BEFORE jump.xml so the
    ``<attachment>/<sha256>`` elements in jump.xml record the bytes
    that actually reached disk — "agreement by construction".
  * D26: index row appears after the write with the current
    ``INDEX_SCHEMA_VERSION`` already stamped (open_index idempotent).
  * D27: the service function emits a ``jump_created`` INFO record
    on success (with ``attachment_count``); on the D23 collision
    caught by SQLite (index drift), emits
    ``create_jump_index_conflict`` WARNING.

Out of scope for this slice (and tested in future phases):
  * REST endpoint multipart wiring (Phase 3.3 tests live in
    ``test_rest_jumps.py``; these tests drive the service directly).
  * Crash-path subprocess harness (Phase 3.4, per D25).
  * ``update_jump`` / ``delete_jump`` (Phase 3.5).
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date
from pathlib import Path

import pytest

from backend.api.errors import JumpNumberConflict, ValidationFailedError
from backend.models.jump import JumpCreate
from backend.services.jump_service import JUMP_XML_NAME, Upload, create_jump
from backend.storage.bootstrap import bootstrap_logbook
from backend.storage.index import INDEX_FILENAME, open_index
from backend.storage.manifest import MANIFEST_NAME, from_jump_xml
from backend.xml.validator import XSDValidationError, parse, validate

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def bootstrapped_root(tmp_path: Path) -> Path:
    """A freshly bootstrapped logbook root ready for service calls."""
    root = tmp_path / "logbook"
    bootstrap_logbook(root)
    # Prime the index so the open_index inside create_jump sees the
    # current schema on first service call. Without this, the first
    # call would still work (open_index does the fresh-DB branch) but
    # the test's later assertions about schema_was_rebuilt=False would
    # need to account for the initial True.
    result = open_index(root)
    result.conn.close()
    return root


def _minimal_payload(**overrides) -> JumpCreate:
    data = dict(
        jump_number=1,
        date=date(2026, 4, 22),
        dropzone="Skydive Elsinore",
        exit_altitude_m=4000,
        deployment_altitude_m=900,
    )
    data.update(overrides)
    return JumpCreate(**data)


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #

class TestHappyPath:
    def test_returns_jump_with_fresh_uuid(self, bootstrapped_root: Path):
        # create_jump mints a UUID and returns it on the model. The id
        # is the stable internal reference (D4) and must be populated
        # even though JumpCreate doesn't carry one.
        jump = create_jump(bootstrapped_root, "default", _minimal_payload())
        assert jump.id is not None
        assert jump.jump_number == 1
        assert jump.dropzone == "Skydive Elsinore"

    def test_two_jumps_get_different_uuids(self, bootstrapped_root: Path):
        j1 = create_jump(bootstrapped_root, "default", _minimal_payload(jump_number=1))
        j2 = create_jump(bootstrapped_root, "default", _minimal_payload(jump_number=2))
        assert j1.id != j2.id

    def test_folder_name_without_title_is_bare_prefix(
        self, bootstrapped_root: Path
    ):
        create_jump(bootstrapped_root, "default", _minimal_payload(jump_number=42))
        assert (bootstrapped_root / "jumps" / "[42]").is_dir()

    def test_folder_name_with_title(self, bootstrapped_root: Path):
        create_jump(
            bootstrapped_root,
            "default",
            _minimal_payload(jump_number=851, title="First 4-way"),
        )
        assert (bootstrapped_root / "jumps" / "[851] First 4-way").is_dir()

    def test_folder_name_with_unicode_title(self, bootstrapped_root: Path):
        # D4 revised 2026-04-23: Unicode permitted in folder names.
        create_jump(
            bootstrapped_root,
            "default",
            _minimal_payload(jump_number=1, title="Première chute 🪂"),
        )
        assert (bootstrapped_root / "jumps" / "[1] Première chute 🪂").is_dir()

    def test_folder_name_normalises_nfd_title_to_nfc(
        self, bootstrapped_root: Path
    ):
        """TEST-5 — D4 NFC round-trip pin (audit 2026-04-29).

        ``unicodedata.normalize("NFC", ...)`` runs on every folder
        name (``filesystem.normalize_nfc``). A title submitted as NFD
        ("Cafe\\u0301", 5 codepoints, ``e`` + COMBINING ACUTE) lands
        on disk as NFC ("Caf\\u00e9", 4 codepoints, single ``é``).

        Why pin this:
        - macOS HFS+ stored NFD; APFS preserves what you write.
          Without NFC normalisation the same logical title produces
          different folder names per OS, breaking the deterministic
          SHA256 manifest and round-trip across cloud sync.
        - Removing the normalisation would silently drift between
          platforms; this test catches that regression at the
          observable boundary (the directory entry).
        """
        import unicodedata

        nfd_title = "Café"  # 5 codepoints (e + combining acute)
        nfc_title = "Café"  # 4 codepoints (precomposed é)
        # Sanity preconditions on the test inputs themselves so a
        # future Python upgrade that changes Unicode behaviour fails
        # here loudly rather than silently passing/failing the
        # invariant.
        assert unicodedata.normalize("NFD", nfc_title) == nfd_title
        assert unicodedata.normalize("NFC", nfd_title) == nfc_title
        assert nfd_title != nfc_title  # different byte sequences

        create_jump(
            bootstrapped_root,
            "default",
            _minimal_payload(jump_number=4242, title=nfd_title),
        )

        jumps_dir = bootstrapped_root / "jumps"
        # Read the actual on-disk directory entry — bytes, not the
        # logical lookup. ``Path.iterdir`` yields the names as the
        # filesystem stored them.
        names = [p.name for p in jumps_dir.iterdir()]
        # Exactly one jump folder.
        jump_folders = [n for n in names if n.startswith("[4242]")]
        assert len(jump_folders) == 1, names
        on_disk = jump_folders[0]

        # The folder name's title segment matches the NFC form
        # exactly, byte-for-byte.
        assert on_disk == f"[4242] {nfc_title}"
        # Codepoint-level comparison — the NFD form must NOT appear.
        assert nfd_title not in on_disk
        # And the canonical NFC representation is what we got.
        assert unicodedata.normalize("NFC", on_disk) == on_disk

    def test_jump_xml_on_disk_parses_and_validates(
        self, bootstrapped_root: Path
    ):
        # D2 invariant: on-disk XML must be parseable through the
        # hardened parser and XSD-valid. Any write that produces
        # disk state that verify would flag is a bug.
        create_jump(
            bootstrapped_root,
            "default",
            _minimal_payload(jump_number=1, title="Glacier"),
        )
        xml_path = bootstrapped_root / "jumps" / "[1] Glacier" / JUMP_XML_NAME
        element = parse(xml_path.read_bytes())
        validate(element)  # raises on failure

    def test_manifest_matches_from_jump_xml(self, bootstrapped_root: Path):
        # D25 §"Critical distinction": on the write path we must use
        # from_jump_xml to produce SHA256SUMS, so the just-written
        # manifest structurally equals what folder_reconcile would
        # compute on next open. Without this, reconcile would rewrite
        # on the very first read — a harmless but surprising side
        # effect.
        create_jump(bootstrapped_root, "default", _minimal_payload(jump_number=1))
        folder = bootstrapped_root / "jumps" / "[1]"
        on_disk = (folder / MANIFEST_NAME).read_bytes()
        recomputed = from_jump_xml(folder, logbook_root=bootstrapped_root)
        assert on_disk == recomputed

    def test_index_row_populated(self, bootstrapped_root: Path):
        jump = create_jump(
            bootstrapped_root,
            "default",
            _minimal_payload(jump_number=7, title="Sunset load"),
        )
        result = open_index(bootstrapped_root)
        try:
            row = result.conn.execute(
                "SELECT id, user_id, jump_number, date, dropzone, title, "
                "folder, schema_ns, created_at, updated_at "
                "FROM jumps WHERE id = ?",
                (str(jump.id),),
            ).fetchone()
        finally:
            result.conn.close()

        assert row is not None
        assert row["id"] == str(jump.id)
        assert row["user_id"] == "default"
        assert row["jump_number"] == 7
        assert row["date"] == "2026-04-22"
        assert row["dropzone"] == "Skydive Elsinore"
        # Phase 3.1: title denormalized into the index so list views
        # don't need a per-row XML read (D3 compatibility: title still
        # lives canonically in jump.xml, so reindex can rebuild it).
        assert row["title"] == "Sunset load"
        assert row["folder"] == "jumps/[7] Sunset load"
        assert row["schema_ns"] == "https://skydive-logbook.org/schema/v1"
        # Timestamps: ISO-8601 UTC with ms precision, 'Z' suffix (D17).
        # On insert, created_at == updated_at.
        assert row["created_at"].endswith("Z")
        assert row["created_at"] == row["updated_at"]

    def test_index_title_null_when_absent(self, bootstrapped_root: Path):
        # Optional title → NULL in the title column, not an empty
        # string. Preserves the D4 "absent ≠ empty" distinction in
        # both XML and index.
        jump = create_jump(
            bootstrapped_root, "default", _minimal_payload(jump_number=99)
        )
        result = open_index(bootstrapped_root)
        try:
            row = result.conn.execute(
                "SELECT title FROM jumps WHERE id = ?", (str(jump.id),)
            ).fetchone()
        finally:
            result.conn.close()
        assert row["title"] is None

    def test_emits_jump_created_log(self, bootstrapped_root: Path, caplog):
        caplog.set_level(logging.INFO, logger="backend.services.jump")
        jump = create_jump(
            bootstrapped_root,
            "default",
            _minimal_payload(jump_number=3, title="Solo"),
        )
        records = [r for r in caplog.records if r.message == "jump_created"]
        assert len(records) == 1
        record = records[0]
        assert record.levelname == "INFO"
        assert record.jump_id == str(jump.id)
        assert record.user_id == "default"
        assert record.jump_number == 3
        assert record.folder == "jumps/[3] Solo"


# --------------------------------------------------------------------------- #
# D23: duplicate jump_number collisions — three layers of defense
# --------------------------------------------------------------------------- #

class TestDuplicateJumpNumber:
    def test_duplicate_via_prefix_scan(self, bootstrapped_root: Path):
        # First jump lands. Second call with the same jump_number is
        # caught by the filesystem prefix scan before any write.
        create_jump(
            bootstrapped_root, "default", _minimal_payload(jump_number=42, title="A")
        )
        with pytest.raises(JumpNumberConflict) as exc_info:
            create_jump(
                bootstrapped_root,
                "default",
                _minimal_payload(jump_number=42, title="B"),
            )
        assert exc_info.value.code == "jump_number_conflict"
        assert exc_info.value.http_status == 409
        # The `errors` array points at #/jump_number (RFC 9457 §3).
        assert exc_info.value.errors is not None
        assert exc_info.value.errors[0].pointer == "#/jump_number"

    def test_duplicate_via_index_constraint(
        self, bootstrapped_root: Path, monkeypatch
    ):
        # Index-layer catch (D23 SQLite UNIQUE). Simulate a scenario
        # where the service's prefix scan passes but the index already
        # has a row for that (user_id, jump_number) — this models
        # index-filesystem divergence (e.g. the folder was manually
        # moved or deleted, leaving a stale index row).
        #
        # We stuff a row into the index directly, then call create_jump.
        result = open_index(bootstrapped_root)
        try:
            result.conn.execute(
                "INSERT INTO jumps (id, user_id, jump_number, date, dropzone, "
                "folder, schema_ns, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "11111111-1111-4111-8111-111111111111",
                    "default",
                    100,
                    "2026-01-01",
                    "DZ",
                    "jumps/stale-folder-path",
                    "https://skydive-logbook.org/schema/v1",
                    "2026-01-01T00:00:00.000Z",
                    "2026-01-01T00:00:00.000Z",
                ),
            )
        finally:
            result.conn.close()

        with pytest.raises(JumpNumberConflict):
            create_jump(
                bootstrapped_root,
                "default",
                _minimal_payload(jump_number=100, title="fresh"),
            )

    def test_v01_folder_space_is_shared_across_users(
        self, bootstrapped_root: Path
    ):
        # D23 UNIQUE is on (user_id, jump_number), but the filesystem
        # folder namespace under ``jumps/`` is not yet user-prefixed
        # (D8 keeps user_id as a service parameter; folder layout
        # will add a user prefix when multi-user actually lands). So
        # in v0.1, alice's jump_number=5 and bob's jump_number=5 DO
        # collide at the filesystem prefix-scan even though the SQL
        # UNIQUE would allow it.
        #
        # Pinning this as explicit behavior: when the multi-user slice
        # redesigns the folder layout, this test flips to "both
        # succeed" and the prefix scan gets user-scoped.
        create_jump(
            bootstrapped_root, "alice", _minimal_payload(jump_number=5, title="A")
        )
        with pytest.raises(JumpNumberConflict):
            create_jump(
                bootstrapped_root, "bob", _minimal_payload(jump_number=5, title="B")
            )


# --------------------------------------------------------------------------- #
# Payload validation
# --------------------------------------------------------------------------- #

class TestInvalidPayload:
    def test_forbidden_char_in_title_raises(self, bootstrapped_root: Path):
        # A '/' in the title would produce a folder name like
        # "[1] bad/title" — sanitize_folder_name rejects that. The
        # service translates the ValueError into ValidationFailedError.
        with pytest.raises(ValidationFailedError):
            create_jump(
                bootstrapped_root,
                "default",
                _minimal_payload(jump_number=1, title="bad/title"),
            )

    def test_oversize_title_raises_at_pydantic(self, bootstrapped_root: Path):
        # Jump.title has max_length=120 per D4. Pydantic catches it
        # first — before the service function even runs — because
        # JumpCreate is constructed by the test. Verify the error
        # type to lock in the 422 behavior end-to-end.
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            JumpCreate(
                jump_number=1,
                title="x" * 121,
                date=date(2026, 4, 22),
                dropzone="DZ",
                exit_altitude_m=4000,
                deployment_altitude_m=900,
            )


# --------------------------------------------------------------------------- #
# Post-condition side effects
# --------------------------------------------------------------------------- #

class TestSideEffects:
    def test_jumps_dir_created_if_missing(self, tmp_path: Path):
        # Bootstrap is normally called first, but create_jump must
        # tolerate a root where jumps/ hasn't been created yet (the
        # service-layer code mkdir's it). Otherwise any bootstrap
        # failure downstream of jumps/ creation leaves create_jump
        # fragile.
        root = tmp_path / "logbook"
        bootstrap_logbook(root)
        # Delete jumps/ to simulate a corrupt bootstrap state.
        (root / "jumps").rmdir()
        create_jump(root, "default", _minimal_payload(jump_number=1))
        assert (root / "jumps" / "[1]").is_dir()

    def test_no_tmp_files_leaked(self, bootstrapped_root: Path):
        # atomic_write leaves no .tmp file on successful writes. A
        # dangling .tmp inside a jump folder would be reported as an
        # orphan by verify — we never want that in the create path.
        create_jump(bootstrapped_root, "default", _minimal_payload(jump_number=1))
        folder = bootstrapped_root / "jumps" / "[1]"
        tmps = [p for p in folder.rglob("*.tmp")]
        assert tmps == []

    def test_index_file_exists_after_call(self, bootstrapped_root: Path):
        # open_index inside create_jump should produce the index file
        # at the canonical path (belt-and-braces: bootstrap doesn't
        # create it).
        create_jump(bootstrapped_root, "default", _minimal_payload(jump_number=1))
        assert (bootstrapped_root / INDEX_FILENAME).is_file()


# --------------------------------------------------------------------------- #
# End-to-end composability check (verify runs clean on a created jump)
# --------------------------------------------------------------------------- #

class TestComposabilityWithVerify:
    def test_verify_reports_clean_after_create(self, bootstrapped_root: Path):
        # A jump created by the service must pass every verify check:
        # XSD-valid jump.xml, no attachments to mismatch, SHA256SUMS
        # matches from_jump_xml (so not flagged as stale), no orphan
        # files, no duplicate jump_numbers. This is the key composability
        # check that says "Phase 3.0 plays nicely with every storage
        # primitive we've already built."
        from backend.storage.verify import verify_logbook

        create_jump(
            bootstrapped_root,
            "default",
            _minimal_payload(jump_number=1, title="Clean"),
        )
        report = verify_logbook(bootstrapped_root)
        assert report.clean, f"expected clean, got issues: {report.issues}"
        assert report.folders_scanned == 1

    def test_sha256sums_hashes_jump_xml_correctly(self, bootstrapped_root: Path):
        # Spot-check the manifest's jump.xml line matches a fresh hash
        # of the on-disk bytes. If the two diverge, from_jump_xml has a
        # bug OR create_jump wrote the manifest from pre-write bytes.
        create_jump(bootstrapped_root, "default", _minimal_payload(jump_number=1))
        folder = bootstrapped_root / "jumps" / "[1]"
        jump_bytes = (folder / JUMP_XML_NAME).read_bytes()
        expected_hash = hashlib.sha256(jump_bytes).hexdigest()
        manifest_lines = (folder / MANIFEST_NAME).read_text().splitlines()
        xml_lines = [ln for ln in manifest_lines if ln.endswith("  jump.xml")]
        assert len(xml_lines) == 1
        assert xml_lines[0].startswith(expected_hash)


# --------------------------------------------------------------------------- #
# Phase 3.3 — attachments (D21, D25 step 2, D30)
# --------------------------------------------------------------------------- #

def _up(filename: str, data: bytes, content_type: str | None = None) -> Upload:
    """Build an Upload from a bytes blob. Most tests want this shape."""
    return Upload(
        filename=filename,
        content_type=content_type,
        chunks=[data],  # single-chunk iterable; atomic_write_stream handles any shape
    )


class TestAttachmentsHappyPath:
    def test_no_uploads_produces_empty_attachments(self, bootstrapped_root: Path):
        # Backward-compat: absent uploads or ``uploads=None`` both mean
        # "create jump with no attachments". The existing 3.0/3.1 tests
        # use the positional signature; this one locks down the
        # keyword-with-None case too.
        jump = create_jump(
            bootstrapped_root, "default", _minimal_payload(jump_number=1), uploads=None
        )
        assert jump.attachments == []

    def test_empty_uploads_list_produces_empty_attachments(
        self, bootstrapped_root: Path
    ):
        jump = create_jump(
            bootstrapped_root, "default", _minimal_payload(jump_number=1), uploads=[]
        )
        assert jump.attachments == []

    def test_single_attachment_lands_on_disk(self, bootstrapped_root: Path):
        data = b"lat,lon,alt\n34.1,-117.2,4000\n"
        create_jump(
            bootstrapped_root,
            "default",
            _minimal_payload(jump_number=1, title="FS"),
            uploads=[_up("track.csv", data, content_type="text/csv")],
        )
        att_path = bootstrapped_root / "jumps" / "[1] FS" / "track.csv"
        assert att_path.read_bytes() == data

    def test_attachment_sha256_matches_bytes(self, bootstrapped_root: Path):
        # D25 §"agreement by construction": the <sha256> we record in
        # jump.xml must be the hash of the bytes that landed on disk.
        data = b"x" * 1024
        jump = create_jump(
            bootstrapped_root,
            "default",
            _minimal_payload(jump_number=1),
            uploads=[_up("blob.bin", data)],
        )
        assert len(jump.attachments) == 1
        att = jump.attachments[0]
        assert att.filename == "blob.bin"
        assert att.sha256 == hashlib.sha256(data).hexdigest()
        assert att.size == 1024

    def test_attachment_content_type_passes_through(self, bootstrapped_root: Path):
        jump = create_jump(
            bootstrapped_root,
            "default",
            _minimal_payload(jump_number=1),
            uploads=[_up("clip.mp4", b"mp4-bytes", content_type="video/mp4")],
        )
        assert jump.attachments[0].content_type == "video/mp4"

    def test_attachment_content_type_optional(self, bootstrapped_root: Path):
        # A client that doesn't know the mime type (rare, but
        # possible) sends None; the field is optional per the
        # Attachment model.
        jump = create_jump(
            bootstrapped_root,
            "default",
            _minimal_payload(jump_number=1),
            uploads=[_up("mystery.dat", b"x")],
        )
        assert jump.attachments[0].content_type is None

    def test_multiple_attachments(self, bootstrapped_root: Path):
        uploads = [
            _up("a.csv", b"alpha"),
            _up("b.csv", b"bravo"),
            _up("c.csv", b"charlie"),
        ]
        jump = create_jump(
            bootstrapped_root,
            "default",
            _minimal_payload(jump_number=1, title="Multi"),
            uploads=uploads,
        )
        folder = bootstrapped_root / "jumps" / "[1] Multi"
        assert (folder / "a.csv").read_bytes() == b"alpha"
        assert (folder / "b.csv").read_bytes() == b"bravo"
        assert (folder / "c.csv").read_bytes() == b"charlie"
        assert [a.filename for a in jump.attachments] == ["a.csv", "b.csv", "c.csv"]
        # Each hash matches its own bytes.
        assert jump.attachments[0].sha256 == hashlib.sha256(b"alpha").hexdigest()
        assert jump.attachments[1].sha256 == hashlib.sha256(b"bravo").hexdigest()
        assert jump.attachments[2].sha256 == hashlib.sha256(b"charlie").hexdigest()

    def test_chunks_can_be_generator(self, bootstrapped_root: Path):
        # Real multipart uploads arrive as iterators, not lists. A
        # streaming consumer (D21) must not assume re-iterability.
        def produce():
            yield b"part-1"
            yield b"part-2"
            yield b"part-3"

        jump = create_jump(
            bootstrapped_root,
            "default",
            _minimal_payload(jump_number=1),
            uploads=[Upload(filename="stream.bin", content_type=None, chunks=produce())],
        )
        folder = bootstrapped_root / "jumps" / "[1]"
        assert (folder / "stream.bin").read_bytes() == b"part-1part-2part-3"
        assert jump.attachments[0].sha256 == hashlib.sha256(
            b"part-1part-2part-3"
        ).hexdigest()

    def test_large_streaming_attachment(self, bootstrapped_root: Path):
        # 2 MiB through 64 KiB chunks. Exercises the streaming path
        # on a file larger than a single hash update; would fail
        # loudly if the implementation buffered everything in
        # memory (it wouldn't explode but slow the test) or
        # mis-counted chunks (hash/size mismatch).
        chunk = b"y" * 65536
        n_chunks = 32
        uploads = [
            Upload(
                filename="big.bin",
                content_type="application/octet-stream",
                chunks=(chunk for _ in range(n_chunks)),
            )
        ]
        jump = create_jump(
            bootstrapped_root,
            "default",
            _minimal_payload(jump_number=1),
            uploads=uploads,
        )
        att = jump.attachments[0]
        assert att.size == n_chunks * len(chunk)
        assert att.sha256 == hashlib.sha256(chunk * n_chunks).hexdigest()

    def test_empty_attachment_is_valid(self, bootstrapped_root: Path):
        # 0-byte attachments are legal per D21 (no size floor). SHA of
        # the empty string is the conventional digest.
        jump = create_jump(
            bootstrapped_root,
            "default",
            _minimal_payload(jump_number=1),
            uploads=[_up("empty.txt", b"")],
        )
        assert jump.attachments[0].size == 0
        assert jump.attachments[0].sha256 == hashlib.sha256(b"").hexdigest()

    def test_attachment_recorded_in_jump_xml(self, bootstrapped_root: Path):
        # Parse the written jump.xml and assert the <attachment>
        # element carries the same sha256 the service returned. Any
        # drift would indicate jump.xml was serialized before the
        # attachment's hash was known — breaking D25 step 2.
        data = b"hello"
        create_jump(
            bootstrapped_root,
            "default",
            _minimal_payload(jump_number=1),
            uploads=[_up("hi.txt", data, content_type="text/plain")],
        )
        xml_path = bootstrapped_root / "jumps" / "[1]" / JUMP_XML_NAME
        element = parse(xml_path.read_bytes())
        validate(element)
        ns = "{https://skydive-logbook.org/schema/v1}"
        atts = element.find(f"{ns}attachments")
        assert atts is not None
        att_elements = atts.findall(f"{ns}attachment")
        assert len(att_elements) == 1
        att_el = att_elements[0]
        assert att_el.find(f"{ns}filename").text == "hi.txt"
        assert att_el.find(f"{ns}sha256").text == hashlib.sha256(data).hexdigest()
        assert att_el.find(f"{ns}size").text == "5"
        assert att_el.find(f"{ns}content_type").text == "text/plain"

    def test_manifest_covers_attachments(self, bootstrapped_root: Path):
        # SHA256SUMS must list every attachment in the folder. Without
        # this, verify would report "stale manifest" on the very first
        # read and reconcile would rewrite the file — harmless but a
        # sign of write-path drift from D25.
        create_jump(
            bootstrapped_root,
            "default",
            _minimal_payload(jump_number=1),
            uploads=[_up("a.txt", b"A"), _up("b.txt", b"B")],
        )
        folder = bootstrapped_root / "jumps" / "[1]"
        on_disk = (folder / MANIFEST_NAME).read_bytes()
        # Byte-identical to what from_jump_xml would produce; if we
        # didn't call from_jump_xml on write, this would fail with a
        # different hash-to-filename pairing.
        assert on_disk == from_jump_xml(folder, logbook_root=bootstrapped_root)
        # Belt-and-braces: check both filenames appear.
        assert b"  a.txt\n" in on_disk
        assert b"  b.txt\n" in on_disk
        assert b"  jump.xml\n" in on_disk

    def test_filename_nfc_normalized_on_disk(self, bootstrapped_root: Path):
        # NFD "é" (e + combining acute) normalizes to NFC "é" before
        # it becomes the on-disk filename. Same rule as folder names —
        # keeps cross-OS sync deterministic.
        nfd_name = "caf" + "e\u0301" + ".txt"
        nfc_name = "caf\u00e9.txt"
        jump = create_jump(
            bootstrapped_root,
            "default",
            _minimal_payload(jump_number=1),
            uploads=[_up(nfd_name, b"x")],
        )
        assert jump.attachments[0].filename == nfc_name
        assert (bootstrapped_root / "jumps" / "[1]" / nfc_name).is_file()

    def test_attachment_count_in_log(self, bootstrapped_root: Path, caplog):
        caplog.set_level(logging.INFO, logger="backend.services.jump")
        create_jump(
            bootstrapped_root,
            "default",
            _minimal_payload(jump_number=1),
            uploads=[_up("a.txt", b"A"), _up("b.txt", b"B")],
        )
        records = [r for r in caplog.records if r.message == "jump_created"]
        assert len(records) == 1
        assert records[0].attachment_count == 2

    def test_verify_clean_after_create_with_attachments(
        self, bootstrapped_root: Path
    ):
        # End-to-end composability: a jump created with attachments
        # must pass verify. Hashes in jump.xml match on-disk bytes,
        # manifest matches from_jump_xml, no orphans, no duplicates.
        from backend.storage.verify import verify_logbook

        create_jump(
            bootstrapped_root,
            "default",
            _minimal_payload(jump_number=1, title="V"),
            uploads=[
                _up("track.csv", b"lat,lon\n1,2\n"),
                _up("photo.jpg", b"\xff\xd8\xff\xe0fake-jpeg"),
            ],
        )
        report = verify_logbook(bootstrapped_root)
        assert report.clean, f"expected clean, got: {report.issues}"


class TestAttachmentsRejectedPayloads:
    def test_forbidden_char_in_filename_raises(self, bootstrapped_root: Path):
        with pytest.raises(ValidationFailedError) as exc_info:
            create_jump(
                bootstrapped_root,
                "default",
                _minimal_payload(jump_number=1),
                uploads=[_up("bad/name.txt", b"x")],
            )
        # Pointer locates the offending upload by index.
        assert exc_info.value.errors is not None
        assert exc_info.value.errors[0].pointer == "#/files/0/filename"

    def test_windows_reserved_filename_raises(self, bootstrapped_root: Path):
        with pytest.raises(ValidationFailedError):
            create_jump(
                bootstrapped_root,
                "default",
                _minimal_payload(jump_number=1),
                uploads=[_up("NUL.txt", b"x")],
            )

    def test_duplicate_filename_raises(self, bootstrapped_root: Path):
        # Two uploads producing the same canonical filename collide on
        # disk; we reject before any write so the first one isn't
        # silently overwritten.
        with pytest.raises(ValidationFailedError) as exc_info:
            create_jump(
                bootstrapped_root,
                "default",
                _minimal_payload(jump_number=1),
                uploads=[
                    _up("dup.txt", b"first"),
                    _up("dup.txt", b"second"),
                ],
            )
        errors = exc_info.value.errors or []
        # At least one error points at the duplicate (index 1 — the
        # second occurrence is the "extra" one).
        assert any(e.pointer == "#/files/1/filename" for e in errors)

    def test_bad_filename_leaves_no_folder(self, bootstrapped_root: Path):
        # 422 on filename validation must not leave a half-made folder
        # behind. Checked because the filename sanitization now runs
        # BEFORE mkdir — a regression that moved it after mkdir would
        # trip this test.
        with pytest.raises(ValidationFailedError):
            create_jump(
                bootstrapped_root,
                "default",
                _minimal_payload(jump_number=42),
                uploads=[_up("bad/name.txt", b"x")],
            )
        assert not (bootstrapped_root / "jumps" / "[42]").exists()

    def test_multiple_filename_errors_all_reported(self, bootstrapped_root: Path):
        # The RFC 9457 ``errors`` array lists EVERY offending file,
        # not just the first — clients can correct them in one pass.
        with pytest.raises(ValidationFailedError) as exc_info:
            create_jump(
                bootstrapped_root,
                "default",
                _minimal_payload(jump_number=1),
                uploads=[
                    _up("ok.txt", b"good"),
                    _up("bad/1.txt", b"bad1"),
                    _up("NUL", b"bad2"),
                ],
            )
        errors = exc_info.value.errors or []
        pointers = {e.pointer for e in errors}
        assert "#/files/1/filename" in pointers
        assert "#/files/2/filename" in pointers


# --------------------------------------------------------------------------- #
# TEST-4: XSD-rejection write-path pinning (audit 2026-04-29)
# --------------------------------------------------------------------------- #

class TestXsdRejectionBlocksWrite:
    """Pins CLAUDE.md §5 invariant 2: every XML produced is XSD-validated
    BEFORE the atomic write.

    The defense-in-depth: if a future bug in ``jump_to_element`` emitted
    XML missing a required element, the ``validate(element)`` call must
    fire and stop the write. ``jump.xml`` must NOT land on disk; the
    SQLite index row must NOT be inserted.

    The audit (2026-04-29 TEST-4) framed this as "monkey-patch the
    Pydantic model"; the actual seam is one level lower. Pydantic's
    ``Jump`` enforces field presence at construction, so a Pydantic-
    valid ``Jump`` always produces XSD-valid XML *unless* ``jump_to_element``
    itself drops a required element. The test patches the serialiser
    output to model that exact failure mode.

    Note: per ``create_jump``'s docstring, the folder is ``mkdir``'d at
    D25 step 1 BEFORE serialise+validate (step 3). On XSD failure the
    folder will exist (empty); the invariant is "no jump.xml landed",
    not "no folder created" — the audit's wording was imprecise. The
    "incomplete folder" state is exactly the D25 crash-state row that
    ``folder_reconcile`` and ``verify`` already handle.
    """

    def test_xsd_rejection_blocks_jump_xml_write(
        self,
        bootstrapped_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from lxml import etree

        from backend.services import jump_service
        from backend.xml import serialize as serialize_module

        original = jump_service.jump_to_element

        def broken_jump_to_element(jump):
            """Drop the required ``<date>`` element from the produced XML.

            The XSD declares ``<date>`` as a required child; removing it
            should cause ``validate(element)`` to raise
            ``XSDValidationError``.
            """
            element = original(jump)
            ns = etree.QName(element).namespace
            date_el = element.find(f"{{{ns}}}date")
            assert date_el is not None, (
                "fixture invariant: a Pydantic-valid Jump always emits <date>"
            )
            element.remove(date_el)
            return element

        monkeypatch.setattr(
            jump_service, "jump_to_element", broken_jump_to_element
        )
        # Belt-and-braces: also patch the original site so any other
        # caller in the create path sees the broken serialiser.
        monkeypatch.setattr(
            serialize_module, "jump_to_element", broken_jump_to_element
        )

        with pytest.raises(XSDValidationError):
            create_jump(
                bootstrapped_root,
                "default",
                _minimal_payload(jump_number=99),
            )

        # Folder exists from D25 step 1 mkdir, but is empty — the
        # invariant we pin is "no authoritative bytes on disk before
        # XSD validation passes".
        jump_folder = bootstrapped_root / "jumps" / "[99]"
        assert not (jump_folder / JUMP_XML_NAME).exists(), (
            "jump.xml must not land on disk if XSD validation fails"
        )
        assert not (jump_folder / MANIFEST_NAME).exists(), (
            "SHA256SUMS must not land on disk if XSD validation fails"
        )

        # SQLite row must not exist either (index write is gated on
        # XML write per D3).
        result = open_index(bootstrapped_root)
        try:
            row = result.conn.execute(
                "SELECT id FROM jumps WHERE jump_number = ?", (99,)
            ).fetchone()
        finally:
            result.conn.close()
        assert row is None, "SQLite row must not be inserted if XSD failed"

    def test_xsd_passes_on_valid_jump_baseline(
        self, bootstrapped_root: Path
    ) -> None:
        """Sanity baseline: the unmonkey-patched code path produces
        XSD-valid XML and the jump lands on disk. Without this check
        the previous test could pass spuriously if the create flow
        were broken end-to-end.
        """
        result = create_jump(
            bootstrapped_root, "default", _minimal_payload(jump_number=100)
        )
        jump_xml = bootstrapped_root / "jumps" / "[100]" / JUMP_XML_NAME
        assert jump_xml.exists()
        # Re-validate from disk to prove the schema is healthy.
        element = parse(jump_xml.read_bytes())
        validate(element)
        assert result.jump_number == 100
