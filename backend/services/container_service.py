"""Container service — R.0.3b create / get for the container component
(D33, D34).

Mirrors :mod:`backend.services.dropzone_service` for the same reasons
(D7 thin REST adapter, D2 + D10 invariants on every write, D32 audit
timestamps). The slice is intentionally narrow — only ``create`` and
``get`` ship in R.0.3 per D33's rollout. Update / list / delete come
with R.1.

Storage shape per D33:

    logbook_root/
      inventory/
        containers/
          <uuid>.xml         # one flat file per container
      .trash/
        inventory/
          containers/
            <ts>_<uuid>.xml/<uuid>.xml   # post-soft-delete (R.1+)

Invariants (same as dropzone_service):
  * XSD validation on every write (D2).
  * Atomic write via ``storage.filesystem.atomic_write`` (D10).
  * No SHA256SUMS — components are flat single files; the integrity
    surface is XSD validation + the hardened parser. Manifest-style
    integrity belongs on folder-with-attachments entities (jumps;
    rigs once they ship).
  * No SQLite index work in R.0.3 — inventory tables land later
    alongside D35's per-kind ``*_wear`` projections. ``get`` reads
    the XML directly; list/filter is R.1's job.
"""
from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError

from ..api.errors import (
    ComponentInUse,
    FieldError,
    NotFoundError,
    ValidationFailedError,
    validation_failed_from_pydantic,
)
from ..models._component_base import ComponentStatus
from ..models.container import Container, ContainerCreate, ContainerUpdate
from ..storage.filesystem import atomic_write
from ..storage.trash import soft_delete_file
from ..xml.serialize import container_to_bytes, container_to_element, element_to_container
from ..xml.validator import XMLError, validate
from ..xml.validator import parse as xml_parse
from ._timestamps import now_utc_iso
from ._wear_counts import count_jumps_per_rig, derived_for
from ._write_lock import with_writer_lock

# Subdirectory under logbook_root where container XMLs live. Matches
# the path bootstrap creates in R.0.3a (_SUBDIRS).
_CONTAINERS_DIR = "inventory/containers"
# Same subdir under .trash/ when soft-deleted. ``soft_delete_file``
# creates parent dirs as needed (D19).
_TRASH_SUBDIR = "inventory/containers"

_logger = logging.getLogger("backend.services.container")


def _container_path(logbook_root: Path, container_id: UUID) -> Path:
    """Resolve the on-disk path for a container's XML file.

    Uses the UUID directly as the filename — no sanitization needed
    because UUIDs are guaranteed safe across every filesystem we
    target (D4).
    """
    return logbook_root / _CONTAINERS_DIR / f"{container_id}.xml"


def _read_container(path: Path) -> Container:
    """Parse + XSD-validate one container XML file.

    Raises ``NotFoundError`` if the file is missing,
    ``ValidationFailedError`` if the contents don't validate. Other
    ``OSError``s (permission, I/O) propagate unmodified — those are
    infrastructure problems the API layer surfaces as 500s.
    """
    if not path.is_file():
        raise NotFoundError(f"container file not found: {path.name}")
    try:
        element = xml_parse(path.read_bytes())
        validate(element)
    except XMLError as exc:
        # Disk corruption or hand-edit broke the XML. Same posture as
        # dropzone_service — surface as 422 so an operator can re-edit
        # or restore from backup.
        raise ValidationFailedError(
            f"container {path.stem} is invalid: {exc}",
        ) from exc
    return element_to_container(element)


def _write_container(logbook_root: Path, c: Container) -> None:
    """Serialize, XSD-validate, and atomically write a Container to disk.

    Validates BEFORE the atomic write so a failed XSD check leaves
    any previous file untouched. D2 + D10 invariants both apply.
    """
    element = container_to_element(c)
    validate(element)  # D2: every write XSD-validated before persistence
    path = _container_path(logbook_root, c.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, container_to_bytes(c))


@with_writer_lock
def create_container(
    logbook_root: Path,
    user_id: str,
    payload: ContainerCreate,
) -> Container:
    """Create a new container at ``inventory/containers/<uuid>.xml``.

    Server-assigns the UUID, stamps ``created_at`` / ``updated_at``,
    XSD-validates, and writes the file atomically. Returns the
    persisted Container.

    ``user_id`` is accepted per D8 for forward compatibility but not
    used to scope visibility in v0.1 — components are conceptually a
    single jumper's gear, and the single-user posture matches
    dropzone_service. When multi-user lands, a future D-entry will
    decide whether to scope inventory per user.

    Raises:
      ValidationFailedError: when Pydantic / XSD reject the shape.
        The REST layer (R.1+) turns this into a 422 problem+json with
        field pointers per D16.
    """
    del user_id  # v0.1: components are shared; reserved for forward compat
    now = now_utc_iso()
    try:
        c = Container(
            id=uuid4(),
            **payload.model_dump(),
            created_at=now,
            updated_at=now,
        )
    except ValidationError as exc:
        # Defensive — ContainerCreate already validated the payload.
        # A future field added on Container but not on ContainerCreate
        # could trip this.
        raise validation_failed_from_pydantic(exc, "container validation failed") from exc

    try:
        _write_container(logbook_root, c)
    except XMLError as exc:
        # Generated XML failed XSD validation. Indicates the model
        # passed Pydantic but is shaped wrong for the schema — a bug
        # in serialize, not in user input. Surface cleanly anyway.
        raise ValidationFailedError(
            f"generated container XML failed XSD validation: {exc}",
        ) from exc

    _logger.info(
        "container_created",
        extra={
            "container_id": str(c.id),
            "manufacturer": c.manufacturer,
            "model": c.model,
        },
    )
    # D35: a freshly-created container is unassigned, so derived is
    # 0 and total == initial. Run the helper anyway so the response
    # shape matches get_container / list_containers byte-for-byte
    # (clients don't see a stale ``jump_count_total = 0`` directly
    # after a 201).
    return _with_derived_count(c, count_jumps_per_rig(logbook_root))


def _with_derived_count(
    c: Container, counts_by_rig: dict[UUID, int]
) -> Container:
    """Stamp D35 ``jump_count_derived`` and ``jump_count_total``
    from the jumps-per-rig map.

    Returns a fresh Container; the on-disk shape is never mutated.
    """
    derived = derived_for(counts_by_rig, c.assigned_rig_id)
    return c.model_copy(
        update={
            "jump_count_derived": derived,
            "jump_count_total": c.jump_count_initial + derived,
        },
    )


def get_container(
    logbook_root: Path,
    user_id: str,
    container_id: UUID,
) -> Container:
    """Return the container with the given id, or raise NotFoundError.

    Per D35 the response carries ``jump_count_derived`` (count of
    jumps logged against the rig this container is on) and
    ``jump_count_total`` (initial + derived) alongside
    ``jump_count_initial``. The two derived fields come from the
    SQLite jumps index — never from ``container.xml`` — so the
    on-disk record stays the editable seed and the projection
    travels with the response.
    """
    del user_id  # v0.1: see create_container
    raw = _read_container(_container_path(logbook_root, container_id))
    counts = count_jumps_per_rig(logbook_root)
    return _with_derived_count(raw, counts)


@with_writer_lock
def set_assigned_rig_id(
    logbook_root: Path,
    container_id: UUID,
    rig_id: UUID | None,
) -> Container:
    """Set / clear the container's ``assigned_rig_id`` reference (D37, R.2.0c.iii.a).

    **Internal seam, called by ``rig_service`` only.** REST callers
    route through ``rig_service.create_rig`` (which sets) and
    ``rig_service.delete_rig`` (which clears). Direct REST mutation
    of ``assigned_rig_id`` via ``update_container`` is rejected in
    R.2.0c.iii.b — this helper is the only sanctioned write site.

    Read-modify-write under D2 + D10:
      1. Read the current container (404 if missing).
      2. Mutate ``assigned_rig_id`` and bump ``updated_at``.
      3. XSD-validate the merged shape.
      4. Atomic-write the file.

    No collision check on the destination rig id — the caller owns
    that invariant. ``rig_service.create_rig`` validates that the
    component's existing ``assigned_rig_id`` is None (or equal to
    the rig being created — idempotent re-run) BEFORE calling this
    helper, so a stale call here doesn't accidentally break the
    "every component is in zero or one rigs" invariant.
    """
    current = _read_container(_container_path(logbook_root, container_id))
    merged = current.model_copy(
        update={
            "assigned_rig_id": rig_id,
            "updated_at": now_utc_iso(),
        }
    )
    _write_container(logbook_root, merged)
    return merged


def list_containers(
    logbook_root: Path,
    user_id: str,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[Container]:
    """List every container under ``inventory/containers/``.

    Walks the directory and parses each XML file (D2 hardened parser
    + XSD validation per file). Returns full :class:`Container`
    objects rather than a compact summary — for v0.1's small
    inventory the parse cost is negligible, and the picker UI gets
    every field it needs without a follow-up GET. A summary type
    can land additively when a UI surfaces a need.

    Ordering: ``created_at`` descending (newest first), mirroring
    the convention used by ``list_jumps``. Components without a
    timestamp (e.g. legacy hand-edited files where reindex would
    fall back to mtime) sort last so the natural list view stays
    sensible.

    ``limit`` / ``offset`` apply at the service layer after parsing
    + sorting. For v0.1 inventory sizes (~tens of components per
    user) this is fine; SQLite-backed pagination is a future
    optimization listed in HANDOFF.md.

    Files that fail XSD validation are logged at WARNING and
    skipped — the list endpoint stays useful even if one file is
    corrupt; an operator runs ``verify`` to diagnose.
    """
    del user_id  # v0.1: see create_container
    folder = logbook_root / _CONTAINERS_DIR
    if not folder.is_dir():
        # Bootstrap should have created this; tolerate the absent
        # case anyway (a fresh logbook root before bootstrap, a
        # deleted dir, etc.) — return empty rather than raising.
        return []

    parsed: list[Container] = []
    for xml_path in folder.glob("*.xml"):
        try:
            parsed.append(_read_container(xml_path))
        except ValidationFailedError as exc:
            _logger.warning(
                "container_skip_invalid",
                extra={
                    "container_path": str(xml_path),
                    "reason": str(exc),
                },
            )
            continue

    # D35: enrich every container with ``jump_count_derived`` from a
    # single jumps-index scan. One SQL pass for N containers beats
    # the per-item ``count_jumps_for_rig`` call this would
    # otherwise be.
    counts = count_jumps_per_rig(logbook_root)
    parsed = [_with_derived_count(c, counts) for c in parsed]

    # Newest first; None timestamps sort last so unstamped legacy
    # records don't crowd the top of the picker.
    parsed.sort(
        key=lambda c: c.created_at or "",
        reverse=True,
    )

    if offset:
        parsed = parsed[offset:]
    if limit is not None:
        parsed = parsed[:limit]
    return parsed


@with_writer_lock
def update_container(
    logbook_root: Path,
    user_id: str,
    container_id: UUID,
    payload: ContainerUpdate,
) -> Container:
    """Full-replace update of a container's editable fields.

    Preserves ``id``, ``assigned_rig_id``, and ``created_at``; bumps
    ``updated_at``. Re-validates Pydantic + XSD before writing.

    R.2.0c.iii.b enforces D37's rule that a container currently on a
    rig cannot transition to a non-active status (retired / sold /
    out_of_service) via this PUT — the user must detach the rig
    first (delete the rig, or wait for R.5's repack flow). The
    rejection is a :class:`ComponentInUse` 409 with the holding rig's
    id in the error details so the UI can route the user there.

    ``assigned_rig_id`` itself is no longer on :class:`ContainerUpdate`
    (R.2.0c.iii.b): the field is rig_service-owned, mutated only via
    ``set_assigned_rig_id``. A PUT body that includes the field is
    rejected at the Pydantic edge by ``extra="forbid"`` (422).

    Raises:
      NotFoundError: no container with this id is on disk.
      ComponentInUse: container is on a rig and the target status is
        not active (D37, 409).
      ValidationFailedError: Pydantic / XSD rejects the merged shape.
    """
    del user_id  # v0.1: see create_container
    current = _read_container(_container_path(logbook_root, container_id))

    # R.2.0c.iii.b: D37 rule — can't retire/sell/OOS a component
    # that's currently on a rig. The current status doesn't matter;
    # what matters is "the future state would be a non-active
    # component that's still on a rig", which violates the
    # invariant.
    if (
        current.assigned_rig_id is not None
        and payload.status != ComponentStatus.ACTIVE
    ):
        raise ComponentInUse(
            f"container {container_id} is on rig {current.assigned_rig_id}; "
            "detach (via rig delete or R.5 repack) before changing status",
            errors=[
                FieldError(
                    pointer="#/status",
                    detail=(
                        f"container is on rig {current.assigned_rig_id}; "
                        "only the active status is allowed while assigned"
                    ),
                ),
            ],
            assigned_rig_id=str(current.assigned_rig_id),
        )

    try:
        merged = Container(
            id=current.id,                                    # immutable
            assigned_rig_id=current.assigned_rig_id,          # rig-owned
            created_at=current.created_at,                    # immutable (D32)
            updated_at=now_utc_iso(),
            **payload.model_dump(),
        )
    except ValidationError as exc:
        raise validation_failed_from_pydantic(exc, "container validation failed") from exc

    try:
        _write_container(logbook_root, merged)
    except XMLError as exc:
        raise ValidationFailedError(
            f"generated container XML failed XSD validation: {exc}",
        ) from exc

    _logger.info(
        "container_updated",
        extra={
            "container_id": str(merged.id),
            "manufacturer": merged.manufacturer,
            "model": merged.model,
            "status": merged.status.value,
        },
    )
    # D35: stamp ``jump_count_derived`` on the response so the client
    # render after an edit matches what a follow-up GET would show.
    return _with_derived_count(merged, count_jumps_per_rig(logbook_root))


@with_writer_lock
def delete_container(
    logbook_root: Path,
    user_id: str,
    container_id: UUID,
) -> Path:
    """Soft-delete a container to ``.trash/inventory/containers/`` (D19).

    Returns the new path inside ``.trash`` so callers can log the
    move. No cascade — a rig that referenced this container by id
    keeps the dangling reference; the rig service (R.2+) is
    responsible for handling stale references on its own
    invariants. Same posture as ``delete_dropzone`` not cascading
    to jumps.

    Raises :class:`NotFoundError` when the file doesn't exist.
    """
    del user_id  # v0.1: see create_container
    path = _container_path(logbook_root, container_id)
    if not path.is_file():
        raise NotFoundError(f"container {container_id} not found")
    try:
        trashed = soft_delete_file(path, logbook_root, _TRASH_SUBDIR)
    except FileNotFoundError as exc:
        # Race with a concurrent delete or out-of-band file move.
        # End state from caller's view is the same as success, but
        # surface the not-found honestly.
        raise NotFoundError(f"container {container_id} not found") from exc

    _logger.info(
        "container_deleted",
        extra={
            "container_id": str(container_id),
            "trashed_to": str(trashed.relative_to(logbook_root)),
        },
    )
    return trashed

