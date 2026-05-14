"""Pydantic model for the per-jump rig snapshot (D36, R.2.1).

A rig snapshot is written once per jump as ``rig-snapshot.xml``
inside the jump folder, alongside ``jump.xml`` and hashed into the
same ``SHA256SUMS`` manifest. It denormalizes the Rig + four
components + Jumper as they were on the jump's date so historical
queries ("what gear was on jump #427") don't silently mutate when
current state evolves later (component retired, reline happens,
jumper's exit_weight changes, etc.).

Per D36:
  * ``main`` includes ``current_lineset`` but NOT ``lineset_history``
    — only the lineset actually in use on this jump matters. The
    XSD tolerates either shape; the writer enforces empty history
    on the snapshot side.
  * ``rig`` is denormalized to (id, nickname, jurisdiction,
    last_repack_date). Repack history is intentionally NOT carried
    — D36 keeps the snapshot compact.
  * The snapshot is immutable post-create. ``update_jump`` does
    NOT rewrite it (D31 + D36).

R.2.1 lands the static shape only. The writer in ``create_jump``
follows in R.2.3 once R.2.2 has added the jump.xml fields
(``<rig_id>`` / ``<environment_flags>`` / ``<reserve_ride>``).
"""
from __future__ import annotations

from datetime import date as _date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .aad import AAD
from .container import Container
from .jumper import Jumper
from .main import Main
from .reserve import Reserve
from .rig import Jurisdiction


class RigSnapshotRig(BaseModel):
    """Frozen rig identity at snapshot time (D36).

    Not a full :class:`Rig` — the snapshot deliberately omits
    ``repack_history`` to keep snapshot files small. The
    ``last_repack_date`` field is the most recent entry's date as
    of snapshot time, computed by the writer.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    nickname: str = Field(min_length=1, max_length=120)
    jurisdiction: Jurisdiction
    # None when the rig has no repack history yet (a freshly-created
    # rig that has never been packed). The writer computes this
    # from ``rig.repack_history[-1].date`` if the list is non-empty.
    last_repack_date: _date | None = None


class RigSnapshot(BaseModel):
    """Frozen snapshot of a rig + its four components + the jumper at
    jump time (D36).

    Serialized to ``<jump_folder>/rig-snapshot.xml``. Immutable
    post-create.

    The four component fields and ``jumper`` are full
    :class:`Main` / :class:`Reserve` / :class:`AAD` /
    :class:`Container` / :class:`Jumper` instances — the snapshot
    carries every field of each entity so historical resolution
    doesn't depend on the live entity files still being intact.

    The writer is expected to set ``main.lineset_history = []`` on
    the snapshot copy (D36); the model itself doesn't enforce
    this so a hand-crafted file with history present still parses
    cleanly. Round-trip tests pin the byte-stable empty-history
    case.
    """

    model_config = ConfigDict(extra="forbid")

    # D17 canonical UTC ISO-8601 timestamp. Authored by
    # ``create_jump`` at the moment the snapshot is frozen. Stored
    # as a string for the same reason Jump.created_at is — opaque
    # token, no tz-aware datetime on the wire.
    snapshot_at: str

    # Frozen rig identity (id + nickname + jurisdiction + optional
    # last_repack_date). Not a full Rig — see :class:`RigSnapshotRig`.
    rig: RigSnapshotRig

    # Full denormalized copies. Reusing the live models keeps the
    # field set in lockstep — a future change to e.g. Main grows
    # the snapshot's main entry too, additively.
    main: Main
    reserve: Reserve
    aad: AAD
    container: Container
    jumper: Jumper
