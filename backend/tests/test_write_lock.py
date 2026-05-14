"""Unit tests for the D50 in-process writer lock.

Pinned behaviours:

  * The decorator serialises concurrent calls — interleaved sections
    inside two threads' write functions cannot overlap.
  * The decorator is exception-safe — a raise inside the wrapped
    function releases the lock.
  * The decorator is re-entrant — a decorated function calling
    another decorated function on the same thread does not deadlock.

Integration with the actual service layer is exercised in
``test_concurrent_writes.py`` (the §A7 race-window regression).
"""
from __future__ import annotations

import threading
import time

import pytest

from backend.services._write_lock import WRITER_LOCK, with_writer_lock


class TestSerialisation:
    def test_concurrent_calls_do_not_interleave(self):
        # Two threads call decorated functions that record an entry
        # and exit timestamp around a small sleep. With proper
        # serialisation, one thread's [enter, exit] interval cannot
        # overlap the other's. With no lock, the sleeps would overlap.
        events: list[tuple[str, float]] = []
        lock = threading.Lock()  # protects ``events`` only

        @with_writer_lock
        def critical(name: str) -> None:
            with lock:
                events.append((f"{name}-enter", time.monotonic()))
            time.sleep(0.05)
            with lock:
                events.append((f"{name}-exit", time.monotonic()))

        t1 = threading.Thread(target=critical, args=("a",))
        t2 = threading.Thread(target=critical, args=("b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Find the order of enter/exit pairs. Each pair must be
        # contiguous — no other-thread enter between them.
        assert len(events) == 4
        # First event is enter; second must be the SAME thread's exit.
        first_thread = events[0][0].split("-")[0]
        assert events[1][0] == f"{first_thread}-exit"
        # Third event is the other thread's enter; fourth its exit.
        other = "b" if first_thread == "a" else "a"
        assert events[2][0] == f"{other}-enter"
        assert events[3][0] == f"{other}-exit"


class TestExceptionSafety:
    def test_lock_released_on_exception(self):
        @with_writer_lock
        def boom() -> None:
            raise RuntimeError("kaboom")

        # If the lock leaked, this second acquisition would block
        # forever and the test would hang. We use a non-blocking
        # acquire to assert availability immediately.
        with pytest.raises(RuntimeError, match="kaboom"):
            boom()

        # Lock is free — non-blocking acquire returns True.
        assert WRITER_LOCK.acquire(blocking=False) is True
        WRITER_LOCK.release()

    def test_exception_type_preserved(self):
        # The decorator must not re-wrap exceptions in its own type.
        @with_writer_lock
        def picky() -> None:
            raise ValueError("specific")

        with pytest.raises(ValueError, match="specific"):
            picky()


class TestReentrancy:
    def test_same_thread_can_re_enter(self):
        # The cross-service write composition pattern — rig_service
        # calls main_service.set_assigned_rig_id (both decorated).
        # Same thread, two decorated frames on the stack, must not
        # deadlock.
        marks: list[str] = []

        @with_writer_lock
        def inner() -> None:
            marks.append("inner")

        @with_writer_lock
        def outer() -> None:
            marks.append("outer-pre")
            inner()
            marks.append("outer-post")

        outer()
        assert marks == ["outer-pre", "inner", "outer-post"]

    def test_reentry_releases_correctly(self):
        # After the outer call returns, the lock must be fully
        # released — the inner call's exit shouldn't have left the
        # lock in a half-held state.
        @with_writer_lock
        def inner() -> None:
            pass

        @with_writer_lock
        def outer() -> None:
            inner()

        outer()
        assert WRITER_LOCK.acquire(blocking=False) is True
        WRITER_LOCK.release()


class TestTransparency:
    def test_return_value_passes_through(self):
        @with_writer_lock
        def echo(x: int) -> int:
            return x * 2

        assert echo(7) == 14

    def test_args_and_kwargs_pass_through(self):
        @with_writer_lock
        def add(a: int, b: int = 0, *, c: int = 0) -> int:
            return a + b + c

        assert add(1, 2, c=3) == 6
        assert add(10, b=20) == 30

    def test_functools_wraps_preserves_metadata(self):
        @with_writer_lock
        def documented(x: int) -> int:
            """The original docstring."""
            return x

        assert documented.__name__ == "documented"
        assert documented.__doc__ == "The original docstring."
