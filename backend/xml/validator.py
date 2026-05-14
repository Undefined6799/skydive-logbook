"""Defensive XML parsing + XSD validation (D2, D6, D18).

All XML that enters this process goes through `parse()`. Size is capped
at 10 MB, any DOCTYPE declaration is rejected before lxml sees the
bytes, and the parser is configured with entity resolution, DTD loading,
and network access all disabled. XXE and billion-laughs attacks are
structurally impossible.

The 10 MB cap is generous — a jump XML file is typically under 10 KB.
The limit is there to kill trivial DoS attempts before lxml is even
invoked. Raise it only if a legitimate field grows large (e.g. embedded
binary), and add a test for the new bound at the same time.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeAlias

from lxml import etree

from ..models.common import SCHEMA_NAMESPACE_V1

# Public type aliases for the lxml objects this module returns. lxml's
# bundled type stubs are incomplete (the C-extension surface of
# ``lxml.etree`` is opaque to pyright), so concrete annotations like
# ``etree._Element`` cascade as ``Unknown`` through every caller. These
# aliases expose ``Any`` to callers — accurate about what pyright can
# verify — while keeping a name for documentation and future tightening
# if upstream stubs improve.
XMLElement: TypeAlias = Any
XMLSchema: TypeAlias = Any

MAX_XML_BYTES = 10 * 1024 * 1024  # 10 MB. Jump XML is typically < 10 KB.

# Pre-parse defense against DOCTYPE declarations (XXE / billion-laughs).
# lxml does not expose a ``forbid_dtd`` kwarg on XMLParser; a byte-level
# scan runs before lxml sees the bytes, so the check is version-agnostic
# and catches every class of DTD-based attack at the earliest point.
#
# The XML spec mandates uppercase ``<!DOCTYPE``, but we match
# case-insensitively so a non-conformant writer cannot smuggle one in
# via lowercase.
#
# Theoretical false positive: literal ``<!DOCTYPE`` inside a CDATA
# section. Not a real shape in this project — jump notes never contain
# that string — and erring on the side of rejecting is the correct
# posture for untrusted XML.
_DOCTYPE_RE = re.compile(rb"<!DOCTYPE", re.IGNORECASE)

SCHEMA_DIR = Path(__file__).parent / "schema"
"""Source copies shipped with the app. On first-run these are copied into
the logbook root so the data is self-describing without the app (D5, D18)."""


class XMLError(RuntimeError):
    """Base class for XML parsing / validation failures."""


class XMLTooLarge(XMLError):
    pass


class XMLMalformed(XMLError):
    pass


class XSDValidationError(XMLError):
    pass


def _make_parser() -> etree.XMLParser:
    """Build a parser with all dangerous lxml features disabled.

    lxml does not expose a ``forbid_dtd`` kwarg (verified against
    lxml 5.x / 6.x — see commit history), so we layer the defenses:

    - ``resolve_entities=False`` — custom entities are not expanded,
      which neutralizes the billion-laughs class (entities may be
      declared but cannot blow up memory).
    - ``no_network=True`` — SYSTEM / PUBLIC references are never
      fetched; external DTD retrieval is impossible.
    - ``load_dtd=False`` — inline DTDs are not loaded for entity
      declarations; reinforces ``resolve_entities=False``.
    - ``dtd_validation=False`` — we never use DTD-driven defaults.
    - ``huge_tree=False`` — keeps lxml's default limits on attribute
      and element counts.

    DOCTYPE declarations are rejected *before* lxml sees the bytes
    (see ``parse``), so the "safe by construction" guarantee does not
    depend on lxml's internal behavior when handed a malicious DTD.

    Refs:
      - https://lxml.de/parsing.html
      - https://cheatsheetseries.owasp.org/cheatsheets/XML_External_Entity_Prevention_Cheat_Sheet.html
    """
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        huge_tree=False,
        load_dtd=False,
        dtd_validation=False,
        remove_blank_text=False,
        remove_comments=False,
    )


def parse(data: bytes) -> XMLElement:
    """Parse bytes into an Element. Rejects oversize or malformed input.

    Any ``<!DOCTYPE ...>`` declaration is rejected with ``XMLMalformed``
    before lxml is invoked (D6). This shuts down XXE and billion-laughs
    at the earliest possible point and is independent of any specific
    lxml version's DTD-handling behavior.
    """
    if len(data) > MAX_XML_BYTES:
        raise XMLTooLarge(f"XML input exceeds {MAX_XML_BYTES} bytes")
    if _DOCTYPE_RE.search(data):
        raise XMLMalformed("DOCTYPE declarations are not permitted")
    try:
        return etree.fromstring(data, _make_parser())
    except etree.XMLSyntaxError as e:
        raise XMLMalformed(f"malformed XML: {e}") from e


@lru_cache(maxsize=8)
def _load_schema(xsd_path: Path) -> XMLSchema:
    # XSD files are trusted (shipped with the app); we still parse with
    # the hardened parser, but we do NOT apply the DOCTYPE byte check
    # because lxml's XSD loader occasionally relies on DTD internals.
    #
    # Cache lifetime: keyed on the Path object only — NOT on file
    # mtime. The cache is therefore stale if the XSD bytes on disk
    # change between two lookups for the same path within one process
    # lifetime. In practice the app bootstraps once (D29 writes the
    # XSDs into the logbook root) and never mutates them at runtime,
    # so the invalidation gap is unreachable. If a future slice ever
    # rewrites an XSD mid-process (e.g. a hot-reload for schema
    # development), key on ``(xsd_path, xsd_path.stat().st_mtime_ns)``
    # instead.
    parser = _make_parser()
    doc = etree.parse(str(xsd_path), parser)
    return etree.XMLSchema(doc)


def schema_for_namespace(namespace: str, schema_dir: Path | None = None) -> XMLSchema:
    """Return the cached XMLSchema matching an XML's declared namespace.

    Look-up strategy: map namespace → filename.
      `https://skydive-logbook.org/schema/v1` → `SCHEMA.v1.xsd`
    (D18: every namespace has a file in the schema dir.)
    """
    schema_dir = schema_dir or SCHEMA_DIR
    if namespace == SCHEMA_NAMESPACE_V1:
        return _load_schema(schema_dir / "SCHEMA.v1.xsd")
    raise XSDValidationError(f"no schema registered for namespace {namespace!r}")


def validate(element: XMLElement, schema: XMLSchema | None = None) -> None:
    """Validate an element against its schema. Raises XSDValidationError on failure.

    If `schema` is omitted, one is selected by the element's namespace.
    """
    if schema is None:
        ns = etree.QName(element).namespace
        schema = schema_for_namespace(ns or "")
    if not schema.validate(element):
        raise XSDValidationError(f"XSD validation failed: {schema.error_log}")


def namespace_of(element: XMLElement) -> str:
    """Return the XML namespace declared on ``element``, or ``""``.

    Wraps ``etree.QName(element).namespace`` so callers in
    ``backend/storage/`` and ``backend/services/`` don't need to
    import lxml directly. The empty-string return matches D18's
    "no namespace declared" case.
    """
    ns = etree.QName(element).namespace
    return ns or ""


def validate_schema_file(xsd_path: Path) -> None:
    """Confirm that ``xsd_path`` parses as a valid XML Schema (D29).

    Boot-time safety check: a freshly-installed XSD that lxml cannot
    compile would silently break every read path. ``bootstrap_logbook``
    calls this after copying a shipped XSD into the logbook root so
    failure surfaces at startup, not at the first GET.

    Raises ``XSDValidationError`` (wrapping lxml's
    ``XMLSchemaParseError``) if the file is corrupt or malformed.

    Lives in this module — alongside ``parse`` and ``validate`` — so
    every direct lxml use stays inside the ``backend/xml/`` typed
    boundary (the pyright config silences lxml stub gaps for this
    folder).
    """
    parser = _make_parser()
    try:
        doc = etree.parse(str(xsd_path), parser)
        etree.XMLSchema(doc)
    except etree.XMLSchemaParseError as exc:
        raise XSDValidationError(
            f"XSD at {xsd_path} did not compile: {exc}"
        ) from exc
