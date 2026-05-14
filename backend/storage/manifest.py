"""SHA-256 manifest per folder (D5, D6, D25).

Format: GNU coreutils `shasum -c` compatible — `<hash>  <path>` (two
spaces, binary-mode). Paths are relative to the folder containing
SHA256SUMS.

`summary.md` is excluded: it is a derived artifact (D5) and regenerable
from jump.xml, so including it would produce false-positive integrity
failures whenever we change the summary template.

**Two manifest sources — do not confuse them (D25 §"Critical distinction").**

- ``generate(folder)`` hashes the bytes on disk right now. Correct for
  the **write path only**: the bytes were just written, ``jump.xml``
  claims were derived from those same bytes, divergence is impossible
  at that point.
- ``from_jump_xml(folder)`` emits a manifest from ``jump.xml``'s
  claimed hashes, plus a freshly-computed hash of ``jump.xml`` itself.
  Correct for the **recovery path only**. If attachment bytes have
  silently rotted, ``generate()`` would "successfully" produce a
  manifest matching the rotten bytes, blessing the corruption. The
  claim-based form preserves ``jump.xml`` as the authoritative witness.

Code review rule: ``generate`` is only ever called from the write path.
Any call site in a recovery or open path is a bug.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..xml.validator import namespace_of, schema_for_namespace, validate
from ..xml.validator import parse as _xml_parse

MANIFEST_NAME = "SHA256SUMS"
"""Filename for the manifest inside each jump folder."""

JUMP_XML_NAME = "jump.xml"
"""Filename of the authoritative jump document inside each jump folder."""

_EXCLUDED_ALWAYS = {MANIFEST_NAME, f"{MANIFEST_NAME}.tmp", "summary.md", "summary.md.tmp"}


def sha256_file(path: Path) -> str:
    """Compute the SHA-256 hex digest of a file, streaming."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def generate(folder: Path, extra_excludes: set[str] | None = None) -> bytes:
    """Build SHA256SUMS content for every regular file under `folder`.

    Walks recursively. Paths are POSIX-style (forward slashes) regardless
    of host OS, so the file round-trips on `shasum -c` across platforms.
    """
    folder = Path(folder)
    excludes = _EXCLUDED_ALWAYS | (extra_excludes or set())
    lines: list[str] = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(folder)
        rel_str = rel.as_posix()
        if rel.name in excludes or rel_str in excludes:
            continue
        digest = sha256_file(path)
        lines.append(f"{digest}  {rel_str}\n")
    return "".join(lines).encode("utf-8")


def parse(manifest_bytes: bytes) -> list[tuple[str, str]]:
    """Parse a SHA256SUMS file into [(digest, rel_path), ...].

    Raises ValueError on malformed lines.
    """
    entries: list[tuple[str, str]] = []
    for lineno, raw in enumerate(manifest_bytes.decode("utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        # GNU format is `<hash><two spaces><path>`. Tolerate `<hash><space>*<path>`
        # too (text-mode marker) for compatibility with third-party tools.
        if "  " in raw:
            digest, rel = raw.split("  ", 1)
        elif " *" in raw:
            digest, rel = raw.split(" *", 1)
        else:
            raise ValueError(f"malformed manifest line {lineno}: {raw!r}")
        if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
            raise ValueError(f"bad digest on line {lineno}: {digest!r}")
        entries.append((digest, rel))
    return entries


def from_jump_xml(
    folder: Path,
    logbook_root: Path | None = None,
    *,
    validate_xsd: bool = True,
) -> bytes:
    """Emit a manifest from ``jump.xml``'s claimed attachment hashes (D25).

    Used on the **recovery path** only — see the module docstring for
    the distinction from ``generate()``. Attachment bytes are never
    read. If they have silently rotted, the manifest we emit still
    says what the file *ought* to hash to according to ``jump.xml``,
    preserving ``jump.xml`` as the authoritative witness of the
    intended content.

    Arguments:
      folder: the jump folder containing ``jump.xml`` and any
        attachments.
      logbook_root: if given, XSD schema files are loaded from
        ``<logbook_root>/SCHEMA.v*.xsd`` per D18. If None, the
        app-shipped copy under ``backend/xml/schema`` is used. After a
        successful bootstrap (D29) the two are byte-identical; the
        parameter exists so the recovery path honors D18's "schema
        lives with the data" guarantee even if the source copy has
        shifted underneath a running app.
      validate_xsd: when False, skip XSD validation and emit the
        manifest from whatever the parser can recover. Used by
        verify in ``.trash/`` per D62, where historical schema drift
        (D57 removed fields, D61 renamed values) makes XSD failures
        expected and uninformative. The manifest itself derives from
        ``<attachment>`` claims, which the parser can still extract
        from a schema-drifted file. Default True preserves the strict
        contract every other caller relies on.

    Raises:
      FileNotFoundError: ``jump.xml`` is absent. Per D25 this means
        "not a valid jump" and is out of reconcile's scope (``verify``
        reports it).
      XMLError (``XMLMalformed`` / ``XSDValidationError`` /
        ``XMLTooLarge``): the hardened parser / validator rejected the
        file. ``XSDValidationError`` is suppressed when
        ``validate_xsd=False``; the others still propagate.
    """
    folder = Path(folder)
    jump_xml_path = folder / JUMP_XML_NAME

    # Hardened parser is CLAUDE.md-invariant for every XML read.
    jump_xml_bytes = jump_xml_path.read_bytes()
    root = _xml_parse(jump_xml_bytes)

    # Validate against the XSD declared by the element's namespace.
    # When ``logbook_root`` is given we look up the schema next to the
    # data (D18); otherwise fall back to the app-shipped copy.
    # ``validate_xsd=False`` (D62, trash-only) skips this step.
    ns = namespace_of(root)
    if validate_xsd:
        schema_dir = logbook_root if logbook_root is not None else None
        schema = schema_for_namespace(ns, schema_dir=schema_dir)
        validate(root, schema=schema)

    # Collect (digest, rel_path) from the XML's claims. Attachments are
    # flat children of the jump folder — D4's sanitize_filename forbids
    # '/' in filenames, so paths are always single-segment.
    entries: list[tuple[str, str]] = []
    entries.append((sha256_bytes(jump_xml_bytes), JUMP_XML_NAME))

    ns_prefix = f"{{{ns}}}" if ns else ""
    atts_el = root.find(f"{ns_prefix}attachments")
    if atts_el is not None:
        for a in atts_el.findall(f"{ns_prefix}attachment"):
            filename_el = a.find(f"{ns_prefix}filename")
            sha_el = a.find(f"{ns_prefix}sha256")
            # XSD validation above guarantees these children exist and
            # carry text; the ``or ""`` guards are belt-and-braces for
            # mypy/lint strictness.
            filename = (filename_el.text or "") if filename_el is not None else ""
            digest = (sha_el.text or "") if sha_el is not None else ""
            entries.append((digest, filename))

    # Sort by rel_path to match ``generate()``'s output shape (rglob +
    # sorted). Jump.xml lands at its alphabetical position among
    # attachments, which is what a shasum -c consumer expects.
    entries.sort(key=lambda e: e[1])

    return "".join(f"{digest}  {rel}\n" for digest, rel in entries).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    """Compute the SHA-256 hex digest of a bytes object.

    Companion to ``sha256_file`` for when the bytes are already in
    memory (recovery path: we just read ``jump.xml`` to parse it, so
    hashing it from memory avoids a second disk read).
    """
    return hashlib.sha256(data).hexdigest()


JUMPER_XML_NAME = "jumper.xml"
"""Filename of the authoritative jumper document inside a jumper folder."""

ATTACHMENTS_SUBDIR = "attachments"
"""Subfolder name where jumper credential card / medical PDFs live."""

# Two underscores; mirror of the same constant in
# storage/jumper_attachments.py. Duplicated here to avoid a circular
# import (manifest is foundational; jumper_attachments imports from
# manifest indirectly via jumper_migration).
_ATTACHMENT_NAME_SEP = "__"


def from_jumper_xml(folder: Path, logbook_root: Path | None = None) -> bytes:
    """Emit a jumper folder's manifest from ``jumper.xml``'s claimed hashes (D47).

    Mirrors :func:`from_jump_xml` for the jumper folder layout
    (D47, Phase C.1):

        folder/
          jumper.xml
          SHA256SUMS
          attachments/
            <attachment_uuid>__<safe-filename>.<ext>

    Used on the **recovery path** only — see the module docstring for
    the distinction from :func:`generate`. Attachment bytes are never
    read. If they have silently rotted, the manifest we emit still
    says what each file *ought* to hash to according to ``jumper.xml``,
    preserving the XML as the authoritative witness of the intended
    content.

    Each ``<attachment>`` element under ``<jumper>/<attachments>``
    must carry both a ``<filename>`` (the user-facing name) and an
    ``<id>`` (the attachment UUID, required at the Pydantic layer
    per JumperAttachment). The on-disk relative path is computed as
    ``attachments/<id>__<filename>`` to match the layout written by
    :mod:`backend.storage.jumper_attachments`.

    The ``<id>`` field is optional in the shared XSD AttachmentType
    (jump attachments may elide it for backward compat); a jumper
    attachment without an ``<id>`` cannot be located on disk under
    the composed-name scheme. We raise ``ValueError`` in that case
    rather than silently producing a manifest entry with a wrong
    path — better to surface the corruption than to bless it.

    Arguments:
      folder: the jumper folder containing ``jumper.xml`` and any
        attachments under ``attachments/``.
      logbook_root: if given, XSD schema files are loaded from
        ``<logbook_root>/SCHEMA.v*.xsd`` per D18. If None, the
        app-shipped copy under ``backend/xml/schema`` is used.

    Raises:
      FileNotFoundError: ``jumper.xml`` is absent.
      XMLError (``XMLMalformed`` / ``XSDValidationError`` /
        ``XMLTooLarge``): the hardened parser / validator rejected
        the file. Propagates.
      ValueError: an ``<attachment>`` element is missing its
        ``<id>`` child (a jumper attachment must have one).
    """
    folder = Path(folder)
    jumper_xml_path = folder / JUMPER_XML_NAME

    jumper_xml_bytes = jumper_xml_path.read_bytes()
    root = _xml_parse(jumper_xml_bytes)

    ns = namespace_of(root)
    schema_dir = logbook_root if logbook_root is not None else None
    schema = schema_for_namespace(ns, schema_dir=schema_dir)
    validate(root, schema=schema)

    entries: list[tuple[str, str]] = [
        (sha256_bytes(jumper_xml_bytes), JUMPER_XML_NAME),
    ]

    ns_prefix = f"{{{ns}}}" if ns else ""
    atts_el = root.find(f"{ns_prefix}attachments")
    if atts_el is not None:
        for a in atts_el.findall(f"{ns_prefix}attachment"):
            filename_el = a.find(f"{ns_prefix}filename")
            sha_el = a.find(f"{ns_prefix}sha256")
            id_el = a.find(f"{ns_prefix}id")
            filename = (filename_el.text or "") if filename_el is not None else ""
            digest = (sha_el.text or "") if sha_el is not None else ""
            if id_el is None or not (id_el.text or "").strip():
                # Jumper attachments require <id> per D47 / Phase B.4.
                # An attachment without an id can't be located on
                # disk under <id>__<filename>; surface as a manifest-
                # generation failure rather than write a bad entry.
                raise ValueError(
                    f"jumper attachment {filename!r} is missing required "
                    f"<id> child — cannot compose disk path for manifest"
                )
            attachment_id = (id_el.text or "").strip()
            disk_name = f"{attachment_id}{_ATTACHMENT_NAME_SEP}{filename}"
            rel_path = f"{ATTACHMENTS_SUBDIR}/{disk_name}"
            entries.append((digest, rel_path))

    entries.sort(key=lambda e: e[1])
    return "".join(f"{digest}  {rel}\n" for digest, rel in entries).encode("utf-8")


def verify(folder: Path) -> list[tuple[str, str]]:
    """Verify every file listed in SHA256SUMS against its recorded hash.

    Returns a list of (path, reason) for any problems. An empty list means
    all files match. Missing manifest counts as a problem.
    """
    folder = Path(folder)
    manifest = folder / MANIFEST_NAME
    if not manifest.is_file():
        return [(MANIFEST_NAME, "missing")]
    try:
        entries = parse(manifest.read_bytes())
    except ValueError as e:
        return [(MANIFEST_NAME, str(e))]

    problems: list[tuple[str, str]] = []
    for digest, rel in entries:
        target = folder / rel
        if not target.is_file():
            problems.append((rel, "missing"))
            continue
        actual = sha256_file(target)
        if actual != digest:
            problems.append((rel, f"hash mismatch: expected {digest}, got {actual}"))
    return problems
