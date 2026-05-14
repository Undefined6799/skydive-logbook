"""AAD service — R.0.3c create / get for the AAD component (D33, D34).

Mirrors :mod:`backend.services.container_service`. Only ``create``
and ``get`` ship in R.0.3 per D33's rollout; update / list / delete
come with R.1.

Storage shape per D33:

    logbook_root/
      inventory/
        aads/
          <uuid>.xml         # one flat file per AAD

Service-window / EOL outputs are NOT stored — D39's pure-function
lookup derives them from manufacturer + model + DOM at read time
(R.4 territory).
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
from ..models.aad import AAD, AADCreate, AADUpdate
from ..storage.filesystem import atomic_write
from ..storage.trash import soft_delete_file
from ..xml.serialize import aad_to_bytes, aad_to_element, element_to_aad
from ..xml.validator import XMLError, validate
from ..xml.validator import parse as xml_parse
from ._timestamps import now_utc_iso
from ._write_lock import with_writer_lock

_AADS_DIR = "inventory/aads"
_TRASH_SUBDIR = "inventory/aads"

_logger = logging.getLogger("backend.services.aad")




def _aad_path(logbook_root: Path, aad_id: UUID) -> Path:
    """Resolve the on-disk path for an AAD's XML file."""
    return logbook_root / _AADS_DIR / f"{aad_id}.xml"


def _read_aad(path: Path) -> AAD:
    """Parse + XSD-validate one AAD XML file."""
    if not path.is_file():
        raise NotFoundError(f"aad file not found: {path.name}")
    try:
        element = xml_parse(path.read_bytes())
        validate(element)
    except XMLError as exc:
        raise ValidationFailedError(
            f"aad {path.stem} is invalid: {exc}",
        ) from exc
    return element_to_aad(element)


def _write_aad(logbook_root: Path, a: AAD) -> None:
    """Serialize, XSD-validate, and atomically write an AAD to disk."""
    element = aad_to_element(a)
    validate(element)  # D2: every write XSD-validated before persistence
    path = _aad_path(logbook_root, a.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, aad_to_bytes(a))


@with_writer_lock
def create_aad(
    logbook_root: Path,
    user_id: str,
    payload: AADCreate,
) -> AAD:
    """Create a new AAD at ``inventory/aads/<uuid>.xml``.

    Server-assigns the UUID, stamps timestamps, XSD-validates, and
    writes the file atomically. Returns the persisted AAD.

    Raises ValidationFailedError when Pydantic / XSD reject the shape.
    """
    del user_id  # v0.1: components are shared; reserved for forward compat
    now = now_utc_iso()
    try:
        a = AAD(
            id=uuid4(),
            **payload.model_dump(),
            created_at=now,
            updated_at=now,
        )
    except ValidationError as exc:
        raise validation_failed_from_pydantic(exc, "aad validation failed") from exc

    try:
        _write_aad(logbook_root, a)
    except XMLError as exc:
        raise ValidationFailedError(
            f"generated aad XML failed XSD validation: {exc}",
        ) from exc

    _logger.info(
        "aad_created",
        extra={
            "aad_id": str(a.id),
            "manufacturer": a.manufacturer,
            "model": a.model,
        },
    )
    return a


def get_aad(
    logbook_root: Path,
    user_id: str,
    aad_id: UUID,
) -> AAD:
    """Return the AAD with the given id, or raise NotFoundError."""
    del user_id  # v0.1: see create_aad
    return _read_aad(_aad_path(logbook_root, aad_id))


@with_writer_lock
def set_assigned_rig_id(
    logbook_root: Path,
    aad_id: UUID,
    rig_id: UUID | None,
) -> AAD:
    """Set / clear the AAD's ``assigned_rig_id`` reference (D37, R.2.0c.iii.a).

    See ``container_service.set_assigned_rig_id`` for the contract —
    same internal seam: rig_service-only, atomic, XSD-validated.
    """
    current = _read_aad(_aad_path(logbook_root, aad_id))
    merged = current.model_copy(
        update={
            "assigned_rig_id": rig_id,
            "updated_at": now_utc_iso(),
        }
    )
    _write_aad(logbook_root, merged)
    return merged


def list_aads(
    logbook_root: Path,
    user_id: str,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[AAD]:
    """List every AAD under ``inventory/aads/``.

    Same posture as ``container_service.list_containers`` (R.1a):
    walks the directory, parses each file, returns full AAD objects
    sorted by ``created_at`` descending. Files that fail XSD
    validation are logged WARNING and skipped.
    """
    del user_id
    folder = logbook_root / _AADS_DIR
    if not folder.is_dir():
        return []

    parsed: list[AAD] = []
    for xml_path in folder.glob("*.xml"):
        try:
            parsed.append(_read_aad(xml_path))
        except ValidationFailedError as exc:
            _logger.warning(
                "aad_skip_invalid",
                extra={"aad_path": str(xml_path), "reason": str(exc)},
            )
            continue

    parsed.sort(key=lambda a: a.created_at or "", reverse=True)
    if offset:
        parsed = parsed[offset:]
    if limit is not None:
        parsed = parsed[:limit]
    return parsed


@with_writer_lock
def update_aad(
    logbook_root: Path,
    user_id: str,
    aad_id: UUID,
    payload: AADUpdate,
) -> AAD:
    """Full-replace update of an AAD's editable fields.

    Preserves ``id``, ``assigned_rig_id``, and ``created_at``; bumps
    ``updated_at``. Same posture as
    ``container_service.update_container``. AAD service-window / EOL
    outputs are NOT stored — they're derived by D39's pure-function
    lookup at read time.

    R.2.0c.iii.b D37 enforcement: an AAD on a rig cannot transition
    to non-active via PUT (rejected with :class:`ComponentInUse`
    409). ``assigned_rig_id`` is no longer on AADUpdate; PUT bodies
    that include it are 422 at the Pydantic edge.
    """
    del user_id
    current = _read_aad(_aad_path(logbook_root, aad_id))

    if (
        current.assigned_rig_id is not None
        and payload.status != ComponentStatus.ACTIVE
    ):
        raise ComponentInUse(
            f"aad {aad_id} is on rig {current.assigned_rig_id}; "
            "detach (via rig delete or R.5 repack) before changing status",
            errors=[
                FieldError(
                    pointer="#/status",
                    detail=(
                        f"aad is on rig {current.assigned_rig_id}; "
                        "only the active status is allowed while assigned"
                    ),
                ),
            ],
            assigned_rig_id=str(current.assigned_rig_id),
        )

    try:
        merged = AAD(
            id=current.id,
            assigned_rig_id=current.assigned_rig_id,
            created_at=current.created_at,
            updated_at=now_utc_iso(),
            **payload.model_dump(),
        )
    except ValidationError as exc:
        raise validation_failed_from_pydantic(exc, "aad validation failed") from exc

    try:
        _write_aad(logbook_root, merged)
    except XMLError as exc:
        raise ValidationFailedError(
            f"generated aad XML failed XSD validation: {exc}",
        ) from exc

    _logger.info(
        "aad_updated",
        extra={
            "aad_id": str(merged.id),
            "manufacturer": merged.manufacturer,
            "model": merged.model,
            "status": merged.status.value,
        },
    )
    return merged


@with_writer_lock
def delete_aad(
    logbook_root: Path,
    user_id: str,
    aad_id: UUID,
) -> Path:
    """Soft-delete an AAD to ``.trash/inventory/aads/`` (D19).

    No cascade — a rig that referenced this AAD by id keeps the
    dangling reference; R.2's rig service handles stale references.
    """
    del user_id
    path = _aad_path(logbook_root, aad_id)
    if not path.is_file():
        raise NotFoundError(f"aad {aad_id} not found")
    try:
        trashed = soft_delete_file(path, logbook_root, _TRASH_SUBDIR)
    except FileNotFoundError as exc:
        raise NotFoundError(f"aad {aad_id} not found") from exc

    _logger.info(
        "aad_deleted",
        extra={
            "aad_id": str(aad_id),
            "trashed_to": str(trashed.relative_to(logbook_root)),
        },
    )
    return trashed


