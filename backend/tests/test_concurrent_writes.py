"""Integration test for D50's writer lock in service-layer use.

Pins down two behaviours:

  1. The forward-review §A7 race window — a concurrent ``get_jump``
     mid-``update_jump`` rename — does NOT produce a transient
     500/404 when the writer lock is in effect. With the lock, the
     update completes atomically (XML rewrite → manifest rewrite →
     folder rename → index update) before any other thread can
     observe an inconsistent intermediate state.

  2. Concurrent writes to different resources serialise correctly —
     two threads each updating a different jump finish without
     interleaving the multi-step write sequences.

The unit-level lock contract is in ``test_write_lock.py``; this
file exercises the lock as part of the actual service surface.
"""
from __future__ import annotations

import threading
from pathlib import Path
from uuid import UUID

import pytest

from backend.models.jump import JumpCreate, JumpUpdate
from backend.services.jump_service import (
    create_jump,
    get_jump,
    update_jump,
)
from backend.storage.bootstrap import bootstrap_logbook
from backend.storage.index import open_index

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def bootstrapped_root(tmp_path: Path) -> Path:
    root = tmp_path / "logbook"
    bootstrap_logbook(root)
    result = open_index(root)
    result.conn.close()
    return root


def _seed_jump(
    root: Path, jump_number: int = 1, title: str | None = "Original"
) -> UUID:
    """Create a jump and return its UUID."""
    payload = JumpCreate(
        jump_number=jump_number,
        date="2026-04-22",  # type: ignore[arg-type]  # pydantic coerces
        dropzone="Skydive Elsinore",
        exit_altitude_m=4000,
        deployment_altitude_m=900,
        title=title,
    )
    jump = create_jump(root, "default", payload)
    return jump.id


def _update_payload(
    jump_number: int = 1, title: str | None = "Edited"
) -> JumpUpdate:
    return JumpUpdate(
        jump_number=jump_number,
        date="2026-04-22",  # type: ignore[arg-type]
        dropzone="Skydive Elsinore",
        exit_altitude_m=4000,
        deployment_altitude_m=900,
        title=title,
    )


# --------------------------------------------------------------------------- #
# §A7: get_jump during update_jump rename
# --------------------------------------------------------------------------- #

class TestReadDuringUpdateRename:
    """Forward-review §A7's race: an update_jump that renames the
    folder must not allow a concurrent get_jump to observe the
    intermediate state where the folder is at the new path but the
    index still points at the old path.

    Today's threading model: FastAPI sync handlers run on a
    threadpool. Two requests to the same process can interleave at
    arbitrary Python statements. Without D50's lock, get_jump could
    read the index, get the OLD folder string, then try to read
    ``<old>/jump.xml`` after the rename moved the folder — a
    transient FileNotFoundError → 500.

    With the lock, get_jump blocks until update_jump finishes the
    full sequence (xml + manifest + rename + index update). The
    read either sees the pre-update state or the post-update state,
    never the intermediate.
    """

    def test_concurrent_read_observes_consistent_state(
        self, bootstrapped_root: Path
    ):
        jump_id = _seed_jump(bootstrapped_root, jump_number=1, title="Before")

        # Run many writer/reader pairs in parallel. Without the lock
        # this would race; with the lock, every read either sees
        # "Before" or the latest written title — never an error.
        ITERATIONS = 20
        errors: list[BaseException] = []
        observed_titles: list[str | None] = []
        observed_lock = threading.Lock()

        def writer(i: int) -> None:
            try:
                update_jump(
                    bootstrapped_root,
                    "default",
                    jump_id,
                    _update_payload(jump_number=1, title=f"After-{i}"),
                )
            except BaseException as exc:  # noqa: BLE001
                with observed_lock:
                    errors.append(exc)

        def reader() -> None:
            try:
                jump = get_jump(bootstrapped_root, "default", jump_id)
                with observed_lock:
                    observed_titles.append(jump.title)
            except BaseException as exc:  # noqa: BLE001
                with observed_lock:
                    errors.append(exc)

        threads: list[threading.Thread] = []
        for i in range(ITERATIONS):
            threads.append(threading.Thread(target=writer, args=(i,)))
            threads.append(threading.Thread(target=reader))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No transient errors. With the lock, every reader either
        # blocks until the writer completes or runs at a quiescent
        # moment — never mid-rename.
        assert errors == [], (
            f"unexpected errors during concurrent get/update: {errors!r}"
        )
        # Every observed title is one of the values the writer
        # could have written, OR the original "Before". No corrupt
        # / partial state.
        valid = {"Before"} | {f"After-{i}" for i in range(ITERATIONS)}
        unexpected = [t for t in observed_titles if t not in valid]
        assert unexpected == [], (
            f"reader observed titles never written: {unexpected!r}"
        )


# --------------------------------------------------------------------------- #
# Two writers on different resources serialise correctly
# --------------------------------------------------------------------------- #

class TestConcurrentWritesDifferentResources:
    """Two writers updating different jumps run serially under the
    lock, but both succeed and the post-state is the union of both
    updates (no clobbering, no lost-update).
    """

    def test_two_jumps_updated_concurrently(
        self, bootstrapped_root: Path
    ):
        a_id = _seed_jump(bootstrapped_root, jump_number=1, title="A-orig")
        b_id = _seed_jump(bootstrapped_root, jump_number=2, title="B-orig")

        errors: list[BaseException] = []
        errors_lock = threading.Lock()

        def update_a() -> None:
            try:
                update_jump(
                    bootstrapped_root,
                    "default",
                    a_id,
                    _update_payload(jump_number=1, title="A-new"),
                )
            except BaseException as exc:  # noqa: BLE001
                with errors_lock:
                    errors.append(exc)

        def update_b() -> None:
            try:
                update_jump(
                    bootstrapped_root,
                    "default",
                    b_id,
                    _update_payload(jump_number=2, title="B-new"),
                )
            except BaseException as exc:  # noqa: BLE001
                with errors_lock:
                    errors.append(exc)

        ta = threading.Thread(target=update_a)
        tb = threading.Thread(target=update_b)
        ta.start()
        tb.start()
        ta.join()
        tb.join()

        assert errors == [], f"unexpected errors: {errors!r}"

        # Both updates landed.
        a_after = get_jump(bootstrapped_root, "default", a_id)
        b_after = get_jump(bootstrapped_root, "default", b_id)
        assert a_after.title == "A-new"
        assert b_after.title == "B-new"
