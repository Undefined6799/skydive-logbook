"""Component wear-count derivation from the SQLite jumps index (D35).

Per D35, every component wear counter is ``initial + derived``:

  * ``*_initial`` lives in the component XML — the editable seed
    used at onboarding for used-gear and for manual corrections.
  * ``*_derived`` is computed from the jumps index — the count of
    jumps that have been logged against the component's currently-
    assigned rig.
  * ``*_total`` (a computed field on the response model) is the sum.

This module is the single read-path that computes ``*_derived`` for
the four jump-counted counters in v0.1:

  * ``Main.jump_count_derived``
  * ``AAD.jump_count_derived``
  * ``Container.jump_count_derived``
  * ``Main.current_lineset.jumps_on_lineset_derived``

V0.1 approximation. The proper D35 derivation walks
``rig-snapshot.xml`` per jump so a swap mid-rig keeps each
component's history attached to it correctly. That writer is R.2.3
territory and hasn't shipped yet, so until then we count by
``jumps.rig_id`` directly: any jump whose ``rig_id`` matches the
component's ``assigned_rig_id`` contributes 1. This is correct when
components never swap (the v0.1 common case) and over-counts the
new component / under-counts the replaced one across a swap. The
R.4 rig-snapshot-aware pass supersedes this without changing the
public response shape.
"""
from __future__ import annotations

from pathlib import Path
from uuid import UUID

from ..storage.index import open_index


def count_jumps_per_rig(
    logbook_root: Path,
    user_id: str = "default",
) -> dict[UUID, int]:
    """Return a ``{rig_id → jump count}`` map for one user's jumps.

    One indexed scan over ``jumps`` produces the whole map; callers
    that need counts for several components in one render (e.g.
    ``list_mains`` enriching N main canopies in a fleet) pay the
    cost once and look up by id from the dict. Single-component
    callers (``get_main``) use the same helper and key into the
    result with the component's ``assigned_rig_id``.

    Rows with ``rig_id IS NULL`` (legacy quick-log jumps, jumps
    logged before R.2.2 added the ref) are excluded by the WHERE
    clause — they contribute to no component's derived count.

    Keys are returned as ``UUID`` so callers can compare against
    ``ComponentBase.assigned_rig_id`` (also ``UUID | None``)
    without per-key string conversion. SQLite stores the rig_id as
    TEXT, so we hydrate each key here.
    """
    result = open_index(logbook_root)
    try:
        rows = result.conn.execute(
            "SELECT rig_id, COUNT(*) AS n FROM jumps "
            "WHERE user_id = ? AND rig_id IS NOT NULL "
            "GROUP BY rig_id",
            (user_id,),
        ).fetchall()
    finally:
        result.conn.close()
    return {UUID(row["rig_id"]): int(row["n"]) for row in rows}


def derived_for(
    counts_by_rig: dict[UUID, int],
    assigned_rig_id: UUID | None,
) -> int:
    """Return ``counts_by_rig[assigned_rig_id]``, or 0 when unassigned."""
    if assigned_rig_id is None:
        return 0
    return counts_by_rig.get(assigned_rig_id, 0)
