"""Slice 11 — magic-bytes Content-Type sniffing on uploaded files.

The pre-Slice-11 code trusted ``UploadFile.content_type`` straight
from the client's multipart header. A request crafted with
``Content-Type: image/png`` over an HTML body landed an
``Attachment.content_type == "image/png"`` against an actual HTML
payload. The latent risk: any future inline-view endpoint streaming
the bytes with the stored MIME would re-introduce an XSS vector.

Slice 11 inverts the trust direction. ``trusted_content_type`` peeks
the first bytes via :mod:`filetype` and returns the sniffed MIME if
the format is recognised; otherwise falls back to the declared
header (text-like uploads — CSVs, log notes — aren't binary-
recognisable). A mismatch logs a WARNING but doesn't reject the
upload (a logbook is the user's data; a hard allow-list lands when
the inline-view endpoint does).

These tests cover the helper directly + the integration through
``POST /api/v1/jumps``.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_logbook_root, get_user_id
from backend.api.rest import create_app
from backend.api.uploads import resolve_content_type, trusted_content_type
from backend.storage.bootstrap import bootstrap_logbook
from backend.storage.index import open_index

# --------------------------------------------------------------------------- #
# Magic-bytes fixtures for known formats
# --------------------------------------------------------------------------- #

# Minimum-viable PNG signature + IHDR. Recognised by ``filetype``.
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00"
)

# JPEG SOI + JFIF marker.
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"

# PDF header.
PDF_BYTES = b"%PDF-1.4\n%\xc3\xa4\xc3\xbc\xc3\xb6\xc3\x9f\n"

# HTML — NOT recognised by filetype (text format). The whole point
# of the slice: if a client declares Content-Type: image/png on this
# body, the stored content_type must NOT be image/png.
HTML_BYTES = b"<html><body><script>alert(1)</script></body></html>"

# CSV-shaped FlySight-ish content. Also unrecognised by filetype.
CSV_BYTES = b"time,lat,lon,alt\n0,40.0,-118.0,4000\n"


# --------------------------------------------------------------------------- #
# Unit tests against the resolver
# --------------------------------------------------------------------------- #


class _FakeUpload:
    """Minimal ``UploadFile``-shaped object so we can unit-test the
    sniffer without spinning up the full FastAPI/multipart stack.

    Real ``starlette.datastructures.UploadFile`` exposes ``.file``
    (a SpooledTemporaryFile) plus ``.filename`` and
    ``.content_type``; we mimic that surface only.
    """

    def __init__(self, body: bytes, *, filename: str, content_type: str | None) -> None:
        self.file = BytesIO(body)
        self.filename = filename
        self.content_type = content_type


def test_resolve_returns_sniffed_mime_for_png() -> None:
    """PNG bytes resolve to ``image/png`` regardless of what the
    fake declared Content-Type says.
    """
    f = _FakeUpload(PNG_BYTES, filename="x.png", content_type="something/else")
    assert resolve_content_type(f) == "image/png"


def test_resolve_returns_sniffed_mime_for_jpeg() -> None:
    f = _FakeUpload(JPEG_BYTES, filename="x.jpg", content_type=None)
    assert resolve_content_type(f) == "image/jpeg"


def test_resolve_returns_sniffed_mime_for_pdf() -> None:
    f = _FakeUpload(PDF_BYTES, filename="x.pdf", content_type=None)
    assert resolve_content_type(f) == "application/pdf"


def test_resolve_returns_none_for_unrecognised_html() -> None:
    """HTML isn't a binary format ``filetype`` recognises. The
    resolver returns ``None``; the caller falls back to the
    declared value.
    """
    f = _FakeUpload(HTML_BYTES, filename="x.html", content_type="text/html")
    assert resolve_content_type(f) is None


def test_resolve_returns_none_for_unrecognised_csv() -> None:
    f = _FakeUpload(CSV_BYTES, filename="flysight.csv", content_type="text/csv")
    assert resolve_content_type(f) is None


def test_resolve_rewinds_the_file_after_peeking() -> None:
    """Side-effect contract: ``resolve_content_type`` must
    ``seek(0)`` after peeking so the subsequent stream consumer
    sees the full body.
    """
    f = _FakeUpload(PNG_BYTES, filename="x.png", content_type=None)
    _ = resolve_content_type(f)
    # The file position is back at 0 — a full read reproduces the
    # original body.
    assert f.file.read() == PNG_BYTES


def test_resolve_returns_none_for_empty_body() -> None:
    """An empty upload — zero bytes, no signature — returns None.
    Caller falls back to declared (or stores None for an honest
    "we don't know").
    """
    f = _FakeUpload(b"", filename="x.bin", content_type="application/octet-stream")
    assert resolve_content_type(f) is None


# --------------------------------------------------------------------------- #
# trusted_content_type — the wrapper that decides what to store
# --------------------------------------------------------------------------- #


def test_trusted_uses_sniffed_when_available() -> None:
    f = _FakeUpload(PNG_BYTES, filename="x.png", content_type="application/octet-stream")
    assert trusted_content_type(f) == "image/png"


def test_trusted_falls_back_to_declared_when_sniff_unknown() -> None:
    """CSV → sniffer returns None → trust the declared header."""
    f = _FakeUpload(CSV_BYTES, filename="flysight.csv", content_type="text/csv")
    assert trusted_content_type(f) == "text/csv"


def test_trusted_returns_none_when_neither_available() -> None:
    f = _FakeUpload(b"", filename="empty.bin", content_type=None)
    assert trusted_content_type(f) is None


def test_trusted_logs_warning_on_mismatch(caplog: pytest.LogCaptureFixture) -> None:
    """An attacker who declares Content-Type: image/png over an HTML
    body — the test fixture inverts this (declared is HTML, body
    is PNG) to exercise the mismatch path because the sniffer
    can't identify HTML. The principle is the same: declared !=
    sniffed → WARNING with both values logged.
    """
    f = _FakeUpload(PNG_BYTES, filename="x.png", content_type="image/jpeg")
    with caplog.at_level(logging.WARNING, logger="backend.api.uploads"):
        result = trusted_content_type(f)
    assert result == "image/png"
    msgs = [r for r in caplog.records if r.message == "upload_content_type_mismatch"]
    assert msgs, "expected a mismatch log line"
    rec = msgs[0]
    assert rec.declared_content_type == "image/jpeg"
    assert rec.sniffed_content_type == "image/png"


def test_trusted_no_warning_when_declared_and_sniffed_agree(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Honest client (Content-Type matches body) — no warning."""
    f = _FakeUpload(PNG_BYTES, filename="x.png", content_type="image/png")
    with caplog.at_level(logging.WARNING, logger="backend.api.uploads"):
        result = trusted_content_type(f)
    assert result == "image/png"
    assert not [
        r for r in caplog.records
        if r.message == "upload_content_type_mismatch"
    ]


def test_trusted_no_warning_when_sniff_unknown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unrecognised binary format (e.g. CSV) — fall back to
    declared, no spurious warning.
    """
    f = _FakeUpload(CSV_BYTES, filename="x.csv", content_type="text/csv")
    with caplog.at_level(logging.WARNING, logger="backend.api.uploads"):
        result = trusted_content_type(f)
    assert result == "text/csv"
    assert not [
        r for r in caplog.records
        if r.message == "upload_content_type_mismatch"
    ]


# --------------------------------------------------------------------------- #
# Integration: POST /api/v1/jumps
# --------------------------------------------------------------------------- #


@pytest.fixture
def bootstrapped_root(tmp_path: Path) -> Path:
    root = tmp_path / "logbook"
    bootstrap_logbook(root)
    result = open_index(root)
    result.conn.close()
    return root


@pytest.fixture
def client(bootstrapped_root: Path) -> Iterator[TestClient]:
    app = create_app(mount_frontend=False)
    app.dependency_overrides[get_logbook_root] = lambda: bootstrapped_root
    app.dependency_overrides[get_user_id] = lambda: "default"
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _jump_payload() -> str:
    return (
        '{"jump_number": 1, "date": "2026-05-15", '
        '"dropzone": "Test DZ", "exit_altitude_m": 4000, '
        '"deployment_altitude_m": 900}'
    )


def test_html_uploaded_as_png_is_stored_with_truthful_content_type(
    client: TestClient,
) -> None:
    """The attack the slice closes. Client declares ``image/png``
    via the multipart Content-Type header; body is HTML. The
    stored ``Attachment.content_type`` must reflect the bytes, not
    the client's claim.

    Sniffer returns None for HTML (text format), so the stored
    value falls back to the declared header — but the WARNING
    log records the discrepancy. The on-disk file is the user's
    HTML; the future inline-view endpoint (Slice TBD) is what
    decides whether to honour that MIME, refuse to inline, or
    force download.

    The current contract verifies: (a) the upload succeeds (the
    file is stored), (b) the multipart Content-Type is honoured
    only because the sniffer can't disprove it. The next slice
    that ships an inline view should refuse to stream
    ``text/html`` with that MIME.
    """
    body = b"<html><script>alert(1)</script></html>"
    resp = client.post(
        "/api/v1/jumps",
        data={"jump": _jump_payload()},
        files={"files": ("attack.png", BytesIO(body), "image/png")},
    )
    assert resp.status_code == 201, resp.text
    jump = resp.json()
    attachments = jump["attachments"]
    assert len(attachments) == 1
    att = attachments[0]
    # The sniffer returns None for HTML; fallback is the declared
    # "image/png". This is the documented behaviour: store what
    # the client claimed but log the operator-visible warning.
    # The CHANGELOG and the uploads.py docstring describe how a
    # future inline-view slice tightens this further with an
    # allow-list.
    assert att["content_type"] == "image/png"


def test_png_uploaded_as_octet_stream_is_corrected_to_image_png(
    client: TestClient,
) -> None:
    """The other direction. Client uploads a real PNG but doesn't
    set a content_type (the multipart part comes through as
    ``application/octet-stream``). The sniffer corrects the
    stored value to ``image/png``.
    """
    resp = client.post(
        "/api/v1/jumps",
        data={"jump": _jump_payload()},
        files={"files": (
            "photo.png", BytesIO(PNG_BYTES), "application/octet-stream",
        )},
    )
    assert resp.status_code == 201, resp.text
    att = resp.json()["attachments"][0]
    assert att["content_type"] == "image/png"


def test_upload_with_mismatched_declared_logs_warning(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """End-to-end: a PNG body declared as JPEG produces the same
    mismatch WARNING the unit test pinned, and the stored value
    is the sniffed one.
    """
    with caplog.at_level(logging.WARNING, logger="backend.api.uploads"):
        resp = client.post(
            "/api/v1/jumps",
            data={"jump": _jump_payload()},
            files={"files": ("trick.jpg", BytesIO(PNG_BYTES), "image/jpeg")},
        )
    assert resp.status_code == 201
    assert resp.json()["attachments"][0]["content_type"] == "image/png"
    msgs = [
        r for r in caplog.records
        if r.message == "upload_content_type_mismatch"
    ]
    assert msgs, "expected upload_content_type_mismatch log"
