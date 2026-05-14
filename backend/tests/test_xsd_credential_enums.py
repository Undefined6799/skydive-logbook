"""Phase B.1 — D47 credential simple types: structural assertions.

These tests verify that the seven new simple types added to
SCHEMA.v1.xsd by D47 are present with exactly the enumeration values
the D-entry specifies. The simple types are not yet referenced by any
element in the schema (that lands in B.2 / B.3); the value-validation
tests for each enum will land alongside the elements that consume
them. For now we assert structurally and confirm the XSD still
compiles — a syntax error in any of the new types would break every
existing roundtrip test, but the structural assertion gives a
sharper failure message ("CSPARatingCode is missing 'pffi'" vs
"XSD failed to compile somewhere").

Per D18: enumerations are additive within v1. If a future
reviewer adds a value, this test should be updated in the same
slice — drift between the XSD and this test is the bug the test
catches.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from backend.xml.validator import _load_schema

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "xml" / "schema" / "SCHEMA.v1.xsd"
XS_NS = "http://www.w3.org/2001/XMLSchema"


# Expected enumeration values for each new simple type per D47. These
# mirror the closed enums documented in the D-entry; if the D-entry is
# updated, this list must move in lockstep.
EXPECTED_ENUMS = {
    "OrgEnum": ["CSPA", "USPA", "OTHER"],
    "CSPACopLevel": ["solo", "a", "b", "c", "d"],
    "USPACopLevel": ["a", "b", "c", "d"],
    "CSPARatingCode": [
        "c1",
        "c2",
        "c3_wingsuit",
        "c3_canopy_piloting",
        "c3_freefly",
        "c3_canopy_formation",
        "cdc",
        "jm",
        "jmr",
        "gci",
        "ssi",
        "pffi",
        "sse",
        "lf",
        "rigger_a",
        "rigger_a1",
        "rigger_a2",
        "rigger_b",
        "rigger_instructor",
        "rigger_examiner",
        "ejr",
    ],
    "USPARatingCode": [
        "coach",
        "affi",
        "iad_i",
        "sl_i",
        "ti",
        "coach_examiner",
        "affi_examiner",
        "iad_examiner",
        "sl_examiner",
        "ti_examiner",
        "course_director",
        "iecd",
        "pro",
        "sta",
    ],
    "TandemSystem": [
        "upt_vector",
        "upt_sigma",
        "strong_dual_hawk",
        "other",
    ],
    "MedicalKind": ["class_iii"],
}


@pytest.fixture(scope="module")
def schema_root() -> etree._Element:
    """Parsed XSD as an lxml element for structural traversal."""
    return etree.parse(str(SCHEMA_PATH)).getroot()


def _find_simple_type(schema_root: etree._Element, name: str) -> etree._Element | None:
    return schema_root.find(f"{{{XS_NS}}}simpleType[@name='{name}']")


def _enumeration_values(simple_type_el: etree._Element) -> list[str]:
    restriction = simple_type_el.find(f"{{{XS_NS}}}restriction")
    assert restriction is not None, "simpleType is missing xs:restriction"
    return [e.get("value") for e in restriction.findall(f"{{{XS_NS}}}enumeration")]


@pytest.mark.parametrize("type_name", sorted(EXPECTED_ENUMS.keys()))
def test_simple_type_is_defined(schema_root: etree._Element, type_name: str) -> None:
    """Each of the seven new simple types must exist in SCHEMA.v1.xsd."""
    el = _find_simple_type(schema_root, type_name)
    assert el is not None, (
        f"{type_name} not found in SCHEMA.v1.xsd — D47 / Phase B.1 added it; "
        f"if the type was renamed update EXPECTED_ENUMS and the D-entry together."
    )


@pytest.mark.parametrize("type_name,expected", sorted(EXPECTED_ENUMS.items()))
def test_simple_type_enumeration_values(
    schema_root: etree._Element, type_name: str, expected: list[str]
) -> None:
    """The enumeration values must match D47 exactly, in order.

    Order matters because the on-disk XSD file is a contract artifact
    (D5: self-describing to a human editor). A reviewer reading the
    enum should see the values in the order the D-entry presents
    them, so reordering a value is treated the same as renaming it
    and the test enforces that.
    """
    el = _find_simple_type(schema_root, type_name)
    assert el is not None, f"{type_name} missing"
    assert _enumeration_values(el) == expected


def test_simple_type_has_documentation(schema_root: etree._Element) -> None:
    """Every new simple type must carry an xs:documentation block.

    D47 / D5 require the on-disk XML to be self-describing — a human
    opening SCHEMA.v1.xsd should be able to read why each enum exists
    and where its values came from without consulting the application
    source. The annotation is the project's mechanism for that.
    """
    for type_name in EXPECTED_ENUMS:
        el = _find_simple_type(schema_root, type_name)
        assert el is not None, f"{type_name} missing"
        annotation = el.find(f"{{{XS_NS}}}annotation")
        assert annotation is not None, f"{type_name} is missing xs:annotation"
        documentation = annotation.find(f"{{{XS_NS}}}documentation")
        assert documentation is not None, f"{type_name} is missing xs:documentation"
        body = (documentation.text or "").strip()
        assert len(body) > 50, (
            f"{type_name}'s xs:documentation is too short to be useful — "
            f"D5 requires the schema to explain itself."
        )


def test_schema_still_compiles() -> None:
    """The XSD must still load without errors after the B.1 additions.

    This is the implicit invariant every other roundtrip test relies
    on; an explicit assertion here gives a sharper failure message
    when a typo in the new types breaks the schema as a whole.
    """
    # _load_schema is cached but the cache is keyed on the path, so a
    # post-edit rerun of the test suite picks up the current bytes.
    schema = _load_schema(SCHEMA_PATH)
    assert isinstance(schema, etree.XMLSchema)
