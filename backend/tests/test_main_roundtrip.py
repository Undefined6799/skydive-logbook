"""End-to-end XML round-trip + XSD validation for Main (R.0.2e).

Pipeline mirrors the Container / AAD / Reserve round-trips:
  Main → serialize → parse → XSD-validate → Main  ==  original

Pin Main-specific shape:
  * Lineset is nested (current + history), never standalone.
  * current_lineset is optional — None means "not yet lined."
  * lineset_history wrapper elides when empty.
  * Lineset shape is identical between current and history per D34.
  * size_sqft :g formatting (same as Reserve / exit_altitude_m).
  * default_environment is a single Environment value (renamed
    from D33's "default_environment_flags" on 2026-04-28; the
    "flags" suffix had been misleading).
  * jump_count_initial always emits (D35 deterministic seed).
  * Lineset id is a stable UUID — preserved across round-trips so
    R.2's rig-snapshot can pin to it (D36).
"""
from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from backend.models._component_base import ComponentStatus, NotesLogEntry
from backend.models.dropzone import Environment
from backend.models.main import Lineset, Main
from backend.xml.serialize import (
    element_to_main,
    main_to_bytes,
    main_to_element,
)
from backend.xml.validator import parse, validate


def _sample_lineset(**overrides) -> Lineset:
    """Convenience builder. Fills required fields with defensible
    defaults so tests can override only what they're exercising."""
    base = {
        "line_type": "Vectran V750",
        "breaking_strength_lb": 750.0,
        "install_date": date(2025, 1, 15),
        "installed_by": "Master Rigger A. Smith",
        # D46: was consumed_lb_initial: float (lb).
        "jumps_on_lineset_initial": 0,
    }
    base.update(overrides)
    return Lineset(**base)


class TestMainRoundTrip:
    def test_minimal_main_roundtrips(self):
        # Bare-minimum onboarding: just the universal id + status +
        # zero jump_count_initial. No lineset, no history.
        original = Main()
        raw = main_to_bytes(original)
        element = parse(raw)
        validate(element)
        restored = element_to_main(element)
        assert restored == original

    def test_full_field_set_roundtrips(self):
        original = Main(
            id=uuid4(),
            status=ComponentStatus.ACTIVE,
            assigned_rig_id=uuid4(),
            notes_log=[
                NotesLogEntry(
                    at="2026-04-28T14:30:00.000Z",
                    text="Bought used; 850 prior jumps recorded",
                ),
            ],
            manufacturer="Performance Designs",
            model="Sabre 2",
            serial="S2-987654",
            size_sqft=170.0,
            date_of_manufacture=date(2018, 6, 15),
            default_environment=Environment.DUST_SAND_SALT,
            has_rds=True,
            jump_count_initial=850,
            current_lineset=_sample_lineset(
                id=uuid4(),
                line_type="HMA 500",
                breaking_strength_lb=500.0,
                jumps_on_lineset_initial=0,
            ),
            lineset_history=[
                _sample_lineset(
                    id=uuid4(),
                    line_type="Vectran V750",
                    breaking_strength_lb=750.0,
                    install_date=date(2020, 3, 1),
                    jumps_on_lineset_initial=600,
                ),
                _sample_lineset(
                    id=uuid4(),
                    line_type="HMA 500",
                    breaking_strength_lb=500.0,
                    install_date=date(2023, 9, 1),
                    jumps_on_lineset_initial=350,
                ),
            ],
            created_at="2026-04-28T14:30:00.000Z",
            updated_at="2026-04-28T14:30:00.000Z",
        )
        element = main_to_element(original)
        validate(element)
        assert element_to_main(element) == original

    def test_optional_identification_fields_elide_when_none(self):
        original = Main()
        raw = main_to_bytes(original)
        for absent in (
            b"<manufacturer>",
            b"<model>",
            b"<serial>",
            b"<size_sqft>",
            b"<date_of_manufacture>",
            b"<default_environment>",
            # D45: has_rds defaults to False and elides — keeps
            # pre-reification main.xml byte-stable.
            b"<has_rds>",
            b"<current_lineset>",
            b"<lineset_history>",
        ):
            assert absent not in raw, f"unexpected element {absent!r} in output"

    def test_size_sqft_integer_value_emits_without_decimal(self):
        original = Main(size_sqft=170.0)
        raw = main_to_bytes(original)
        assert b"<size_sqft>170</size_sqft>" in raw
        assert b"<size_sqft>170.0</size_sqft>" not in raw

    def test_size_sqft_fractional_preserved(self):
        original = Main(size_sqft=149.5)
        raw = main_to_bytes(original)
        assert b"<size_sqft>149.5</size_sqft>" in raw
        restored = element_to_main(parse(raw))
        assert restored.size_sqft == 149.5

    def test_default_environment_each_value_validates(self):
        # All three D45 values must pass XSD validation when set as
        # the default. Field is a single Environment value, not a
        # bit set — the rename from "default_environment_flags"
        # (D33 wording, 2026-04-28) reflects that.
        for env in Environment:
            m = Main(default_environment=env)
            element = main_to_element(m)
            validate(element)

    def test_jump_count_initial_zero_still_emits(self):
        original = Main()
        raw = main_to_bytes(original)
        assert b"<jump_count_initial>0</jump_count_initial>" in raw

    def test_used_gear_starting_count_roundtrips(self):
        original = Main(jump_count_initial=850)
        element = main_to_element(original)
        validate(element)
        restored = element_to_main(element)
        assert restored.jump_count_initial == 850

    def test_status_each_value_validates(self):
        for s in ComponentStatus:
            m = Main(status=s)
            element = main_to_element(m)
            validate(element)


class TestHasRdsRoundTrip:
    """D45 RDS flag round-trip. The element is optional in the XSD and
    elides when False so pre-D45-reification main.xml stays byte-
    stable. starred-style: emit ``<has_rds>true</has_rds>`` only on
    True; parse ``False`` when absent.

    The +0.15 lb wear-math contribution this flag enables is R.4
    territory; here we only pin the data plumbing.
    """

    def test_default_false_elides_element(self):
        # A freshly-constructed Main has has_rds=False and must not
        # emit the element at all — the XSD's minOccurs="0" lets it
        # be absent, and that's how unset mains stay byte-stable.
        original = Main()
        raw = main_to_bytes(original)
        assert b"<has_rds>" not in raw

    def test_true_roundtrips(self):
        original = Main(has_rds=True)
        element = main_to_element(original)
        validate(element)
        restored = element_to_main(element)
        assert restored.has_rds is True

    def test_explicit_false_still_elides(self):
        # Distinct from "absent" in pydantic-land (both ⇒ False), but
        # the serializer must treat them the same so the XML stays
        # compact. This pin protects against a future refactor that
        # might emit ``<has_rds>false</has_rds>`` and accidentally
        # break byte-stable round-trip with pre-D45 files.
        original = Main(has_rds=False)
        raw = main_to_bytes(original)
        assert b"<has_rds>" not in raw

    def test_absent_element_parses_as_false(self):
        # Pre-D45-reification main.xml: written before this field
        # existed. The parser must default has_rds to False so old
        # mains don't trip on the missing element.
        without_rds = Main()
        raw = main_to_bytes(without_rds)
        assert b"<has_rds>" not in raw
        restored = element_to_main(parse(raw))
        assert restored.has_rds is False

    def test_xsd_accepts_boolean_lexical_forms(self):
        # xs:boolean accepts "true"/"false"/"1"/"0"; the parser
        # normalizes "1"/"true" to Python True. Pin the lexical-
        # forms branch in element_to_main.
        # Build XML by hand to bypass our serializer (which always
        # emits "true").
        from lxml import etree as _et

        from backend.xml.validator import SCHEMA_NAMESPACE_V1

        # Round-trip a Main that emits the element, then mutate to
        # "1" so the parser sees the alternate lexical form.
        original = Main(has_rds=True)
        raw = main_to_bytes(original)
        # The parser is hardened (D2) — go through it, not the
        # raw lxml parser, so this exercises the production path.
        root = parse(raw)
        # Mutate <has_rds>true</has_rds> → <has_rds>1</has_rds>.
        ns_qn = f"{{{SCHEMA_NAMESPACE_V1}}}has_rds"
        has_rds_el = root.find(ns_qn)
        assert has_rds_el is not None
        has_rds_el.text = "1"
        # Re-validate to confirm "1" is XSD-valid, then re-parse.
        validate(root)
        # Round-trip the mutated tree back through serialize bytes
        # and the parser to exercise element_to_main directly.
        mutated_bytes = _et.tostring(root, xml_declaration=True, encoding="UTF-8")
        restored = element_to_main(parse(mutated_bytes))
        assert restored.has_rds is True


class TestLinesetRoundTrip:
    def test_current_only_no_history(self):
        ls = _sample_lineset(id=uuid4())
        original = Main(current_lineset=ls)
        element = main_to_element(original)
        validate(element)
        restored = element_to_main(element)
        assert restored.current_lineset == ls
        assert restored.lineset_history == []

    def test_history_preserves_order(self):
        # Append-only log: order is load-bearing. Reordering would
        # silently rewrite which lineset was on the main during which
        # time period.
        ids = [uuid4() for _ in range(3)]
        history = [
            _sample_lineset(id=ids[0], install_date=date(2020, 1, 1)),
            _sample_lineset(id=ids[1], install_date=date(2022, 1, 1)),
            _sample_lineset(id=ids[2], install_date=date(2024, 1, 1)),
        ]
        original = Main(lineset_history=history)
        element = main_to_element(original)
        validate(element)
        restored = element_to_main(element)
        assert [ls.id for ls in restored.lineset_history] == ids

    def test_history_and_current_coexist(self):
        history = [
            _sample_lineset(
                id=uuid4(),
                line_type="Vectran V750",
                jumps_on_lineset_initial=500,
                install_date=date(2020, 1, 1),
            ),
        ]
        current = _sample_lineset(
            id=uuid4(),
            line_type="HMA 500",
            jumps_on_lineset_initial=0,
            install_date=date(2024, 6, 1),
        )
        original = Main(current_lineset=current, lineset_history=history)
        element = main_to_element(original)
        validate(element)
        restored = element_to_main(element)
        assert restored.current_lineset == current
        assert restored.lineset_history == history

    def test_lineset_id_is_uuid_and_stable(self):
        # R.2+'s rig-snapshot.xml pins to the lineset id (D36); the
        # round-trip must preserve it byte-stable.
        fixed_id = UUID("12345678-1234-4234-8234-123456789abc")
        ls = _sample_lineset(id=fixed_id)
        original = Main(current_lineset=ls)
        raw = main_to_bytes(original)
        assert str(fixed_id).encode() in raw
        restored = element_to_main(parse(raw))
        assert restored.current_lineset is not None
        assert restored.current_lineset.id == fixed_id

    def test_installed_by_optional(self):
        ls = _sample_lineset(id=uuid4(), installed_by=None)
        original = Main(current_lineset=ls)
        raw = main_to_bytes(original)
        assert b"<installed_by>" not in raw
        restored = element_to_main(parse(raw))
        assert restored.current_lineset is not None
        assert restored.current_lineset.installed_by is None

    def test_jumps_on_lineset_initial_zero_emits(self):
        # Same posture as the kind-counter seeds — D35 / D46 want a
        # deterministic seed; the zero case must not be elided.
        ls = _sample_lineset(id=uuid4(), jumps_on_lineset_initial=0)
        original = Main(current_lineset=ls)
        raw = main_to_bytes(original)
        assert b"<jumps_on_lineset_initial>0</jumps_on_lineset_initial>" in raw

    def test_jumps_on_lineset_initial_round_trips_int(self):
        # Per D46 the seed is xs:nonNegativeInteger; floats must
        # not survive the round-trip. This guards against a future
        # well-meaning change typing the field as float again.
        ls = _sample_lineset(id=uuid4(), jumps_on_lineset_initial=123)
        original = Main(current_lineset=ls)
        element = main_to_element(original)
        validate(element)
        restored = element_to_main(element)
        assert restored.current_lineset is not None
        assert restored.current_lineset.jumps_on_lineset_initial == 123
        assert isinstance(restored.current_lineset.jumps_on_lineset_initial, int)

    def test_empty_lineset_history_elides_wrapper(self):
        original = Main(lineset_history=[])
        raw = main_to_bytes(original)
        assert b"<lineset_history>" not in raw


class TestMainXSDContract:
    def test_missing_install_date_fails_xsd(self):
        from backend.models.common import SCHEMA_NAMESPACE_V1
        from backend.xml.validator import XMLError, parse, validate
        ns = SCHEMA_NAMESPACE_V1
        # ``install_date`` is required on LinesetType. Build a XML
        # missing it directly — Pydantic would otherwise catch this.
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<main xmlns="{ns}">
  <id>11111111-1111-4111-8111-111111111111</id>
  <status>active</status>
  <jump_count_initial>0</jump_count_initial>
  <current_lineset>
    <id>22222222-2222-4222-8222-222222222222</id>
    <line_type>Vectran V750</line_type>
    <breaking_strength_lb>750</breaking_strength_lb>
    <jumps_on_lineset_initial>0</jumps_on_lineset_initial>
  </current_lineset>
</main>
""".encode()
        element = parse(xml)
        try:
            validate(element)
        except XMLError:
            return
        raise AssertionError("XSD did not reject lineset missing install_date")

    def test_invalid_default_environment_fails_xsd(self):
        from backend.models.common import SCHEMA_NAMESPACE_V1
        from backend.xml.validator import XMLError, parse, validate
        ns = SCHEMA_NAMESPACE_V1
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<main xmlns="{ns}">
  <id>11111111-1111-4111-8111-111111111111</id>
  <status>active</status>
  <default_environment>volcanic</default_environment>
  <jump_count_initial>0</jump_count_initial>
</main>
""".encode()
        element = parse(xml)
        try:
            validate(element)
        except XMLError:
            return
        raise AssertionError("XSD did not reject unknown environment value")
