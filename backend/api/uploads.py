"""Upload helpers: magic-bytes content-type sniffing (Slice 11).

The client's multipart ``Content-Type`` header is user-controlled —
an attacker who renames ``evil.html`` to ``harmless.png`` and uploads
it has the multipart part claim ``image/png`` while the bytes are
HTML. The pre-Slice-11 code stored that declared value verbatim into
``Attachment.content_type``; the latent hazard was a future inline-
view endpoint streaming the bytes with the stored MIME as the
response ``Content-Type``, which a browser-side MIME sniffer or a
naive `<img>` tag would honour and execute.

This module fixes the trust direction. Every uploaded file is
sniffed via :mod:`filetype` (magic-bytes detection, pure Python,
MIT-licensed); the resolved MIME is what lands in
``Attachment.content_type``. The declared value is logged when it
disagrees with the sniffed value so an operator can see attempts
to spoof. No hard allow-list in v0.1 — a logbook is the user's own
data and locking it down to a narrow type set would hurt UX (note
files, FlySight CSVs, scanned PDFs). The allow-list lands when the
inline-view endpoint does.
"""
from __future__ import annotations

import logging

# ``filetype`` ships no type stubs; pyright otherwise raises
# reportMissingTypeStubs. The runtime contract is well-documented
# (filetype.guess(bytes) -> Type | None with .MIME class attr) and
# pinned by test_upload_content_type_sniffing.py.
import filetype  # pyright: ignore[reportMissingTypeStubs]
from fastapi import UploadFile

_logger = logging.getLogger("backend.api.uploads")

# filetype documents its detection peeking at the first 261 bytes
# of the buffer; 512 gives a comfortable margin and stays well
# under any plausible network frame.
_SNIFF_PEEK_BYTES = 512


def resolve_content_type(upload: UploadFile) -> str | None:
    """Sniff ``upload``'s first bytes and return the trusted MIME.

    Side effect: reads up to :data:`_SNIFF_PEEK_BYTES` from
    ``upload.file`` then ``seek(0)``s so the subsequent
    ``_upload_chunks`` pass sees the full body. Both
    ``SpooledTemporaryFile`` (pre-spool, in-memory) and the
    spilled-to-disk variant support seek.

    Returns:
        * The sniffed MIME if :mod:`filetype` recognises the bytes.
        * ``None`` if the format is unrecognised — typical for
          plain text, CSV, FlySight logs, hand-written notes. The
          caller should fall back to the declared
          ``upload.content_type`` (or store ``None``) in this case.

    The declared multipart Content-Type is NOT consulted by this
    function — it's adversarial input. The caller decides what to
    do with the declared value (warn, ignore) by comparing it
    against the return value.
    """
    f = upload.file
    head = f.read(_SNIFF_PEEK_BYTES)
    try:
        f.seek(0)
    except OSError:
        # SpooledTemporaryFile + a backing file that rejected seek
        # is exotic enough that we don't try to recover — surface
        # the error so an integrator sees it rather than silently
        # corrupting the upload.
        raise
    if not head:
        return None
    # filetype ships no type stubs (the runtime return is one of
    # ~70 hardcoded subclasses with a ``MIME`` class attribute,
    # plus None). pyright can't narrow that union across a hashable
    # object, so the access is suppressed at the callsite — the
    # runtime behaviour is well-pinned by the unit tests in
    # test_upload_content_type_sniffing.py.
    kind = filetype.guess(head)  # pyright: ignore[reportUnknownMemberType]
    if kind is None:
        return None
    return str(kind.MIME)


def trusted_content_type(upload: UploadFile) -> str | None:
    """Return the content type to store on the Attachment.

    Trust order:
      1. Sniffed MIME from :func:`resolve_content_type` if available.
      2. Declared ``upload.content_type`` if the sniffer returned
         ``None`` (the format is one :mod:`filetype` doesn't know —
         plain text, CSV, etc.; the declared value is the only
         hint we have).
      3. ``None`` if neither is available.

    Logs a WARNING when the declared and sniffed values disagree,
    naming both — gives an operator a paper trail for "did
    someone try to upload HTML masqueraded as an image?"
    """
    sniffed = resolve_content_type(upload)
    declared = upload.content_type
    if sniffed is not None and declared is not None and sniffed != declared:
        # ``filename`` is a reserved LogRecord attribute (stdlib
        # logging stamps the source file's name into it), so we
        # name the user-facing field ``upload_filename`` instead.
        # Same convention as jump_service.delete_attachment's
        # ``attachment`` key.
        _logger.warning(
            "upload_content_type_mismatch",
            extra={
                "upload_filename": upload.filename or "",
                "declared_content_type": declared,
                "sniffed_content_type": sniffed,
            },
        )
    if sniffed is not None:
        return sniffed
    return declared
