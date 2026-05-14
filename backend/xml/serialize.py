"""Pydantic ↔ XML serialization (D2).

No business logic here. Round-trip contract:
    jump_to_bytes(j)  → xml_to_jump(parse(bytes)) == j   (for any valid Jump)

Every write path in services must call `validate()` on the serialized
element before persisting, per D2 and the backend-engineer rules.
"""
from __future__ import annotations

from datetime import date as _date
from datetime import time as _time
from uuid import UUID

from lxml import etree

from ..models._component_base import (
    ComponentBase,
    ComponentStatus,
    NotesLogEntry,
)
from ..models.aad import AAD
from ..models.common import GENERATOR_STRING, SCHEMA_NAMESPACE_V1
from ..models.container import Container
from ..models.dropzone import Dropzone, DropzoneAircraft, Environment
from ..models.jump import Attachment, Jump, JumpType
from ..models.jumper import (
    Cop,
    FederationRating,
    Jumper,
    JumperAttachment,
    Medical,
    MedicalKind,
    Membership,
    OrgEnum,
    TandemRating,
    TandemSystem,
)
from ..models.main import Lineset, Main
from ..models.person import Person
from ..models.reserve import Reserve, ReserveRecertExtension
from ..models.rig import Jurisdiction, RepackEntry, Rig
from ..models.rig_snapshot import RigSnapshot, RigSnapshotRig
from .validator import XMLElement

_NS = SCHEMA_NAMESPACE_V1
_NSMAP = {None: _NS}


def _qn(tag: str) -> str:
    return f"{{{_NS}}}{tag}"


def _sub(parent: XMLElement, tag: str, text: str | None = None) -> XMLElement:
    el = etree.SubElement(parent, _qn(tag))
    if text is not None:
        el.text = text
    return el


# --------------------------------------------------------------------------- #
# Jump
# --------------------------------------------------------------------------- #

def jump_to_element(jump: Jump) -> XMLElement:
    root = etree.Element(_qn("jump"), nsmap=_NSMAP)
    _sub(root, "id", str(jump.id))
    _sub(root, "jump_number", str(jump.jump_number))
    # D4: <title> is optional and precedes <date> in the XSD.
    if jump.title is not None:
        _sub(root, "title", jump.title)
    _sub(root, "date", jump.date.isoformat())
    if jump.time is not None:
        # xs:time requires seconds (HH:MM:SS); round-trips via time.fromisoformat.
        _sub(root, "time", jump.time.isoformat(timespec="seconds"))
    if jump.timezone is not None:
        _sub(root, "timezone", jump.timezone)
    _sub(root, "dropzone", jump.dropzone)
    # D44 / D45: optional dropzone reference + per-jump wear-math
    # overrides. Order matches the XSD sequence — XSD validation
    # would reject misordered emit. ``packed_in_poor_conditions``
    # writes only when explicitly set (None ≡ False in the wear
    # math but is preserved as "absent" in the file so a hand-
    # crafted XML round-trips byte-stable).
    if jump.dropzone_id is not None:
        _sub(root, "dropzone_id", str(jump.dropzone_id))
    # D33 (R.2.2-light): rig_id sits between dropzone_id and
    # packed_in_poor_conditions in the XSD sequence — emit in the
    # same order or XSD validation rejects. The per-jump
    # <environment> override that used to sit between rig_id and
    # packed_in_poor_conditions was removed by D57.
    if jump.rig_id is not None:
        _sub(root, "rig_id", str(jump.rig_id))
    if jump.packed_in_poor_conditions is not None:
        _sub(
            root,
            "packed_in_poor_conditions",
            "true" if jump.packed_in_poor_conditions else "false",
        )
    if jump.aircraft is not None:
        _sub(root, "aircraft", jump.aircraft)
    if jump.discipline is not None:
        _sub(root, "discipline", jump.discipline)
    # D47 / Phase B.4: optional tandem-instructor flag. Same posture as
    # packed_in_poor_conditions — absent stays absent on round-trip,
    # explicit False round-trips as <is_tandem>false</is_tandem>.
    if jump.is_tandem is not None:
        _sub(root, "is_tandem", "true" if jump.is_tandem else "false")
    # ``:g`` strips trailing zero on whole numbers (4000.0 → "4000")
    # and keeps fractional digits as-is (4114.8 → "4114.8") so we
    # don't gratuitously rewrite pre-existing integer-meters jumps
    # into decimal-meters jumps on every save.
    _sub(root, "exit_altitude_m", f"{jump.exit_altitude_m:g}")
    _sub(root, "deployment_altitude_m", f"{jump.deployment_altitude_m:g}")
    if jump.freefall_time_s is not None:
        _sub(root, "freefall_time_s", str(jump.freefall_time_s))
    if jump.notes is not None:
        _sub(root, "notes", jump.notes)
    if jump.attachments:
        atts = _sub(root, "attachments")
        for att in jump.attachments:
            a = _sub(atts, "attachment")
            _sub(a, "filename", att.filename)
            _sub(a, "sha256", att.sha256)
            _sub(a, "size", str(att.size))
            if att.content_type is not None:
                _sub(a, "content_type", att.content_type)
    # D53 additive block. Order matches the XSD sequence — XSD
    # validation rejects misordered emit. Each wrapper / scalar
    # elides when the model field is empty / None so a pre-D53
    # jump.xml round-trips byte-stable.
    if jump.jump_types:
        wrap = _sub(root, "jump_types")
        for jt in jump.jump_types:
            _sub(wrap, "jump_type", jt.value)
    # ``:g`` strips trailing zero on whole numbers (50.0 → "50") and
    # keeps fractional digits as-is — same posture as exit_altitude_m
    # so a pre-existing integer-meters entry doesn't gratuitously
    # rewrite to decimal on every save.
    if jump.landing_distance_m is not None:
        _sub(root, "landing_distance_m", f"{jump.landing_distance_m:g}")
    if jump.packed_by is not None:
        _sub(root, "packed_by", str(jump.packed_by))
    if jump.group_members:
        wrap = _sub(root, "group_members")
        for member_id in jump.group_members:
            _sub(wrap, "member", str(member_id))
    if jump.signature is not None:
        _sub(root, "signature", jump.signature)
    # D32: audit timestamps in D17 canonical form. Written when set
    # on the Jump (service layer stamps them on every create/update);
    # absent otherwise so a hand-crafted file still validates.
    if jump.created_at is not None:
        _sub(root, "created_at", jump.created_at)
    if jump.updated_at is not None:
        _sub(root, "updated_at", jump.updated_at)
    # Q4: write-time provenance. Never read back onto the Jump model,
    # so it has no Pydantic field and does not affect round-trip
    # equality. Useful for bug reports and forensics.
    _sub(root, "generator", GENERATOR_STRING)
    return root


def jump_to_bytes(jump: Jump) -> bytes:
    return etree.tostring(
        jump_to_element(jump),
        xml_declaration=True,
        encoding="UTF-8",
        pretty_print=True,
    )


def _find(el: XMLElement, tag: str) -> XMLElement | None:
    return el.find(_qn(tag))


def _text(el: XMLElement, tag: str) -> str | None:
    child = _find(el, tag)
    return child.text if child is not None else None


def _parse_bool(value: str) -> bool:
    """Parse an ``xs:boolean`` lexical form (``"true"``, ``"false"``, ``"1"``,
    ``"0"``) into a Python ``bool``. Whitespace and case are tolerated."""
    return value.strip().lower() in ("true", "1")


def _read_optional_card_and_notes(el: XMLElement, data: dict) -> None:
    """Pull the two optional fields every D47 credential carries —
    ``card_attachment_id`` (UUID) and ``notes`` (str) — from ``el`` into
    ``data`` if present. Shared by membership / cop / rating / tandem
    rating / medical parsers."""
    if (v := _text(el, "card_attachment_id")) is not None:
        data["card_attachment_id"] = UUID(v)
    if (v := _text(el, "notes")) is not None:
        data["notes"] = v


def element_to_jump(root: XMLElement) -> Jump:
    """Inverse of `jump_to_element`. Expects a `<jump>` element in the v1 namespace."""
    data: dict = {
        "id": UUID(_text(root, "id") or ""),
        "jump_number": int(_text(root, "jump_number") or 0),
        "date": _date.fromisoformat(_text(root, "date") or ""),
        "dropzone": _text(root, "dropzone") or "",
        # ``float()`` parses both integer and decimal strings — XSD
        # is xs:decimal so we round-trip the exact value the writer
        # emitted (e.g. "4114.8") rather than truncating to int.
        "exit_altitude_m": float(_text(root, "exit_altitude_m") or 0),
        "deployment_altitude_m": float(_text(root, "deployment_altitude_m") or 0),
    }
    if (v := _text(root, "title")) is not None:
        data["title"] = v
    if (v := _text(root, "time")) is not None:
        data["time"] = _time.fromisoformat(v)
    if (v := _text(root, "timezone")) is not None:
        data["timezone"] = v
    # D44: optional UUID dropzone reference. Absent on legacy jumps.
    if (v := _text(root, "dropzone_id")) is not None:
        data["dropzone_id"] = UUID(v)
    # D33 (R.2.2-light): optional UUID rig reference. Same posture
    # as dropzone_id — absent on legacy jumps and on jumps logged
    # without a rig selected.
    if (v := _text(root, "rig_id")) is not None:
        data["rig_id"] = UUID(v)
    # D57: the per-jump <environment> override was removed; the
    # element no longer appears in XSD-valid jump.xml files. No
    # parse branch is needed because the field is gone from Jump.
    # D45: optional packing-conditions flag. xs:boolean accepts
    # "true"/"false"/"1"/"0" — normalize to a Python bool. Absent
    # element stays as None on the model so round-trip is exact.
    if (v := _text(root, "packed_in_poor_conditions")) is not None:
        data["packed_in_poor_conditions"] = _parse_bool(v)
    if (v := _text(root, "aircraft")) is not None:
        data["aircraft"] = v
    if (v := _text(root, "discipline")) is not None:
        data["discipline"] = v
    # D47 / Phase B.4: optional tandem-instructor flag. Absent ≡ None
    # on the model so a pre-D47 jump.xml round-trips byte-stable.
    if (v := _text(root, "is_tandem")) is not None:
        data["is_tandem"] = _parse_bool(v)
    if (v := _text(root, "freefall_time_s")) is not None:
        data["freefall_time_s"] = int(v)
    if (v := _text(root, "notes")) is not None:
        data["notes"] = v
    if (v := _text(root, "signature")) is not None:
        data["signature"] = v
    # D32: surface audit timestamps onto the model when present. A
    # pre-D32 jump.xml just doesn't have them; reindex fills them
    # from file mtime with a warning.
    if (v := _text(root, "created_at")) is not None:
        data["created_at"] = v
    if (v := _text(root, "updated_at")) is not None:
        data["updated_at"] = v

    atts_el = _find(root, "attachments")
    if atts_el is not None:
        atts: list[Attachment] = []
        for a in atts_el.findall(_qn("attachment")):
            atts.append(
                Attachment(
                    filename=_text(a, "filename") or "",
                    sha256=_text(a, "sha256") or "",
                    size=int(_text(a, "size") or 0),
                    content_type=_text(a, "content_type"),
                )
            )
        if atts:
            data["attachments"] = atts

    # D53: jump_types wrapper. Absent block ↔ empty list on the
    # model (matches the elision posture in the writer). Pre-D53
    # jump.xml files have no <jump_types>; this branch is a no-op
    # for them, keeping back-compat byte-stable.
    jt_wrap = _find(root, "jump_types")
    if jt_wrap is not None:
        types: list[JumpType] = []
        for jt_el in jt_wrap.findall(_qn("jump_type")):
            if jt_el.text is not None:
                types.append(JumpType(jt_el.text))
        if types:
            data["jump_types"] = types
    if (v := _text(root, "landing_distance_m")) is not None:
        # xs:decimal — same float() parse as exit_altitude_m so
        # integer and fractional values round-trip exactly.
        data["landing_distance_m"] = float(v)
    if (v := _text(root, "packed_by")) is not None:
        data["packed_by"] = UUID(v)
    gm_wrap = _find(root, "group_members")
    if gm_wrap is not None:
        members: list[UUID] = []
        for m_el in gm_wrap.findall(_qn("member")):
            if m_el.text is not None:
                members.append(UUID(m_el.text))
        if members:
            data["group_members"] = members

    return Jump(**data)


# --------------------------------------------------------------------------- #
# Dropzone (D44)
# --------------------------------------------------------------------------- #

def dropzone_to_element(dz: Dropzone) -> XMLElement:
    """Serialize a Dropzone to a v1-namespace ``<dropzone>`` element.

    Order matches the XSD sequence; XSD validation would reject a
    misordered emit. Optional fields (``province``, ``notes``,
    timestamps) are omitted when None so the round-trip is byte-
    stable for hand-crafted files that don't set them.
    """
    root = etree.Element(_qn("dropzone"), nsmap=_NSMAP)
    _sub(root, "id", str(dz.id))
    _sub(root, "name", dz.name)
    _sub(root, "city", dz.city)
    if dz.province is not None:
        _sub(root, "province", dz.province)
    _sub(root, "country", dz.country)
    _sub(root, "environment", dz.environment.value)
    # D44 (added 2026-04-28): elide the wrapper element entirely
    # when the fleet is empty so a hand-crafted XML without the
    # addition round-trips byte-stable. Order in the XSD sequence
    # is environment → aircraft → notes.
    if dz.aircraft:
        ac_el = _sub(root, "aircraft")
        for plane in dz.aircraft:
            p_el = _sub(ac_el, "plane")
            _sub(p_el, "model", plane.model)
            if plane.tail_number is not None:
                _sub(p_el, "tail_number", plane.tail_number)
    if dz.notes is not None:
        _sub(root, "notes", dz.notes)
    # D60: starred default-False is elided so an unstarred DZ
    # serialises without the element — preserves byte-stable
    # round-trip for any pre-D60 hand-crafted dropzone.xml that
    # does not carry <starred>. Emit only the true case. Order
    # in the XSD sequence is notes → starred → created_at.
    if dz.starred:
        _sub(root, "starred", "true")
    if dz.created_at is not None:
        _sub(root, "created_at", dz.created_at)
    if dz.updated_at is not None:
        _sub(root, "updated_at", dz.updated_at)
    # Q4 provenance, same as <jump>. Never read back onto the model.
    _sub(root, "generator", GENERATOR_STRING)
    return root


def dropzone_to_bytes(dz: Dropzone) -> bytes:
    return etree.tostring(
        dropzone_to_element(dz),
        xml_declaration=True,
        encoding="UTF-8",
        pretty_print=True,
    )


def element_to_dropzone(root: XMLElement) -> Dropzone:
    """Inverse of ``dropzone_to_element``. Expects a ``<dropzone>``
    element in the v1 namespace.
    """
    data: dict = {
        "id": UUID(_text(root, "id") or ""),
        "name": _text(root, "name") or "",
        "city": _text(root, "city") or "",
        "country": _text(root, "country") or "",
        "environment": Environment(_text(root, "environment") or ""),
    }
    if (v := _text(root, "province")) is not None:
        data["province"] = v
    # D44 aircraft list (added 2026-04-28). Absent block ↔ empty
    # list on the model.
    ac_el = _find(root, "aircraft")
    if ac_el is not None:
        planes: list[DropzoneAircraft] = []
        for plane_el in ac_el.findall(_qn("plane")):
            planes.append(
                DropzoneAircraft(
                    model=_text(plane_el, "model") or "",
                    tail_number=_text(plane_el, "tail_number"),
                )
            )
        if planes:
            data["aircraft"] = planes
    if (v := _text(root, "notes")) is not None:
        data["notes"] = v
    # D60: parse <starred>. xs:boolean accepts "true"/"false"/"1"/"0";
    # absent ⇒ False (Pydantic default). The service enforces the
    # ≥1-DZ-exactly-one-starred invariant, not the parser — we
    # accept whatever the XSD let through and let set_star /
    # delete_dropzone re-converge on the next mutation. Mirrors
    # the parse posture for <rig><starred>.
    if (v := _text(root, "starred")) is not None:
        data["starred"] = _parse_bool(v)
    if (v := _text(root, "created_at")) is not None:
        data["created_at"] = v
    if (v := _text(root, "updated_at")) is not None:
        data["updated_at"] = v
    return Dropzone(**data)


# --------------------------------------------------------------------------- #
# Person (D54)
# --------------------------------------------------------------------------- #

def person_to_element(p: Person) -> XMLElement:
    """Serialize a Person to a v1-namespace ``<person>`` element.

    Order matches the XSD sequence: id → name → notes? →
    created_at? → updated_at? → generator. Optional fields elide
    when None so a hand-crafted file without them round-trips
    byte-stable.
    """
    root = etree.Element(_qn("person"), nsmap=_NSMAP)
    _sub(root, "id", str(p.id))
    _sub(root, "name", p.name)
    if p.notes is not None:
        _sub(root, "notes", p.notes)
    if p.created_at is not None:
        _sub(root, "created_at", p.created_at)
    if p.updated_at is not None:
        _sub(root, "updated_at", p.updated_at)
    # Q4 provenance, same as <jump> / <dropzone>. Never read back
    # onto the model.
    _sub(root, "generator", GENERATOR_STRING)
    return root


def person_to_bytes(p: Person) -> bytes:
    return etree.tostring(
        person_to_element(p),
        xml_declaration=True,
        encoding="UTF-8",
        pretty_print=True,
    )


def element_to_person(root: XMLElement) -> Person:
    """Inverse of :func:`person_to_element`. Expects ``<person>`` in v1 ns."""
    data: dict = {
        "id": UUID(_text(root, "id") or ""),
        "name": _text(root, "name") or "",
    }
    if (v := _text(root, "notes")) is not None:
        data["notes"] = v
    if (v := _text(root, "created_at")) is not None:
        data["created_at"] = v
    if (v := _text(root, "updated_at")) is not None:
        data["updated_at"] = v
    return Person(**data)


# --------------------------------------------------------------------------- #
# Rig-manager component base (D33, D34)
# --------------------------------------------------------------------------- #
#
# Every concrete component element opens its sequence with the
# ``ComponentBaseFields`` xs:group (id, status, assigned_rig_id,
# notes_log, created_at, updated_at). These two helpers serialize and
# parse that prefix in lockstep with the XSD group order so each kind
# stays in sync without copy-pasting six element emits.

def _emit_component_base(root: XMLElement, c: ComponentBase) -> None:
    """Append the ``ComponentBaseFields`` group's elements to ``root``.

    Order is locked to the XSD group sequence. Optional members
    (``assigned_rig_id``, ``notes_log``, timestamps) are omitted when
    absent so a hand-crafted file without them round-trips byte-stable.
    """
    _sub(root, "id", str(c.id))
    _sub(root, "status", c.status.value)
    if c.assigned_rig_id is not None:
        _sub(root, "assigned_rig_id", str(c.assigned_rig_id))
    if c.notes_log:
        wrap = _sub(root, "notes_log")
        for entry in c.notes_log:
            entry_el = _sub(wrap, "entry")
            _sub(entry_el, "at", entry.at)
            _sub(entry_el, "text", entry.text)
    if c.created_at is not None:
        _sub(root, "created_at", c.created_at)
    if c.updated_at is not None:
        _sub(root, "updated_at", c.updated_at)


def _parse_component_base(root: XMLElement) -> dict:
    """Inverse of :func:`_emit_component_base`.

    Returns a kwargs dict suitable for spreading into any
    :class:`ComponentBase` subclass constructor. The caller adds the
    kind-specific fields on top.
    """
    data: dict = {
        "id": UUID(_text(root, "id") or ""),
        "status": ComponentStatus(_text(root, "status") or ""),
    }
    if (v := _text(root, "assigned_rig_id")) is not None:
        data["assigned_rig_id"] = UUID(v)

    notes_wrap = _find(root, "notes_log")
    if notes_wrap is not None:
        entries: list[NotesLogEntry] = []
        for entry_el in notes_wrap.findall(_qn("entry")):
            entries.append(
                NotesLogEntry(
                    at=_text(entry_el, "at") or "",
                    text=_text(entry_el, "text") or "",
                )
            )
        if entries:
            data["notes_log"] = entries

    if (v := _text(root, "created_at")) is not None:
        data["created_at"] = v
    if (v := _text(root, "updated_at")) is not None:
        data["updated_at"] = v
    return data


# --------------------------------------------------------------------------- #
# Container (D33, D34, R.0.2b)
# --------------------------------------------------------------------------- #

def container_to_element(c: Container) -> XMLElement:
    """Serialize a Container to a v1-namespace ``<container>`` element.

    Order matches the XSD sequence: the ComponentBaseFields group
    first, then container-specific fields. Optional identification
    fields (manufacturer, model, serial, size, DOM) are omitted when
    None so the round-trip is byte-stable for partial-provenance
    used-gear records.
    """
    root = etree.Element(_qn("container"), nsmap=_NSMAP)
    _emit_component_base(root, c)
    if c.manufacturer is not None:
        _sub(root, "manufacturer", c.manufacturer)
    if c.model is not None:
        _sub(root, "model", c.model)
    if c.serial is not None:
        _sub(root, "serial", c.serial)
    if c.size is not None:
        _sub(root, "size", c.size)
    if c.date_of_manufacture is not None:
        _sub(root, "date_of_manufacture", c.date_of_manufacture.isoformat())
    # D35: jump_count_initial is required (defaults to 0). Always emit
    # so the projection layer never has to guess a missing seed.
    _sub(root, "jump_count_initial", str(c.jump_count_initial))
    # Q4 write-time provenance.
    _sub(root, "generator", GENERATOR_STRING)
    return root


def container_to_bytes(c: Container) -> bytes:
    return etree.tostring(
        container_to_element(c),
        xml_declaration=True,
        encoding="UTF-8",
        pretty_print=True,
    )


def element_to_container(root: XMLElement) -> Container:
    """Inverse of :func:`container_to_element`.

    Expects a ``<container>`` element in the v1 namespace.
    """
    data = _parse_component_base(root)
    for tag in ("manufacturer", "model", "serial", "size"):
        v = _text(root, tag)
        if v is not None:
            data[tag] = v
    if (v := _text(root, "date_of_manufacture")) is not None:
        data["date_of_manufacture"] = _date.fromisoformat(v)
    # jump_count_initial is required by the XSD; default to 0 here only
    # to make the helper tolerant of partial elements built by tests.
    if (v := _text(root, "jump_count_initial")) is not None:
        data["jump_count_initial"] = int(v)
    return Container(**data)


# --------------------------------------------------------------------------- #
# AAD (D33, D34, R.0.2c)
# --------------------------------------------------------------------------- #

def aad_to_element(a: AAD) -> XMLElement:
    """Serialize an AAD to a v1-namespace ``<aad>`` element.

    Order matches the XSD sequence: ComponentBaseFields group first,
    then AAD-specific fields. ``is_changeable_mode`` is emitted only
    when set so a hand-crafted file with the field absent (i.e.
    "unknown") round-trips byte-stable; the same posture as
    ``packed_in_poor_conditions`` on Jump.
    """
    root = etree.Element(_qn("aad"), nsmap=_NSMAP)
    _emit_component_base(root, a)
    if a.manufacturer is not None:
        _sub(root, "manufacturer", a.manufacturer)
    if a.model is not None:
        _sub(root, "model", a.model)
    if a.serial is not None:
        _sub(root, "serial", a.serial)
    if a.date_of_manufacture is not None:
        _sub(root, "date_of_manufacture", a.date_of_manufacture.isoformat())
    if a.mode is not None:
        _sub(root, "mode", a.mode)
    if a.is_changeable_mode is not None:
        _sub(
            root,
            "is_changeable_mode",
            "true" if a.is_changeable_mode else "false",
        )
    # D35: both counters always emitted (default 0). Deterministic
    # seed for the projection layer.
    _sub(root, "jump_count_initial", str(a.jump_count_initial))
    _sub(root, "fire_count_initial", str(a.fire_count_initial))
    _sub(root, "generator", GENERATOR_STRING)
    return root


def aad_to_bytes(a: AAD) -> bytes:
    return etree.tostring(
        aad_to_element(a),
        xml_declaration=True,
        encoding="UTF-8",
        pretty_print=True,
    )


def element_to_aad(root: XMLElement) -> AAD:
    """Inverse of :func:`aad_to_element`.

    Expects an ``<aad>`` element in the v1 namespace.
    """
    data = _parse_component_base(root)
    for tag in ("manufacturer", "model", "serial", "mode"):
        v = _text(root, tag)
        if v is not None:
            data[tag] = v
    if (v := _text(root, "date_of_manufacture")) is not None:
        data["date_of_manufacture"] = _date.fromisoformat(v)
    if (v := _text(root, "is_changeable_mode")) is not None:
        # xs:boolean accepts "true"/"false"/"1"/"0".
        data["is_changeable_mode"] = _parse_bool(v)
    if (v := _text(root, "jump_count_initial")) is not None:
        data["jump_count_initial"] = int(v)
    if (v := _text(root, "fire_count_initial")) is not None:
        data["fire_count_initial"] = int(v)
    return AAD(**data)


# --------------------------------------------------------------------------- #
# Reserve (D33, D34, R.0.2d)
# --------------------------------------------------------------------------- #

def reserve_to_element(r: Reserve) -> XMLElement:
    """Serialize a Reserve to a v1-namespace ``<reserve>`` element.

    Order matches the XSD sequence: ComponentBaseFields group first,
    then identification, geometry, manufacturer-spec limits, the two
    D35 counter seeds, then the optional recert-extension log. The
    recert-extensions wrapper is omitted entirely when the list is
    empty (same posture as ``notes_log`` and ``DropzoneAircraft``).
    """
    root = etree.Element(_qn("reserve"), nsmap=_NSMAP)
    _emit_component_base(root, r)
    if r.manufacturer is not None:
        _sub(root, "manufacturer", r.manufacturer)
    if r.model is not None:
        _sub(root, "model", r.model)
    if r.serial is not None:
        _sub(root, "serial", r.serial)
    if r.size_sqft is not None:
        # ``:g`` strips trailing zero on whole numbers (143 → "143")
        # and keeps fractional digits as-is (143.5 → "143.5") so a
        # pre-existing integer-area record does not gratuitously
        # rewrite to decimal on every save. Same posture as
        # ``exit_altitude_m`` on Jump.
        _sub(root, "size_sqft", f"{r.size_sqft:g}")
    if r.date_of_manufacture is not None:
        _sub(root, "date_of_manufacture", r.date_of_manufacture.isoformat())
    if r.repack_limit is not None:
        _sub(root, "repack_limit", str(r.repack_limit))
    if r.ride_limit is not None:
        _sub(root, "ride_limit", str(r.ride_limit))
    # D35: both counters always emitted (default 0) so the projection
    # layer never has to guess a missing seed.
    _sub(root, "repack_count_initial", str(r.repack_count_initial))
    _sub(root, "ride_count_initial", str(r.ride_count_initial))
    if r.recert_extensions:
        wrap = _sub(root, "recert_extensions")
        for ext in r.recert_extensions:
            ext_el = _sub(wrap, "extension")
            _sub(ext_el, "granted_at", ext.granted_at)
            _sub(ext_el, "extends_until", ext.extends_until.isoformat())
            if ext.granted_by is not None:
                _sub(ext_el, "granted_by", ext.granted_by)
            if ext.reason is not None:
                _sub(ext_el, "reason", ext.reason)
    _sub(root, "generator", GENERATOR_STRING)
    return root


def reserve_to_bytes(r: Reserve) -> bytes:
    return etree.tostring(
        reserve_to_element(r),
        xml_declaration=True,
        encoding="UTF-8",
        pretty_print=True,
    )


def element_to_reserve(root: XMLElement) -> Reserve:
    """Inverse of :func:`reserve_to_element`.

    Expects a ``<reserve>`` element in the v1 namespace.
    """
    data = _parse_component_base(root)
    for tag in ("manufacturer", "model", "serial"):
        v = _text(root, tag)
        if v is not None:
            data[tag] = v
    if (v := _text(root, "size_sqft")) is not None:
        data["size_sqft"] = float(v)
    if (v := _text(root, "date_of_manufacture")) is not None:
        data["date_of_manufacture"] = _date.fromisoformat(v)
    if (v := _text(root, "repack_limit")) is not None:
        data["repack_limit"] = int(v)
    if (v := _text(root, "ride_limit")) is not None:
        data["ride_limit"] = int(v)
    if (v := _text(root, "repack_count_initial")) is not None:
        data["repack_count_initial"] = int(v)
    if (v := _text(root, "ride_count_initial")) is not None:
        data["ride_count_initial"] = int(v)
    rec_wrap = _find(root, "recert_extensions")
    if rec_wrap is not None:
        exts: list[ReserveRecertExtension] = []
        for ext_el in rec_wrap.findall(_qn("extension")):
            exts.append(
                ReserveRecertExtension(
                    granted_at=_text(ext_el, "granted_at") or "",
                    extends_until=_date.fromisoformat(
                        _text(ext_el, "extends_until") or ""
                    ),
                    granted_by=_text(ext_el, "granted_by"),
                    reason=_text(ext_el, "reason"),
                )
            )
        if exts:
            data["recert_extensions"] = exts
    return Reserve(**data)


# --------------------------------------------------------------------------- #
# Main (D33, D34, R.0.2e)
# --------------------------------------------------------------------------- #

def _emit_lineset(parent: XMLElement, tag: str, ls: Lineset) -> None:
    """Emit a Lineset under ``parent`` with the given tag.

    Used for both ``<current_lineset>`` and each
    ``<lineset_history>/<lineset>`` entry — the shape is identical
    per D34. ``installed_by`` is omitted when None so a hand-crafted
    file without rigger attribution round-trips byte-stable.
    """
    ls_el = _sub(parent, tag)
    _sub(ls_el, "id", str(ls.id))
    _sub(ls_el, "line_type", ls.line_type)
    # ``:g`` for floats — same posture as exit_altitude_m on Jump.
    _sub(ls_el, "breaking_strength_lb", f"{ls.breaking_strength_lb:g}")
    _sub(ls_el, "install_date", ls.install_date.isoformat())
    if ls.installed_by is not None:
        _sub(ls_el, "installed_by", ls.installed_by)
    # D46: int seed. Was ``consumed_lb_initial`` (decimal) per D35;
    # superseded.
    _sub(ls_el, "jumps_on_lineset_initial", str(ls.jumps_on_lineset_initial))


def _parse_lineset(ls_el: XMLElement) -> Lineset:
    """Inverse of :func:`_emit_lineset`. Caller selects the element."""
    return Lineset(
        id=UUID(_text(ls_el, "id") or ""),
        line_type=_text(ls_el, "line_type") or "",
        breaking_strength_lb=float(_text(ls_el, "breaking_strength_lb") or 0),
        install_date=_date.fromisoformat(_text(ls_el, "install_date") or ""),
        installed_by=_text(ls_el, "installed_by"),
        jumps_on_lineset_initial=int(_text(ls_el, "jumps_on_lineset_initial") or 0),
    )


def main_to_element(m: Main) -> XMLElement:
    """Serialize a Main to a v1-namespace ``<main>`` element.

    Order matches the XSD sequence: ComponentBaseFields group first,
    then identification, geometry, default_environment, the
    jump_count_initial seed, then the optional current_lineset and
    lineset_history wrapper. The history wrapper elides when empty.
    """
    root = etree.Element(_qn("main"), nsmap=_NSMAP)
    _emit_component_base(root, m)
    if m.manufacturer is not None:
        _sub(root, "manufacturer", m.manufacturer)
    if m.model is not None:
        _sub(root, "model", m.model)
    if m.serial is not None:
        _sub(root, "serial", m.serial)
    if m.size_sqft is not None:
        _sub(root, "size_sqft", f"{m.size_sqft:g}")
    if m.date_of_manufacture is not None:
        _sub(root, "date_of_manufacture", m.date_of_manufacture.isoformat())
    if m.default_environment is not None:
        _sub(root, "default_environment", m.default_environment.value)
    # D45 RDS flag: emit only when True. Elide when False so pre-D45-
    # reification main.xml round-trips byte-stable. The XSD order
    # places <has_rds> between <default_environment> and
    # <jump_count_initial>.
    if m.has_rds:
        _sub(root, "has_rds", "true")
    # D35: jump_count_initial always emitted (default 0).
    _sub(root, "jump_count_initial", str(m.jump_count_initial))
    if m.current_lineset is not None:
        _emit_lineset(root, "current_lineset", m.current_lineset)
    if m.lineset_history:
        wrap = _sub(root, "lineset_history")
        for archived in m.lineset_history:
            _emit_lineset(wrap, "lineset", archived)
    _sub(root, "generator", GENERATOR_STRING)
    return root


def main_to_bytes(m: Main) -> bytes:
    return etree.tostring(
        main_to_element(m),
        xml_declaration=True,
        encoding="UTF-8",
        pretty_print=True,
    )


def element_to_main(root: XMLElement) -> Main:
    """Inverse of :func:`main_to_element`.

    Expects a ``<main>`` element in the v1 namespace.
    """
    data = _parse_component_base(root)
    for tag in ("manufacturer", "model", "serial"):
        v = _text(root, tag)
        if v is not None:
            data[tag] = v
    if (v := _text(root, "size_sqft")) is not None:
        data["size_sqft"] = float(v)
    if (v := _text(root, "date_of_manufacture")) is not None:
        data["date_of_manufacture"] = _date.fromisoformat(v)
    if (v := _text(root, "default_environment")) is not None:
        data["default_environment"] = Environment(v)
    # D45 RDS flag. xs:boolean accepts "true"/"false"/"1"/"0";
    # absent ⇒ Main's default of False.
    if (v := _text(root, "has_rds")) is not None:
        data["has_rds"] = _parse_bool(v)
    if (v := _text(root, "jump_count_initial")) is not None:
        data["jump_count_initial"] = int(v)
    cur_el = _find(root, "current_lineset")
    if cur_el is not None:
        data["current_lineset"] = _parse_lineset(cur_el)
    hist_wrap = _find(root, "lineset_history")
    if hist_wrap is not None:
        archived: list[Lineset] = []
        for ls_el in hist_wrap.findall(_qn("lineset")):
            archived.append(_parse_lineset(ls_el))
        if archived:
            data["lineset_history"] = archived
    return Main(**data)


# --------------------------------------------------------------------------- #
# Rig (D33, D37, D38, R.2.0a)
# --------------------------------------------------------------------------- #

def _emit_repack_entry(parent: XMLElement, entry: RepackEntry) -> None:
    """Emit one ``<repack>`` child under ``parent`` (D38).

    Order matches ``RepackEntryType`` in the XSD; ``notes`` elides
    when None so a hand-crafted file without rigger commentary
    round-trips byte-stable.
    """
    r_el = _sub(parent, "repack")
    _sub(r_el, "date", entry.date.isoformat())
    _sub(r_el, "rigger", entry.rigger)
    _sub(r_el, "jurisdiction_seal", entry.jurisdiction_seal.value)
    if entry.notes is not None:
        _sub(r_el, "notes", entry.notes)


def _parse_repack_entry(r_el: XMLElement) -> RepackEntry:
    """Inverse of :func:`_emit_repack_entry`."""
    return RepackEntry(
        date=_date.fromisoformat(_text(r_el, "date") or ""),
        rigger=_text(r_el, "rigger") or "",
        jurisdiction_seal=Jurisdiction(_text(r_el, "jurisdiction_seal") or ""),
        notes=_text(r_el, "notes"),
    )


def rig_to_element(r: Rig) -> XMLElement:
    """Serialize a Rig to a v1-namespace ``<rig>`` element.

    Order matches the XSD sequence: id, nickname, jurisdiction, the
    four current_*_id refs, then the optional repack_history and
    notes_log wrappers, then D32 timestamps. Both wrappers elide
    when their list is empty so a freshly-onboarded rig with no
    repacks and no notes round-trips byte-stable.
    """
    root = etree.Element(_qn("rig"), nsmap=_NSMAP)
    _sub(root, "id", str(r.id))
    _sub(root, "nickname", r.nickname)
    _sub(root, "jurisdiction", r.jurisdiction.value)
    _sub(root, "current_main_id", str(r.current_main_id))
    _sub(root, "current_reserve_id", str(r.current_reserve_id))
    _sub(root, "current_aad_id", str(r.current_aad_id))
    _sub(root, "current_container_id", str(r.current_container_id))
    # D58: emit <starred>true</starred> only when set; elide when
    # false so unstarred rig.xml stays compact (and pre-D58 rigs
    # round-trip byte-stable).
    if r.starred:
        _sub(root, "starred", "true")
    # D59: emit <display_order> when set. Pre-D59 rigs serialize
    # with the element absent; list_rigs sorts them after rigs
    # that do carry an explicit order via the tiebreaker chain.
    if r.display_order is not None:
        _sub(root, "display_order", str(r.display_order))
    if r.repack_history:
        wrap = _sub(root, "repack_history")
        for entry in r.repack_history:
            _emit_repack_entry(wrap, entry)
    if r.notes_log:
        wrap = _sub(root, "notes_log")
        for note in r.notes_log:
            entry_el = _sub(wrap, "entry")
            _sub(entry_el, "at", note.at)
            _sub(entry_el, "text", note.text)
    if r.created_at is not None:
        _sub(root, "created_at", r.created_at)
    if r.updated_at is not None:
        _sub(root, "updated_at", r.updated_at)
    _sub(root, "generator", GENERATOR_STRING)
    return root


def rig_to_bytes(r: Rig) -> bytes:
    return etree.tostring(
        rig_to_element(r),
        xml_declaration=True,
        encoding="UTF-8",
        pretty_print=True,
    )


def element_to_rig(root: XMLElement) -> Rig:
    """Inverse of :func:`rig_to_element`. Expects ``<rig>`` in v1 ns."""
    data: dict = {
        "id": UUID(_text(root, "id") or ""),
        "nickname": _text(root, "nickname") or "",
        "jurisdiction": Jurisdiction(_text(root, "jurisdiction") or ""),
        "current_main_id": UUID(_text(root, "current_main_id") or ""),
        "current_reserve_id": UUID(_text(root, "current_reserve_id") or ""),
        "current_aad_id": UUID(_text(root, "current_aad_id") or ""),
        "current_container_id": UUID(_text(root, "current_container_id") or ""),
    }
    # D58: parse <starred>. xs:boolean accepts "true"/"false"/"1"/"0";
    # absent ⇒ False (pydantic default). The model's invariant is
    # service-enforced, not parser-enforced, so we accept whatever
    # the XSD validation passed through.
    if (v := _text(root, "starred")) is not None:
        data["starred"] = _parse_bool(v)
    # D59: parse <display_order>. Absent ⇒ None (pre-D59 legacy);
    # the service guarantees every newly-written rig has a value
    # but list_rigs handles the legacy case via its tiebreaker.
    if (v := _text(root, "display_order")) is not None:
        data["display_order"] = int(v)
    rh_wrap = _find(root, "repack_history")
    if rh_wrap is not None:
        history: list[RepackEntry] = []
        for r_el in rh_wrap.findall(_qn("repack")):
            history.append(_parse_repack_entry(r_el))
        if history:
            data["repack_history"] = history
    notes_wrap = _find(root, "notes_log")
    if notes_wrap is not None:
        notes: list[NotesLogEntry] = []
        for entry_el in notes_wrap.findall(_qn("entry")):
            notes.append(
                NotesLogEntry(
                    at=_text(entry_el, "at") or "",
                    text=_text(entry_el, "text") or "",
                )
            )
        if notes:
            data["notes_log"] = notes
    if (v := _text(root, "created_at")) is not None:
        data["created_at"] = v
    if (v := _text(root, "updated_at")) is not None:
        data["updated_at"] = v
    return Rig(**data)


# --------------------------------------------------------------------------- #
# Jumper (D33, R.2.0a)
# --------------------------------------------------------------------------- #

# --- D47 sub-model helpers (Phase B.4) ---------------------------------- #
#
# Each helper writes one credential / attachment as a child of the
# given parent collection element (``<memberships>``, ``<cops>``, …).
# The writers emit fields in the order the XSD's complex types declare
# them — XSD validation rejects misordered emit, so the order is
# load-bearing. Each writer mirrors a corresponding ``_element_to_*``
# parser; round-trip is byte-stable for any valid sub-model.

def _membership_to_element(parent: XMLElement, m: Membership) -> XMLElement:
    el = _sub(parent, "membership")
    _sub(el, "id", str(m.id))
    _sub(el, "org", m.org.value)
    if m.org_other is not None:
        _sub(el, "org_other", m.org_other)
    _sub(el, "member_number", m.member_number)
    _sub(el, "expiry_date", m.expiry_date.isoformat())
    if m.card_attachment_id is not None:
        _sub(el, "card_attachment_id", str(m.card_attachment_id))
    if m.notes is not None:
        _sub(el, "notes", m.notes)
    return el


def _element_to_membership(el: XMLElement) -> Membership:
    data: dict = {
        "id": UUID(_text(el, "id") or ""),
        "org": OrgEnum(_text(el, "org") or ""),
        "member_number": _text(el, "member_number") or "",
        "expiry_date": _date.fromisoformat(_text(el, "expiry_date") or ""),
    }
    if (v := _text(el, "org_other")) is not None:
        data["org_other"] = v
    _read_optional_card_and_notes(el, data)
    return Membership(**data)


def _cop_to_element(parent: XMLElement, c: Cop) -> XMLElement:
    el = _sub(parent, "cop")
    _sub(el, "id", str(c.id))
    _sub(el, "org", c.org.value)
    if c.org_other is not None:
        _sub(el, "org_other", c.org_other)
    _sub(el, "level", c.level)
    _sub(el, "issued_date", c.issued_date.isoformat())
    if c.card_attachment_id is not None:
        _sub(el, "card_attachment_id", str(c.card_attachment_id))
    if c.notes is not None:
        _sub(el, "notes", c.notes)
    return el


def _element_to_cop(el: XMLElement) -> Cop:
    data: dict = {
        "id": UUID(_text(el, "id") or ""),
        "org": OrgEnum(_text(el, "org") or ""),
        "level": _text(el, "level") or "",
        "issued_date": _date.fromisoformat(_text(el, "issued_date") or ""),
    }
    if (v := _text(el, "org_other")) is not None:
        data["org_other"] = v
    _read_optional_card_and_notes(el, data)
    return Cop(**data)


def _federation_rating_to_element(
    parent: XMLElement, r: FederationRating
) -> XMLElement:
    el = _sub(parent, "rating")
    _sub(el, "id", str(r.id))
    _sub(el, "org", r.org.value)
    if r.org_other is not None:
        _sub(el, "org_other", r.org_other)
    _sub(el, "code", r.code)
    _sub(el, "expiry_date", r.expiry_date.isoformat())
    if r.card_attachment_id is not None:
        _sub(el, "card_attachment_id", str(r.card_attachment_id))
    if r.notes is not None:
        _sub(el, "notes", r.notes)
    return el


def _element_to_federation_rating(el: XMLElement) -> FederationRating:
    data: dict = {
        "id": UUID(_text(el, "id") or ""),
        "org": OrgEnum(_text(el, "org") or ""),
        "code": _text(el, "code") or "",
        "expiry_date": _date.fromisoformat(_text(el, "expiry_date") or ""),
    }
    if (v := _text(el, "org_other")) is not None:
        data["org_other"] = v
    _read_optional_card_and_notes(el, data)
    return FederationRating(**data)


def _tandem_rating_to_element(
    parent: XMLElement, t: TandemRating
) -> XMLElement:
    el = _sub(parent, "tandem_rating")
    _sub(el, "id", str(t.id))
    _sub(el, "system", t.system.value)
    if t.system_other is not None:
        _sub(el, "system_other", t.system_other)
    _sub(el, "expiry_date", t.expiry_date.isoformat())
    if t.card_attachment_id is not None:
        _sub(el, "card_attachment_id", str(t.card_attachment_id))
    if t.currency_reset_at is not None:
        _sub(el, "currency_reset_at", t.currency_reset_at.isoformat())
    if t.notes is not None:
        _sub(el, "notes", t.notes)
    return el


def _element_to_tandem_rating(el: XMLElement) -> TandemRating:
    data: dict = {
        "id": UUID(_text(el, "id") or ""),
        "system": TandemSystem(_text(el, "system") or ""),
        "expiry_date": _date.fromisoformat(_text(el, "expiry_date") or ""),
    }
    if (v := _text(el, "system_other")) is not None:
        data["system_other"] = v
    if (v := _text(el, "currency_reset_at")) is not None:
        data["currency_reset_at"] = _date.fromisoformat(v)
    _read_optional_card_and_notes(el, data)
    return TandemRating(**data)


def _medical_to_element(parent: XMLElement, m: Medical) -> XMLElement:
    el = _sub(parent, "medical")
    _sub(el, "id", str(m.id))
    _sub(el, "kind", m.kind.value)
    _sub(el, "issuing_authority", m.issuing_authority)
    _sub(el, "expiry_date", m.expiry_date.isoformat())
    if m.card_attachment_id is not None:
        _sub(el, "card_attachment_id", str(m.card_attachment_id))
    if m.notes is not None:
        _sub(el, "notes", m.notes)
    return el


def _element_to_medical(el: XMLElement) -> Medical:
    data: dict = {
        "id": UUID(_text(el, "id") or ""),
        "kind": MedicalKind(_text(el, "kind") or ""),
        "issuing_authority": _text(el, "issuing_authority") or "",
        "expiry_date": _date.fromisoformat(_text(el, "expiry_date") or ""),
    }
    _read_optional_card_and_notes(el, data)
    return Medical(**data)


def _jumper_attachment_to_element(
    parent: XMLElement, a: JumperAttachment
) -> XMLElement:
    """Order matches AttachmentType: filename → sha256 → size →
    optional content_type → optional id. The id is required on the
    Pydantic model but the XSD permits its omission (shared with jump
    attachments); we always emit it for jumper attachments."""
    el = _sub(parent, "attachment")
    _sub(el, "filename", a.filename)
    _sub(el, "sha256", a.sha256)
    _sub(el, "size", str(a.size))
    if a.content_type is not None:
        _sub(el, "content_type", a.content_type)
    _sub(el, "id", str(a.id))
    return el


def _element_to_jumper_attachment(el: XMLElement) -> JumperAttachment:
    data: dict = {
        "filename": _text(el, "filename") or "",
        "sha256": _text(el, "sha256") or "",
        "size": int(_text(el, "size") or 0),
        "id": UUID(_text(el, "id") or ""),
    }
    if (v := _text(el, "content_type")) is not None:
        data["content_type"] = v
    return JumperAttachment(**data)


# --- jumper_to_element / element_to_jumper ----------------------------- #

def jumper_to_element(j: Jumper) -> XMLElement:
    """Serialize a Jumper to a v1-namespace ``<jumper>`` element.

    Order matches the XSD sequence:
        id → name? → exit_weight_lb → exit_weight_updated_at?
        → memberships? → cops? → ratings? → tandem_ratings?
        → medicals? → attachments?
        → created_at? → updated_at? → generator

    Each of the six D47 collection wrappers (memberships … attachments)
    elides when its list is empty so a freshly-created jumper with no
    credentials produces a compact file. ``exit_weight_lb`` uses ``:g``
    so an integer value (200.0) emits as ``200``, matching the posture
    on Jump.exit_altitude_m and Reserve.size_sqft.
    """
    root = etree.Element(_qn("jumper"), nsmap=_NSMAP)
    _sub(root, "id", str(j.id))
    if j.name is not None:
        _sub(root, "name", j.name)
    _sub(root, "exit_weight_lb", f"{j.exit_weight_lb:g}")
    if j.exit_weight_updated_at is not None:
        _sub(root, "exit_weight_updated_at", j.exit_weight_updated_at.isoformat())
    if j.memberships:
        wrapper = _sub(root, "memberships")
        for m in j.memberships:
            _membership_to_element(wrapper, m)
    if j.cops:
        wrapper = _sub(root, "cops")
        for c in j.cops:
            _cop_to_element(wrapper, c)
    if j.ratings:
        wrapper = _sub(root, "ratings")
        for r in j.ratings:
            _federation_rating_to_element(wrapper, r)
    if j.tandem_ratings:
        wrapper = _sub(root, "tandem_ratings")
        for t in j.tandem_ratings:
            _tandem_rating_to_element(wrapper, t)
    if j.medicals:
        wrapper = _sub(root, "medicals")
        for med in j.medicals:
            _medical_to_element(wrapper, med)
    if j.attachments:
        wrapper = _sub(root, "attachments")
        for a in j.attachments:
            _jumper_attachment_to_element(wrapper, a)
    if j.created_at is not None:
        _sub(root, "created_at", j.created_at)
    if j.updated_at is not None:
        _sub(root, "updated_at", j.updated_at)
    _sub(root, "generator", GENERATOR_STRING)
    return root


def jumper_to_bytes(j: Jumper) -> bytes:
    return etree.tostring(
        jumper_to_element(j),
        xml_declaration=True,
        encoding="UTF-8",
        pretty_print=True,
    )


def element_to_jumper(root: XMLElement) -> Jumper:
    """Inverse of :func:`jumper_to_element`. Expects ``<jumper>`` in v1 ns."""
    data: dict = {
        "id": UUID(_text(root, "id") or ""),
        "exit_weight_lb": float(_text(root, "exit_weight_lb") or 0),
    }
    if (v := _text(root, "name")) is not None:
        data["name"] = v
    if (v := _text(root, "exit_weight_updated_at")) is not None:
        data["exit_weight_updated_at"] = _date.fromisoformat(v)

    # D47 collections — each wrapper is optional and may also be
    # present with zero entries (tests cover the empty-wrapper case).
    if (wrapper := _find(root, "memberships")) is not None:
        items = [_element_to_membership(c) for c in wrapper.findall(_qn("membership"))]
        if items:
            data["memberships"] = items
    if (wrapper := _find(root, "cops")) is not None:
        items = [_element_to_cop(c) for c in wrapper.findall(_qn("cop"))]
        if items:
            data["cops"] = items
    if (wrapper := _find(root, "ratings")) is not None:
        items = [
            _element_to_federation_rating(c)
            for c in wrapper.findall(_qn("rating"))
        ]
        if items:
            data["ratings"] = items
    if (wrapper := _find(root, "tandem_ratings")) is not None:
        items = [
            _element_to_tandem_rating(c)
            for c in wrapper.findall(_qn("tandem_rating"))
        ]
        if items:
            data["tandem_ratings"] = items
    if (wrapper := _find(root, "medicals")) is not None:
        items = [_element_to_medical(c) for c in wrapper.findall(_qn("medical"))]
        if items:
            data["medicals"] = items
    if (wrapper := _find(root, "attachments")) is not None:
        items = [
            _element_to_jumper_attachment(c)
            for c in wrapper.findall(_qn("attachment"))
        ]
        if items:
            data["attachments"] = items

    if (v := _text(root, "created_at")) is not None:
        data["created_at"] = v
    if (v := _text(root, "updated_at")) is not None:
        data["updated_at"] = v
    return Jumper(**data)


# --------------------------------------------------------------------------- #
# Rig snapshot (D36, R.2.1)
# --------------------------------------------------------------------------- #

def rig_snapshot_to_element(s: RigSnapshot) -> XMLElement:
    """Serialize a :class:`RigSnapshot` to a v1-namespace ``<rig_snapshot>``.

    Order matches the XSD sequence: snapshot_at → nested rig
    identity → main → reserve → aad → container → jumper →
    generator. Each component / jumper child is built by reusing
    the existing per-kind serializer (``main_to_element`` etc.)
    and reparenting the result so the field order and elision
    rules stay identical to the live shape.

    Per D36 the snapshot writer should normally pass a Main with
    ``lineset_history=[]``; this helper does NOT enforce that —
    if the caller supplies a Main with history, it round-trips
    through. The contract is documented on :class:`RigSnapshot`.
    """
    root = etree.Element(_qn("rig_snapshot"), nsmap=_NSMAP)
    _sub(root, "snapshot_at", s.snapshot_at)
    rig_el = _sub(root, "rig")
    _sub(rig_el, "id", str(s.rig.id))
    _sub(rig_el, "nickname", s.rig.nickname)
    _sub(rig_el, "jurisdiction", s.rig.jurisdiction.value)
    if s.rig.last_repack_date is not None:
        _sub(rig_el, "last_repack_date", s.rig.last_repack_date.isoformat())
    # Reuse the per-kind serializers. Each returns a top-level
    # <main>/<reserve>/etc. element in the v1 namespace; appending
    # it as a child of <rig_snapshot> uses the same namespace so
    # lxml doesn't re-declare it on the child.
    root.append(main_to_element(s.main))
    root.append(reserve_to_element(s.reserve))
    root.append(aad_to_element(s.aad))
    root.append(container_to_element(s.container))
    root.append(jumper_to_element(s.jumper))
    _sub(root, "generator", GENERATOR_STRING)
    return root


def rig_snapshot_to_bytes(s: RigSnapshot) -> bytes:
    return etree.tostring(
        rig_snapshot_to_element(s),
        xml_declaration=True,
        encoding="UTF-8",
        pretty_print=True,
    )


def element_to_rig_snapshot(root: XMLElement) -> RigSnapshot:
    """Inverse of :func:`rig_snapshot_to_element`. Expects ``<rig_snapshot>``
    in the v1 namespace.
    """
    rig_el = _find(root, "rig")
    if rig_el is None:
        raise ValueError("rig_snapshot: <rig> element is required")
    rig_data: dict = {
        "id": UUID(_text(rig_el, "id") or ""),
        "nickname": _text(rig_el, "nickname") or "",
        "jurisdiction": Jurisdiction(_text(rig_el, "jurisdiction") or ""),
    }
    if (v := _text(rig_el, "last_repack_date")) is not None:
        rig_data["last_repack_date"] = _date.fromisoformat(v)
    rig_snapshot_rig = RigSnapshotRig(**rig_data)

    main_el = _find(root, "main")
    reserve_el = _find(root, "reserve")
    aad_el = _find(root, "aad")
    container_el = _find(root, "container")
    jumper_el = _find(root, "jumper")
    if any(
        el is None
        for el in (main_el, reserve_el, aad_el, container_el, jumper_el)
    ):
        raise ValueError(
            "rig_snapshot: missing one of <main>, <reserve>, <aad>, "
            "<container>, <jumper>"
        )

    return RigSnapshot(
        snapshot_at=_text(root, "snapshot_at") or "",
        rig=rig_snapshot_rig,
        main=element_to_main(main_el),
        reserve=element_to_reserve(reserve_el),
        aad=element_to_aad(aad_el),
        container=element_to_container(container_el),
        jumper=element_to_jumper(jumper_el),
    )
