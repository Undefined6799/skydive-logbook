"""Tests for D29 logbook-folder bootstrap.

Locks down the contract every caller relies on:

  * Fresh folder: every expected file and subdirectory appears, XSD
    bytes match the app-shipped source, README bytes match the template.
  * Pre-existing README: preserved verbatim (user edits survive).
  * Pre-existing XSD: overwritten with shipped bytes (app ownership).
  * Re-running is a no-op in observable state (idempotent).
  * Subdirectories already present: no error.
  * Deep missing parent path: created via ``parents=True``.
  * Root pointing at a file: ``OSError`` propagates.
"""
from __future__ import annotations

from importlib import resources
from pathlib import Path

import pytest

from backend.storage.bootstrap import bootstrap_logbook

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_SHIPPED_XSD_NAMES = sorted(
    p.name
    for p in resources.files("backend.xml.schema").iterdir()
    if p.name.startswith("SCHEMA.v") and p.name.endswith(".xsd")
)


def _shipped_xsd_bytes(name: str) -> bytes:
    return resources.files("backend.xml.schema").joinpath(name).read_bytes()


def _shipped_readme_bytes() -> bytes:
    return (
        resources.files("backend.storage.templates")
        .joinpath("LOGBOOK_README.md")
        .read_bytes()
    )


# --------------------------------------------------------------------------- #
# Happy path: fresh folder
# --------------------------------------------------------------------------- #

class TestFreshFolder:
    def test_creates_root_if_missing(self, tmp_path):
        root = tmp_path / "new-logbook"
        assert not root.exists()
        bootstrap_logbook(root)
        assert root.is_dir()

    def test_all_shipped_xsds_written(self, tmp_path):
        # The test discovers names dynamically from the package so the
        # assertion stays true when a v2 schema lands.
        bootstrap_logbook(tmp_path)
        assert _SHIPPED_XSD_NAMES, "no shipped XSDs found — test is misconfigured"
        for name in _SHIPPED_XSD_NAMES:
            assert (tmp_path / name).is_file(), f"missing {name}"

    def test_xsd_bytes_match_shipped_source(self, tmp_path):
        bootstrap_logbook(tmp_path)
        for name in _SHIPPED_XSD_NAMES:
            assert (tmp_path / name).read_bytes() == _shipped_xsd_bytes(name)

    def test_readme_written_with_template_bytes(self, tmp_path):
        bootstrap_logbook(tmp_path)
        assert (tmp_path / "README.md").read_bytes() == _shipped_readme_bytes()

    def test_subdirectories_created(self, tmp_path):
        bootstrap_logbook(tmp_path)
        assert (tmp_path / "jumps").is_dir()
        assert (tmp_path / "dropzones").is_dir()
        assert (tmp_path / ".trash").is_dir()
        # R.0.3a (D33): inventory subdirs for the four rig-manager
        # component kinds. Nested under ``inventory/`` and created via
        # ``parents=True`` so the parent appears too.
        assert (tmp_path / "inventory").is_dir()
        assert (tmp_path / "inventory" / "mains").is_dir()
        assert (tmp_path / "inventory" / "reserves").is_dir()
        assert (tmp_path / "inventory" / "aads").is_dir()
        assert (tmp_path / "inventory" / "containers").is_dir()
        # R.2.0b (D33): rig assemblies (folder-with-manifest) and
        # jumper records (flat single file). Both top-level under
        # the logbook root.
        assert (tmp_path / "rigs").is_dir()
        assert (tmp_path / "jumpers").is_dir()

    def test_does_not_create_settings_xml(self, tmp_path):
        # D29 explicitly excludes settings.xml — service layer handles
        # it lazily. A failing assertion here means scope crept.
        bootstrap_logbook(tmp_path)
        assert not (tmp_path / "settings.xml").exists()

    def test_does_not_create_index_sqlite(self, tmp_path):
        # D26's territory, not D29.
        bootstrap_logbook(tmp_path)
        assert not (tmp_path / "index.sqlite").exists()

    def test_does_not_create_logbook_lock(self, tmp_path):
        # Lockfile is the caller's (main.py's) job, not bootstrap's.
        bootstrap_logbook(tmp_path)
        assert not (tmp_path / ".logbook.lock").exists()


# --------------------------------------------------------------------------- #
# Preservation: user edits stay
# --------------------------------------------------------------------------- #

class TestPreservesUserEdits:
    def test_existing_readme_is_not_overwritten(self, tmp_path):
        # The user may have added notes tailored to their own logbook.
        # Those edits must survive a re-bootstrap on app upgrade.
        user_content = b"# My Personal Logbook\n\nCustom notes here.\n"
        (tmp_path / "README.md").write_bytes(user_content)
        bootstrap_logbook(tmp_path)
        assert (tmp_path / "README.md").read_bytes() == user_content

    def test_jumps_and_dropzone_content_untouched(self, tmp_path):
        # A bootstrap on a populated logbook must not disturb existing
        # jump folders or dropzone files. Worst-case failure mode would
        # be our mkdir-order stepping on something; this pins it down.
        jump_dir = tmp_path / "jumps" / "[1] 2020-01-01"
        jump_dir.mkdir(parents=True)
        (jump_dir / "jump.xml").write_bytes(b"<jump/>")
        (tmp_path / "dropzones").mkdir(parents=True, exist_ok=True)
        (tmp_path / "dropzones" / "dz-1.xml").write_bytes(b"<dropzone/>")

        bootstrap_logbook(tmp_path)

        assert (jump_dir / "jump.xml").read_bytes() == b"<jump/>"
        assert (tmp_path / "dropzones" / "dz-1.xml").read_bytes() == b"<dropzone/>"


# --------------------------------------------------------------------------- #
# Authority: XSD is app-owned and always refreshed
# --------------------------------------------------------------------------- #

class TestXsdAuthority:
    def test_existing_xsd_is_overwritten_with_shipped_bytes(self, tmp_path):
        # The XSD ships with the app. If a user (or a corrupted sync)
        # put arbitrary content where v1 should be, bootstrap replaces
        # it. Within a schema version, updates are additive (D18), so
        # refresh never invalidates older jumps.
        assert _SHIPPED_XSD_NAMES, "no shipped XSDs found"
        victim = tmp_path / _SHIPPED_XSD_NAMES[0]
        victim.write_bytes(b"<!-- tampered -->")
        bootstrap_logbook(tmp_path)
        assert victim.read_bytes() == _shipped_xsd_bytes(victim.name)


# --------------------------------------------------------------------------- #
# Idempotency: re-running changes nothing observable
# --------------------------------------------------------------------------- #

class TestIdempotency:
    def test_two_runs_produce_the_same_state(self, tmp_path):
        bootstrap_logbook(tmp_path)
        first = _snapshot(tmp_path)

        bootstrap_logbook(tmp_path)
        second = _snapshot(tmp_path)

        assert first == second

    def test_subdirs_already_present_does_not_raise(self, tmp_path):
        # Pre-creating each subdir forces every mkdir to hit the
        # exist_ok path. A regression where we removed exist_ok=True
        # would surface here.
        for name in (
            "jumps",
            "dropzones",
            "inventory/mains",
            "inventory/reserves",
            "inventory/aads",
            "inventory/containers",
            "rigs",
            "jumpers",
            ".trash",
        ):
            (tmp_path / name).mkdir(parents=True, exist_ok=True)
        bootstrap_logbook(tmp_path)  # must not raise


def _snapshot(root: Path) -> dict[str, bytes | None]:
    """Map every file under ``root`` to its bytes; directories map to None.

    Used to compare the observable state before/after a repeat call.
    """
    out: dict[str, bytes | None] = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        out[rel] = p.read_bytes() if p.is_file() else None
    return out


# --------------------------------------------------------------------------- #
# Path edge cases
# --------------------------------------------------------------------------- #

class TestPathEdgeCases:
    def test_deep_missing_parents_are_created(self, tmp_path):
        # parents=True on the initial mkdir handles this.
        target = tmp_path / "a" / "b" / "c" / "logbook"
        bootstrap_logbook(target)
        assert target.is_dir()
        assert (target / "jumps").is_dir()

    def test_root_is_a_file_raises(self, tmp_path):
        # If the user points us at an existing plain file, mkdir raises
        # FileExistsError (an OSError subclass). We let it propagate so
        # main.py's friendly-error branch catches it.
        victim = tmp_path / "not-a-folder"
        victim.write_bytes(b"oops")
        with pytest.raises(OSError):
            bootstrap_logbook(victim)
