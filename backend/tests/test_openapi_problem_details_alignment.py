"""Pin alignment between the hand-authored ``PROBLEM_DETAILS_SCHEMA``
in ``backend/api/openapi.py`` and the Pydantic ``ProblemDetails``
model in ``backend/api/errors.py``.

The OpenAPI schema is hand-written rather than generated from the
Pydantic model so the published JSON Schema reads against the
RFC 9457 vocabulary verbatim — descriptions and examples keyed to
``type`` / ``title`` / ``status`` / ``detail`` / ``instance`` / our
documented extensions (``code``, ``request_id``, ``errors``). The
trade-off the 2026-05-14 audit flagged (§1.2): nothing keeps the
two in sync. A new ``ProblemDetails`` field could ship without a
schema edit; clients regenerating from ``/openapi.json`` would never
see it.

This test catches that drift at PR time. The Pydantic model is the
source of truth for which fields the wire body can carry; the
hand-authored schema is the source of truth for how those fields
are described to SDK consumers. Every Pydantic field must be
mentioned in the schema's properties; every schema property must
correspond to either a Pydantic field or a known RFC-9457
extension.
"""
from __future__ import annotations

from typing import Any

from backend.api.errors import FieldError, ProblemDetails
from backend.api.openapi import PROBLEM_DETAILS_SCHEMA


def _pydantic_properties() -> set[str]:
    """The set of fields declared on the Pydantic ``ProblemDetails``.

    Reads ``model_fields`` rather than ``model_json_schema`` so the
    enumeration is structural — independent of pydantic's JSON
    Schema generator's quirks across pydantic versions.
    """
    return set(ProblemDetails.model_fields)


def _schema_properties() -> set[str]:
    return set(PROBLEM_DETAILS_SCHEMA["properties"])


def test_every_pydantic_field_appears_in_hand_authored_schema() -> None:
    """A new field on ``ProblemDetails`` must update the OpenAPI schema.

    The test fails if the model adds a field the schema doesn't
    describe — exactly the drift class the 2026-05-14 audit §1.2
    flagged.
    """
    missing = _pydantic_properties() - _schema_properties()
    assert not missing, (
        "ProblemDetails fields present on the model but missing from "
        f"PROBLEM_DETAILS_SCHEMA: {sorted(missing)}. Either add the "
        "property to openapi.PROBLEM_DETAILS_SCHEMA with a description "
        "and example, or remove the field from the Pydantic model."
    )


def test_no_schema_property_is_orphaned_from_the_model() -> None:
    """A property in the hand-authored schema must correspond to a
    real field on ``ProblemDetails`` (so the spec doesn't promise a
    wire field the server never emits).

    Pydantic's ``extra="allow"`` config lets the model carry
    arbitrary extension members at runtime, but the schema should
    only document the ones we explicitly mint via the
    ``ServiceError(.., **details)`` extension mechanism — and right
    now there are none of those. If a future ServiceError subclass
    adds an extension, the schema gets that property too.
    """
    orphans = _schema_properties() - _pydantic_properties()
    assert not orphans, (
        "PROBLEM_DETAILS_SCHEMA declares properties that don't "
        f"correspond to ProblemDetails model fields: {sorted(orphans)}. "
        "Either add the field to the Pydantic model, or remove the "
        "property from the schema."
    )


def test_required_fields_match() -> None:
    """The schema's ``required`` list must match Pydantic's required
    fields (fields without a default).

    A drift here lets the schema advertise a required field that the
    model treats as optional, or vice versa — either case breaks SDK
    consumers' expectations about what they can safely access on a
    decoded response.
    """
    pyd_required = {
        name for name, info in ProblemDetails.model_fields.items()
        if info.is_required()
    }
    schema_required = set(PROBLEM_DETAILS_SCHEMA["required"])
    assert pyd_required == schema_required, (
        f"required-field mismatch — Pydantic: {sorted(pyd_required)}, "
        f"schema: {sorted(schema_required)}. The schema's ``required`` "
        "list and the Pydantic model's required fields must agree."
    )


def test_field_error_shape_is_consistent_with_schema_errors_items() -> None:
    """The ``errors`` array items in the schema must match
    ``FieldError``'s field set.

    Easy slip: change the ``FieldError`` Pydantic model (e.g. rename
    ``pointer`` → ``path``) without touching the nested schema under
    ``errors.items.properties``.
    """
    pyd_fields = set(FieldError.model_fields)
    items: dict[str, Any] = PROBLEM_DETAILS_SCHEMA["properties"]["errors"]["items"]
    schema_props = set(items["properties"])
    assert pyd_fields == schema_props, (
        f"FieldError shape mismatch — Pydantic: {sorted(pyd_fields)}, "
        f"schema items: {sorted(schema_props)}."
    )
    pyd_required_field = {
        name for name, info in FieldError.model_fields.items()
        if info.is_required()
    }
    schema_required_field = set(items["required"])
    assert pyd_required_field == schema_required_field, (
        f"FieldError required-field mismatch — Pydantic: "
        f"{sorted(pyd_required_field)}, "
        f"schema items.required: {sorted(schema_required_field)}."
    )


def test_schema_documents_known_extension_members() -> None:
    """Spot-check that the schema describes the three documented
    extension members (``code``, ``request_id``, ``errors``).

    These are the wire-visible extensions D16 names — they should
    always be in the schema regardless of how the Pydantic model
    evolves around them. Catches an accidental field-rename or
    -removal that the alignment test above would also catch but
    that's worth pinning by name for documentation clarity.
    """
    for extension in ("code", "request_id", "errors"):
        assert extension in PROBLEM_DETAILS_SCHEMA["properties"], (
            f"PROBLEM_DETAILS_SCHEMA missing documented extension "
            f"member {extension!r}"
        )
