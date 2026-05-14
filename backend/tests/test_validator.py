"""Tests for XML parsing hardening and XSD loading (D2, D6)."""
from __future__ import annotations

import pytest

from backend.models.common import SCHEMA_NAMESPACE_V1
from backend.xml.validator import (
    XMLMalformed,
    XMLTooLarge,
    XSDValidationError,
    parse,
    schema_for_namespace,
)

# Keep imports used to silence ruff if we later enable it.
_ = XMLMalformed


class TestParse:
    def test_accepts_valid_xml(self):
        el = parse(b"<root><child/></root>")
        assert el.tag == "root"

    def test_rejects_oversize(self):
        huge = b"<r>" + b"x" * (20 * 1024 * 1024) + b"</r>"
        with pytest.raises(XMLTooLarge):
            parse(huge)

    def test_rejects_malformed(self):
        with pytest.raises(XMLMalformed):
            parse(b"<not-closed>")

    def test_rejects_any_doctype(self):
        """forbid_dtd=True: any <!DOCTYPE ...> is rejected up front.

        This shuts down both the classic file:// XXE and the
        billion-laughs class of attacks (no DTD = no entity
        declarations = no bomb).
        """
        xxe = (
            b'<?xml version="1.0"?>'
            b'<!DOCTYPE r [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            b"<r>&xxe;</r>"
        )
        with pytest.raises(XMLMalformed):
            parse(xxe)

    def test_rejects_plain_doctype(self):
        """Even a DOCTYPE with no entities is rejected (defense in depth)."""
        doc = b'<?xml version="1.0"?><!DOCTYPE r><r/>'
        with pytest.raises(XMLMalformed):
            parse(doc)

    def test_rejects_billion_laughs(self):
        """Billion-laughs cannot be set up: the recursive entity
        declarations need a DOCTYPE, and DOCTYPE is rejected."""
        bomb = (
            b'<?xml version="1.0"?>'
            b'<!DOCTYPE lolz ['
            b'  <!ENTITY lol "lol">'
            b'  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
            b']>'
            b"<lolz>&lol2;</lolz>"
        )
        with pytest.raises(XMLMalformed):
            parse(bomb)

    def test_rejects_external_dtd_reference(self):
        """External DTD fetch is impossible because DOCTYPE is rejected."""
        ext = (
            b'<?xml version="1.0"?>'
            b'<!DOCTYPE r SYSTEM "http://example.com/evil.dtd">'
            b"<r/>"
        )
        with pytest.raises(XMLMalformed):
            parse(ext)

    def test_rejects_doctype_inside_cdata(self):
        """TEST-6 — DOCTYPE-in-CDATA pinning (audit 2026-04-29).

        ``_DOCTYPE_RE`` is a byte-level scan that fires regardless of
        XML context — including inside a CDATA section, a comment, or
        an attribute value. The validator's module docstring
        acknowledges this as a "theoretical false positive" and
        deliberately accepts it: erring on the side of rejecting is
        the correct posture for untrusted XML.

        This test pins the *current* posture so a future parser
        refactor that tries to be cleverer (e.g. tokenise first, then
        check) cannot silently relax the defense without flipping
        this red.

        If a real-world payload ever needs to carry the literal string
        ``<!DOCTYPE`` (jump notes don't, today), this rejection is the
        signal to escalate to a D-entry decision rather than to
        weaken the regex.
        """
        # Literal ``<!DOCTYPE`` byte sequence inside a CDATA section.
        # XML-spec-wise the bytes inside <![CDATA[ ... ]]> are character
        # data, not markup — but the pre-parse scan does not parse
        # CDATA, so the rejection fires.
        doc = (
            b'<?xml version="1.0"?>'
            b'<r><![CDATA[ <!DOCTYPE foo> ]]></r>'
        )
        with pytest.raises(XMLMalformed):
            parse(doc)

    def test_rejects_doctype_case_insensitive(self):
        """Pin the case-insensitive byte scan: ``<!doctype``,
        ``<!Doctype``, etc. all reject. The XML spec mandates
        uppercase ``<!DOCTYPE``, but a non-conformant writer could
        emit lowercase and we want the earliest-possible rejection.
        """
        for variant in (
            b'<!DOCTYPE r><r/>',
            b'<!doctype r><r/>',
            b'<!Doctype r><r/>',
            b'<!docTYPE r><r/>',
        ):
            with pytest.raises(XMLMalformed):
                parse(b'<?xml version="1.0"?>' + variant)


class TestSchemaLookup:
    def test_v1_loads(self):
        schema = schema_for_namespace(SCHEMA_NAMESPACE_V1)
        assert schema is not None

    def test_unknown_namespace_raises(self):
        with pytest.raises(XSDValidationError):
            schema_for_namespace("https://example.com/not-us/v9")
