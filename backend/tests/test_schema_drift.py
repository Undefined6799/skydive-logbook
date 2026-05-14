"""Field-level drift detector for the D2 three-place invariant.

D2 makes "every field exists in three synchronized places — Pydantic
model, XSD, SQLite index" load-bearing. Runtime XSD validation catches
*serializer-vs-XSD* drift on the first write, but it can't catch the
opposite cases:

  * A field added to a Pydantic model that the serializer never emits.
    Writes succeed (XSD doesn't see the missing element), but reads
    silently lose data — the round-trip equality every API consumer
    depends on is broken.
  * A field added to a Pydantic model and to the serializer, but missing
    from the XSD. Caught at runtime by ``validate()`` — too late for the
    CI gate.

This test fires *lexically*: for every top-level Pydantic entity model,
each ``model_fields`` name must appear as a string in both
:mod:`backend.xml.serialize` and ``SCHEMA.v1.xsd``. False positives are
possible (a field name mentioned only in a docstring would slip
through), but in practice every emitted XML element references its name
as a string literal in ``_sub(...)`` and every XSD element declares
``name="…"``, so the check catches the common drift case before the
pull request lands.

The check is intentionally minimal: it does not enforce ordering, types,
optionality, or the SQLite mapping. Those layers fail loud when wrong;
the silent-drift hole is the one this test plugs.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from backend.models.aad import AAD
from backend.models.container import Container
from backend.models.dropzone import Dropzone
from backend.models.jump import Jump
from backend.models.jumper import Jumper
from backend.models.main import Main
from backend.models.person import Person
from backend.models.reserve import Reserve
from backend.models.rig import Rig

# Pydantic field names that are deliberately NOT serialized to XML.
# Each entry is justified — anything else added here without rationale
# is a code-review smell.
_NON_XML_FIELDS: dict[type[BaseModel], frozenset[str]] = {}

_SERIALIZE_SOURCE = (
    Path(__file__).resolve().parents[1] / "xml" / "serialize.py"
).read_text()

_XSD_SOURCE = (
    Path(__file__).resolve().parents[1] / "xml" / "schema" / "SCHEMA.v1.xsd"
).read_text()


# Every top-level entity that round-trips through XML. Add new entities
# here as they ship; the assertion fires the first time the field set
# drifts from the serializer or the XSD.
_ENTITIES: tuple[type[BaseModel], ...] = (
    Jump,
    Rig,
    Main,
    Reserve,
    AAD,
    Container,
    Dropzone,
    Jumper,
    Person,
)


@pytest.mark.parametrize("model_cls", _ENTITIES, ids=lambda c: c.__name__)
def test_every_pydantic_field_is_in_the_serializer(
    model_cls: type[BaseModel],
) -> None:
    """Every field on a top-level entity must appear in ``serialize.py``.

    The serializer references each field name as a string literal in
    ``_sub(...)`` (emit) or ``_text(...)`` (parse). A field added to the
    model but absent from both call sites would round-trip-lose data on
    every read.
    """
    skip = _NON_XML_FIELDS.get(model_cls, frozenset())
    missing = sorted(
        name for name in model_cls.model_fields if name not in skip
        and f'"{name}"' not in _SERIALIZE_SOURCE
    )
    assert not missing, (
        f"{model_cls.__name__} fields not referenced in serialize.py: "
        f"{missing}. Either wire them through ``*_to_element`` /"
        f" ``element_to_*`` or, if intentionally non-XML, add them to"
        f" ``_NON_XML_FIELDS`` with a justification comment."
    )


@pytest.mark.parametrize("model_cls", _ENTITIES, ids=lambda c: c.__name__)
def test_every_pydantic_field_is_declared_in_the_xsd(
    model_cls: type[BaseModel],
) -> None:
    """Every field on a top-level entity must be declared in the XSD.

    XSD validation catches the opposite direction (serializer emits a
    field the XSD doesn't know) at write time. This catches the cases
    where a field is added to Pydantic and to the serializer but the
    XSD wasn't updated — the first real write blows up at runtime
    instead of failing CI.
    """
    skip = _NON_XML_FIELDS.get(model_cls, frozenset())
    missing = sorted(
        name for name in model_cls.model_fields if name not in skip
        and f'name="{name}"' not in _XSD_SOURCE
    )
    assert not missing, (
        f"{model_cls.__name__} fields not declared in SCHEMA.v1.xsd: "
        f"{missing}. Add an ``<xs:element name=\"…\">`` (or extend an"
        f" existing complex type) before merging; runtime XSD validation"
        f" would otherwise reject every write that touches these fields."
    )
