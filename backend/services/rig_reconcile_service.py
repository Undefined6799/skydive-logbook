"""Boot-time reconcile for D37 rig ↔ component bidirectional refs (D70).

When ``rig_service.create_rig`` crashes between writing ``rig.xml`` and
finishing the four-iteration component-assignment loop (D37
§"assignment step"), the on-disk state is a rig folder referencing
four components but only the iteration-completed components pointing
back at the rig. The retry path is doubly broken
(``mkdir(exist_ok=False)`` fails AND the half-bound components have
stale ``assigned_rig_id`` references that defeat
``_validate_component_for_assignment``); D70 documents why.

This module ships the recovery path. Per D70 the policy is
**forward-complete the assignment** — the rig's ``rig.xml`` is the
authoritative statement of intent, so each component the rig
references gets its ``assigned_rig_id`` brought into agreement.
Components whose ``assigned_rig_id`` points at a rig that either no
longer exists or no longer references the component get cleared.

The function is decorated with ``@with_writer_lock`` (D50). It is
idempotent — running it twice is a no-op on the second pass — and
cheap: one parse per rig folder plus one walk of every inventory
component, with at most one ``atomic_write`` per component that
needs repair. Boot-time wiring is in ``backend/main.py``.

Why not heal on read in ``get_rig`` / ``list_rigs``: D70 §"Why not
heal on read" — the cost is asymmetric (every list_rigs would
re-parse every component) and a boot-time reconcile catches the
crash class at exactly the moment a request would otherwise observe
inconsistency, with no per-request cost.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from ..api.errors import NotFoundError, ValidationFailedError
from ..models._component_base import ComponentBase
from ..models.rig import Rig
from . import aad_service, container_service, main_service, reserve_service, rig_service
from ._write_lock import with_writer_lock

_logger = logging.getLogger("backend.services.rig_reconcile")


# Each tuple binds: human-readable kind, getter (returns parsed
# component or raises), and assigner (writes the change). The four
# entries mirror ``rig_service._COMPONENT_REGISTRY`` — kept in
# sync so a future fifth component kind needs one new tuple here
# and one there, not a fan-out across modules.
_GetterFn = Callable[[Path, str, UUID], ComponentBase]
_AssignerFn = Callable[[Path, UUID, UUID | None], ComponentBase]
# Sequence (covariant), not list (invariant): each per-kind lister
# returns ``list[Main] | list[Reserve] | …``, all of which are
# Sequence[ComponentBase] but not list[ComponentBase].
_ListerFn = Callable[[Path, str], Sequence[ComponentBase]]


@dataclass(frozen=True)
class _ComponentEntry:
    kind: str
    rig_field: str
    getter: _GetterFn
    assigner: _AssignerFn
    lister: _ListerFn


_COMPONENT_ENTRIES: tuple[_ComponentEntry, ...] = (
    _ComponentEntry(
        kind="main",
        rig_field="current_main_id",
        getter=main_service.get_main,
        assigner=main_service.set_assigned_rig_id,
        lister=main_service.list_mains,
    ),
    _ComponentEntry(
        kind="reserve",
        rig_field="current_reserve_id",
        getter=reserve_service.get_reserve,
        assigner=reserve_service.set_assigned_rig_id,
        lister=reserve_service.list_reserves,
    ),
    _ComponentEntry(
        kind="aad",
        rig_field="current_aad_id",
        getter=aad_service.get_aad,
        assigner=aad_service.set_assigned_rig_id,
        lister=aad_service.list_aads,
    ),
    _ComponentEntry(
        kind="container",
        rig_field="current_container_id",
        getter=container_service.get_container,
        assigner=container_service.set_assigned_rig_id,
        lister=container_service.list_containers,
    ),
)


@dataclass(frozen=True)
class RigReconcileReport:
    """Counts emitted by :func:`folder_reconcile_rigs`.

    A clean logbook reports zeros across the board. Non-zero values
    are normal post-crash recovery — operator-visible at INFO via the
    structured log line emitted at the end of the function.
    """
    rigs_scanned: int
    components_scanned: int
    components_forward_completed: int
    components_cleared: int
    conflicts: tuple[str, ...] = field(default_factory=tuple)


@with_writer_lock
def folder_reconcile_rigs(
    logbook_root: Path,
    user_id: str,
) -> RigReconcileReport:
    """Heal D37 bidirectional refs between rigs and inventory components.

    Per D70: the rig.xml is the authoritative statement of intent. For
    each rig referenced on disk, every component it references gets
    ``assigned_rig_id`` set to that rig (forward-complete). For every
    component whose ``assigned_rig_id`` points at a rig that either
    doesn't exist or doesn't reference the component, the assignment
    is cleared.

    Idempotent. Re-entrancy under WRITER_LOCK is handled by D50's
    RLock — the inner ``set_assigned_rig_id`` calls re-acquire on the
    same thread without blocking.

    Args:
      logbook_root: the path that contains ``rigs/`` and
        ``inventory/``. Both subtrees are walked; missing subdirs are
        treated as empty (the no-rigs-yet case is healthy).
      user_id: D8 scope. v0.1 is always ``"default"`` (boot site in
        main.py passes that literally); kept as a parameter so the
        multi-user future doesn't need to refactor every call site.

    Returns:
      :class:`RigReconcileReport` with counts and any conflict
      messages. Conflicts (a component referenced by two rig folders)
      are logged at WARNING and the component is left untouched —
      reconcile cannot decide which rig is "right" so it surfaces the
      anomaly for the operator.
    """
    # Step 1: build the "expected" assignment from rig folders.
    # ``list_rigs`` returns valid rigs only (skips partial-create
    # stubs and XML-invalid folders, logging WARNING per the
    # ``rig_skip_invalid`` event). A partial-create rig folder
    # without rig.xml is treated as if the rig didn't exist — its
    # half-bound components (if any) will be detected in step 2 as
    # pointing at a rig that doesn't reference them, and cleared.
    rigs = rig_service.list_rigs(logbook_root, user_id)

    expected: dict[UUID, UUID] = {}
    conflicts: list[str] = []
    for rig in rigs:
        for entry in _COMPONENT_ENTRIES:
            component_id: UUID = getattr(rig, entry.rig_field)
            if component_id in expected:
                # Two rigs reference the same component. D37 pre-write
                # validation should make this impossible, but
                # hand-edited XML (D2 §"the XML is text") could
                # produce it. Surface and skip.
                msg = (
                    f"{entry.kind} {component_id} is referenced by both "
                    f"rig {expected[component_id]} and rig {rig.id}; "
                    "reconcile skipping (operator intervention required)"
                )
                conflicts.append(msg)
                _logger.warning(
                    "rig_reconcile_conflict",
                    extra={
                        "component_kind": entry.kind,
                        "component_id": str(component_id),
                        "rig_a": str(expected[component_id]),
                        "rig_b": str(rig.id),
                    },
                )
            else:
                expected[component_id] = rig.id

    # Step 2: walk inventory and bring components into agreement.
    components_scanned = 0
    components_forward_completed = 0
    components_cleared = 0

    conflict_ids = _components_involved_in_conflicts(expected, rigs, conflicts)

    for entry in _COMPONENT_ENTRIES:
        try:
            components = entry.lister(logbook_root, user_id)
        except (FileNotFoundError, NotFoundError):
            # Inventory subfolder missing entirely — pre-bootstrap or
            # an empty fleet. Healthy state; nothing to walk.
            continue
        for component in components:
            components_scanned += 1
            if component.id in conflict_ids:
                # Two rigs claim this component; reconcile cannot
                # decide. Already logged in step 1.
                continue
            expected_rig = expected.get(component.id)
            current_rig = component.assigned_rig_id
            if current_rig == expected_rig:
                continue
            # Mismatch — repair.
            try:
                entry.assigner(logbook_root, component.id, expected_rig)
            except (ValidationFailedError, NotFoundError) as exc:
                # The component's XML failed to parse / write — log
                # and continue. A broken inventory file is the user's
                # problem; reconcile is best-effort per D70
                # §"intentionally tolerant".
                _logger.warning(
                    "rig_reconcile_component_skip",
                    extra={
                        "component_kind": entry.kind,
                        "component_id": str(component.id),
                        "reason": str(exc),
                    },
                )
                continue
            if expected_rig is None:
                components_cleared += 1
                _logger.info(
                    "rig_reconcile_component_cleared",
                    extra={
                        "component_kind": entry.kind,
                        "component_id": str(component.id),
                        "previous_rig_id": str(current_rig),
                    },
                )
            else:
                components_forward_completed += 1
                _logger.info(
                    "rig_reconcile_component_forward_completed",
                    extra={
                        "component_kind": entry.kind,
                        "component_id": str(component.id),
                        "rig_id": str(expected_rig),
                        "previous_rig_id": (
                            str(current_rig) if current_rig is not None else None
                        ),
                    },
                )

    report = RigReconcileReport(
        rigs_scanned=len(rigs),
        components_scanned=components_scanned,
        components_forward_completed=components_forward_completed,
        components_cleared=components_cleared,
        conflicts=tuple(conflicts),
    )

    # Single summary line so operators see "did the reconcile do
    # anything?" without grepping for individual events. INFO not
    # WARNING because a clean run with zero repairs is the
    # healthy-boot case.
    _logger.info(
        "rig_reconcile_complete",
        extra={
            "rigs_scanned": report.rigs_scanned,
            "components_scanned": report.components_scanned,
            "components_forward_completed": report.components_forward_completed,
            "components_cleared": report.components_cleared,
            "conflicts": len(report.conflicts),
        },
    )
    return report


def _components_involved_in_conflicts(
    expected: dict[UUID, UUID],
    rigs: list[Rig],
    conflicts: list[str],
) -> set[UUID]:
    """Build the set of component ids that two rigs both claim.

    The ``expected`` dict deliberately preserves the first-seen
    binding (so a conflict doesn't poison the rest of the run); this
    helper recovers the component ids that need to be excluded from
    the step-2 repair walk because reconcile can't decide.

    Why re-walk: ``expected`` overwrites no key, so the conflicting
    component's id is in ``expected`` (with the first-seen rig) and
    is also in the ``conflicts`` log line — but neither structure
    preserves *all* the rig ids that claim it. A re-walk
    reconstructs the full mapping in one pass. The cost is
    O(rigs × 4) and runs only when ``conflicts`` is non-empty (the
    healthy path returns the empty set immediately).
    """
    if not conflicts:
        return set()
    seen: dict[UUID, list[UUID]] = {}
    for rig in rigs:
        for entry in _COMPONENT_ENTRIES:
            cid: UUID = getattr(rig, entry.rig_field)
            seen.setdefault(cid, []).append(rig.id)
    return {cid for cid, rig_ids in seen.items() if len(rig_ids) > 1}
