"""Reserve service — R.0.3d create / get for the reserve component
(D33, D34).

Mirrors :mod:`backend.services.container_service`. Only ``create``
and ``get`` ship in R.0.3 per D33's rollout; update / list / delete
come with R.1.

Storage shape per D33:

    logbook_root/
      inventory/
        reserves/
          <uuid>.xml         # one flat file per reserve
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
from ..models.reserve import Reserve, ReserveCreate, ReserveUpdate
from ..storage.filesystem import atomic_write
from ..storage.trash import soft_delete_file
from ..xml.serialize import (
    element_to_reserve,
    reserve_to_bytes,
    reserve_to_element,
)
from ..xml.validator import XMLError, validate
from ..xml.validator import parse as xml_parse
from ._timestamps import now_utc_iso
from ._write_lock import with_writer_lock

_RESERVES_DIR = "inventory/reserves"
_TRASH_SUBDIR = "inventory/reserves"

_logger = logging.getLogger("backend.services.reserve")




def _reserve_path(logbook_root: Path, reserve_id: UUID) -> Path:
    """Resolve the on-disk path for a reserve's XML file."""
    return logbook_root / _RESERVES_DIR / f"{reserve_id}.xml"


def _read_reserve(path: Path) -> Reserve:
    """Parse + XSD-validate one reserve XML file."""
    if not path.is_file():
        raise NotFoundError(f"reserve file not found: {path.name}")
    try:
        element = xml_parse(path.read_bytes())
        validate(element)
    except XMLError as exc:
        raise ValidationFailedError(
            f"reserve {path.stem} is invalid: {exc}",
        ) from exc
    return element_to_reserve(element)


def _write_reserve(logbook_root: Path, r: Reserve) -> None:
    """Serialize, XSD-validate, and atomically write a Reserve to disk."""
    element = reserve_to_element(r)
    validate(element)  # D2: every write XSD-validated before persistence
    path = _reserve_path(logbook_root, r.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, reserve_to_bytes(r))


@with_writer_lock
def create_reserve(
    logbook_root: Path,
    user_id: str,
    payload: ReserveCreate,
) -> Reserve:
    """Create a new reserve at ``inventory/reserves/<uuid>.xml``.

    Server-assigns the UUID, stamps timestamps, XSD-validates, and
    writes atomically. Returns the persisted Reserve.

    Raises ValidationFailedError when Pydantic / XSD reject the shape.
    """
    del user_id  # v0.1: components are shared; reserved for forward compat
    now = now_utc_iso()
    try:
        r = Reserve(
            id=uuid4(),
            **payload.model_dump(),
            created_at=now,
            updated_at=now,
        )
    except ValidationError as exc:
        raise validation_failed_from_pydantic(exc, "reserve validation failed") from exc

    try:
        _write_reserve(logbook_root, r)
    except XMLError as exc:
        raise ValidationFailedError(
            f"generated reserve XML failed XSD validation: {exc}",
        ) from exc

    _logger.info(
        "reserve_created",
        extra={
            "reserve_id": str(r.id),
            "manufacturer": r.manufacturer,
            "model": r.model,
        },
    )
    return r


def get_reserve(
    logbook_root: Path,
    user_id: str,
    reserve_id: UUID,
) -> Reserve:
    """Return the reserve with the given id, or raise NotFoundError."""
    del user_id  # v0.1: see create_reserve
    return _read_reserve(_reserve_path(logbook_root, reserve_id))


@with_writer_lock
def set_assigned_rig_id(
    logbook_root: Path,
    reserve_id: UUID,
    rig_id: UUID | None,
) -> Reserve:
    """Set / clear the reserve's ``assigned_rig_id`` reference (D37, R.2.0c.iii.a).

    See ``container_service.set_assigned_rig_id`` for the contract —
    same internal seam: rig_service-only, atomic, XSD-validated.
    """
    current = _read_reserve(_reserve_path(logbook_root, reserve_id))
    merged = current.model_copy(
        update={
            "assigned_rig_id": rig_id,
            "updated_at": now_utc_iso(),
        }
    )
    _write_reserve(logbook_root, merged)
    return merged


def list_reserves(
    logbook_root: Path,
    user_id: str,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[Reserve]:
    """List every reserve under ``inventory/reserves/``.

    Same posture as ``container_service.list_containers`` (R.1a).
    """
    del user_id
    folder = logbook_root / _RESERVES_DIR
    if not folder.is_dir():
        return []

    parsed: list[Reserve] = []
    for xml_path in folder.glob("*.xml"):
        try:
            parsed.append(_read_reserve(xml_path))
        except ValidationFailedError as exc:
            _logger.warning(
                "reserve_skip_invalid",
                extra={"reserve_path": str(xml_path), "reason": str(exc)},
            )
            continue

    parsed.sort(key=lambda r: r.created_at or "", reverse=True)
    if offset:
        parsed = parsed[offset:]
    if limit is not None:
        parsed = parsed[:limit]
    return parsed


@with_writer_lock
def update_reserve(
    logbook_root: Path,
    user_id: str,
    reserve_id: UUID,
    payload: ReserveUpdate,
) -> Reserve:
    """Full-replace update of a reserve's editable fields.

    Preserves ``id``, ``assigned_rig_id``, and ``created_at``; bumps
    ``updated_at``. The full ``recert_extensions`` log is part of
    the payload — clients APPEND to it by sending the existing list
    plus the new entry. Replacing or trimming the log is permitted
    but uncommon (it's historical record).

    R.2.0c.iii.b D37 enforcement: a reserve on a rig cannot
    transition to non-active via PUT (rejected with
    :class:`ComponentInUse` 409). ``assigned_rig_id`` is no longer
    on ReserveUpdate; PUT bodies that include it are 422 at the
    Pydantic edge.
    """
    del user_id
    current = _read_reserve(_reserve_path(logbook_root, reserve_id))

    if (
        current.assigned_rig_id is not None
        and payload.status != ComponentStatus.ACTIVE
    ):
        raise ComponentInUse(
            f"reserve {reserve_id} is on rig {current.assigned_rig_id}; "
            "detach (via rig delete or R.5 repack) before changing status",
            errors=[
                FieldError(
                    pointer="#/status",
                    detail=(
                        f"reserve is on rig {current.assigned_rig_id}; "
                        "only the active status is allowed while assigned"
                    ),
                ),
            ],
            assigned_rig_id=str(current.assigned_rig_id),
        )

    try:
        merged = Reserve(
            id=current.id,
            assigned_rig_id=current.assigned_rig_id,
            created_at=current.created_at,
            updated_at=now_utc_iso(),
            **payload.model_dump(),
        )
    except ValidationError as exc:
        raise validation_failed_from_pydantic(exc, "reserve validation failed") from exc

    try:
        _write_reserve(logbook_root, merged)
    except XMLError as exc:
        raise ValidationFailedError(
            f"generated reserve XML failed XSD validation: {exc}",
        ) from exc

    _logger.info(
        "reserve_updated",
        extra={
            "reserve_id": str(merged.id),
            "manufacturer": merged.manufacturer,
            "model": merged.model,
            "status": merged.status.value,
        },
    )
    return merged


@with_writer_lock
def delete_reserve(
    logbook_root: Path,
    user_id: str,
    reserve_id: UUID,
) -> Path:
    """Soft-delete a reserve to ``.trash/inventory/reserves/`` (D19)."""
    del user_id
    path = _reserve_path(logbook_root, reserve_id)
    if not path.is_file():
        raise NotFoundError(f"reserve {reserve_id} not found")
    try:
        trashed = soft_delete_file(path, logbook_root, _TRASH_SUBDIR)
    except FileNotFoundError as exc:
        raise NotFoundError(f"reserve {reserve_id} not found") from exc

    _logger.info(
        "reserve_deleted",
        extra={
            "reserve_id": str(reserve_id),
            "trashed_to": str(trashed.relative_to(logbook_root)),
        },
    )
    return trashed


