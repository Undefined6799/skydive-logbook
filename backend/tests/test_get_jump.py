"""Tests for ``get_jump`` — Phase 3.1 single-jump detail read.

Contracts under test:

  * Full round-trip: ``get_jump(create_jump(...).id)`` returns a Jump
    equal to what create_jump wrote. XML is parsed through the
    hardened parser (D2) and XSD-validated before deserialization.
  * Missing id → ``NotFoundError`` (404 via D16 problem+json).
  * Reconcile-on-read (D25): if ``SHA256SUMS`` has drifted,
    ``get_jump`` heals it via ``folder_reconcile`` and returns
    successfully. A second ``get_jump`` is a no-op (no churn).
  * Broken on-disk jump.xml (malformed or XSD-invalid) propagates as
    an XMLError subclass — service surfaces it as a 500 per D16
    (disk corruption is a server-side problem for the caller).
  * User isolation: a jump owned by alice is invisible to default.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

from backend.api.errors import NotFoundError
from backend.models.jump import Jump, JumpCreate
from backend.services.jump_service import create_jump, get_jump
from backend.storage.bootstrap import bootstrap_logbook
from backend.storage.index import open_index
from backend.storage.manifest import MANIFEST_NAME, from_jump_xml

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

@pytest.fixture
def bootstrapped_root(tmp_path: Path) -> Path:
    root = tmp_path / "logbook"
    bootstrap_logbook(root)
    result = open_index(root)
    result.conn.close()
    return root


def _create(root: Path, *, user_id: str = "default", **overrides) -> Jump:
    data = dict(
        jump_number=1,
        date=date(2026, 4, 22),
        dropzone="Skydive Elsinore",
        exit_altitude_m=4000,
        deployment_altitude_m=900,
    )
    data.update(overrides)
    return create_jump(root, user_id, JumpCreate(**data))


# --------------------------------------------------------------------------- #
# Round-trip (happy path)
# --------------------------------------------------------------------------- #

class TestRoundTrip:
    def test_create_then_get_returns_equal_jump(self, bootstrapped_root: Path):
        # Minimal jump: verify every field round-trips through the
        # XML on disk. ``get_jump`` returns a Jump that compares equal
        # to the one create_jump returned — so there are no
        # silently-dropped or silently-transformed fields.
        created = _create(bootstrapped_root, jump_number=1, title="Glacier")
        fetched = get_jump(bootstrapped_root, "default", created.id)
        assert fetched == created

    def test_full_field_set_round_trips(self, bootstrapped_root: Path):
        # Exercise every optional field the Jump model carries — a
        # missing serialize/parse branch would surface here as a
        # silent None or a type mismatch.
        from datetime import time

        created = create_jump(
            bootstrapped_root,
            "default",
            JumpCreate(
                jump_number=851,
                title="4-way FS",
                date=date(2026, 4, 22),
                time=time(14, 30),
                timezone="America/Los_Angeles",
                dropzone="Skydive Elsinore",
                aircraft="Twin Otter",
                discipline="FS-4",
                exit_altitude_m=4000,
                deployment_altitude_m=900,
                freefall_time_s=55,
                notes="Funnel on exit, recovered.",
            ),
        )
        fetched = get_jump(bootstrapped_root, "default", created.id)
        assert fetched == created


# --------------------------------------------------------------------------- #
# Not found
# --------------------------------------------------------------------------- #

class TestNotFound:
    def test_unknown_id_raises_not_found(self, bootstrapped_root: Path):
        _create(bootstrapped_root, jump_number=1)
        with pytest.raises(NotFoundError) as exc_info:
            get_jump(bootstrapped_root, "default", uuid4())
        assert exc_info.value.http_status == 404
        assert exc_info.value.code == "not_found"

    def test_wrong_user_sees_not_found(self, bootstrapped_root: Path):
        # Jump belongs to alice; default asks for it. D3 says the index
        # is authoritative for existence queries, and the index row is
        # scoped to user_id. 404, not 403 — per D23's posture, we don't
        # leak "this id exists but belongs to someone else."
        created = _create(bootstrapped_root, user_id="alice", jump_number=1)
        with pytest.raises(NotFoundError):
            get_jump(bootstrapped_root, "default", created.id)


# --------------------------------------------------------------------------- #
# Reconcile on read (D25)
# --------------------------------------------------------------------------- #

class TestReconcileOnRead:
    def test_stale_manifest_is_healed(self, bootstrapped_root: Path):
        # Simulate a crash between "wrote jump.xml" and "wrote
        # SHA256SUMS" (D25 row 3): XML is valid, manifest is stale or
        # missing. On the first ``get_jump`` after such a crash,
        # folder_reconcile heals the manifest before the caller sees
        # the jump. This test proves reconcile runs and actually
        # repairs.
        created = _create(bootstrapped_root, jump_number=1, title="Crash sim")
        folder = bootstrapped_root / "jumps" / "[1] Crash sim"
        # Plant a bogus manifest — structurally parseable so the
        # "malformed manifest" branch doesn't intercept this test.
        (folder / MANIFEST_NAME).write_bytes(b"0" * 64 + b"  garbage\n")

        fetched = get_jump(bootstrapped_root, "default", created.id)
        assert fetched == created

        # Post-call: the manifest has been regenerated from XML claims.
        expected = from_jump_xml(folder, logbook_root=bootstrapped_root)
        assert (folder / MANIFEST_NAME).read_bytes() == expected

    def test_second_get_is_noop_on_reconciled_folder(
        self, bootstrapped_root: Path
    ):
        # After the first get_jump, manifest is in sync. Second
        # get_jump must not churn the file (mtime unchanged) — we rely
        # on folder_reconcile's idempotent structural compare.
        created = _create(bootstrapped_root, jump_number=1)
        folder = bootstrapped_root / "jumps" / "[1]"

        get_jump(bootstrapped_root, "default", created.id)
        before_mtime = (folder / MANIFEST_NAME).stat().st_mtime_ns

        get_jump(bootstrapped_root, "default", created.id)
        after_mtime = (folder / MANIFEST_NAME).stat().st_mtime_ns

        assert before_mtime == after_mtime


# --------------------------------------------------------------------------- #
# Broken on-disk state propagates
# --------------------------------------------------------------------------- #

class TestBrokenDiskState:
    def test_missing_jump_xml_raises(self, bootstrapped_root: Path):
        created = _create(bootstrapped_root, jump_number=1, title="Gone")
        folder = bootstrapped_root / "jumps" / "[1] Gone"
        (folder / "jump.xml").unlink()
        # ``folder_reconcile`` would raise FileNotFoundError on a
        # missing jump.xml (D25: "not a valid jump" is beyond
        # reconcile's remit). Get_jump lets it propagate — the caller
        # sees a 500-class failure and runs ``verify`` to diagnose.
        with pytest.raises(FileNotFoundError):
            get_jump(bootstrapped_root, "default", created.id)

    def test_malformed_xml_raises(self, bootstrapped_root: Path):
        from backend.xml.validator import XMLMalformed

        created = _create(bootstrapped_root, jump_number=1)
        folder = bootstrapped_root / "jumps" / "[1]"
        (folder / "jump.xml").write_bytes(b"<not-xml<")
        with pytest.raises(XMLMalformed):
            get_jump(bootstrapped_root, "default", created.id)
