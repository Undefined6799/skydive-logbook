"""In-process writer lock for the service layer (D50).

Every public service function that may write to disk — directly OR
transitively via :func:`folder_reconcile` — acquires :data:`WRITER_LOCK`
for its full duration via the :func:`with_writer_lock` decorator.

This includes read-shaped endpoints whose contract calls
``folder_reconcile`` on open (``get_jump``, ``list_jump_files``,
``get_rig``, ``list_rigs``): D25's reconcile-on-read is a write of
``SHA256SUMS`` when the manifest is stale, so the function is a writer
from the lock's perspective even though the API contract is "GET".
Pure reads that never reach a write path would not need the lock, but
no such function exists in v0.1.

D50's earlier framing said "reads do not acquire the lock"; that was
written before D25 reconcile-on-open landed, and the policy moved to
match the code. The current rule is: acquire the lock if the function
may write to disk under any branch.

The lock is **re-entrant** (``threading.RLock``), not a plain
``threading.Lock`` as D50's first draft proposed. The reason is
cross-service write composition: ``rig_service.create_rig`` acquires
the lock then calls ``main_service.set_assigned_rig_id`` (and three
sibling assigners), each of which is also a decorated public service
write. With a plain Lock the second acquisition on the same thread
would deadlock; an RLock recognises the same thread and lets it
proceed. The performance cost vs Lock is negligible (a thread-id
check on each acquisition), well below the cost of a single SQLite
write.

D50 has the policy details (scope, performance posture, alternatives
rejected) and an updated mention of the RLock choice.

Usage::

    from backend.services._write_lock import with_writer_lock

    @with_writer_lock
    def create_jump(logbook_root, user_id, payload, uploads=None):
        ...

A public service write missing this decorator is a code-review smell
per D50 §"Where the lock lives".
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

_P = ParamSpec("_P")
_R = TypeVar("_R")


WRITER_LOCK: threading.RLock = threading.RLock()
"""Module-level singleton lock serialising every decorated service write.

Re-entrant per the cross-service write composition explained above.
Tests that need to verify lock state (e.g. confirming exception-safety)
can introspect via the standard ``threading.RLock`` API.
"""


def with_writer_lock(fn: Callable[_P, _R]) -> Callable[_P, _R]:
    """Serialise every call to ``fn`` through :data:`WRITER_LOCK`.

    The lock is acquired before the wrapped function runs and released
    in a ``finally`` block after it returns or raises. Exception
    propagation is unchanged — the decorator is transparent except for
    the serialisation guarantee.

    Re-entrancy: if ``fn`` (or anything it calls transitively) is
    itself a decorated service write, the same thread re-acquires the
    RLock without blocking. This is what makes
    ``rig_service.create_rig → main_service.set_assigned_rig_id`` work
    without a deadlock.
    """
    @wraps(fn)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with WRITER_LOCK:
            return fn(*args, **kwargs)

    return wrapper
