"""Main service — R.0.3e create / get for the main canopy component
(D33, D34).

Mirrors :mod:`backend.services.container_service`. Only ``create``
and ``get`` ship in R.0.3 per D33's rollout; update / list / delete
come with R.1, and the reline workflow (move ``current_lineset``
into ``lineset_history``, install a new one) is its own service-
layer operation in a later phase.

Storage shape per D33:

    logbook_root/
      inventory/
        mains/
          <uuid>.xml         # one flat file per main canopy

Lineset id generation: the :class:`Lineset` model uses
``default_factory=uuid4`` so a payload that omits a Lineset id gets
a fresh UUID at construction time. The service does not need extra
defensive id stamping — Pydantic handles it.
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
from ..models.main import Main, MainCreate, MainUpdate
from ..storage.filesystem import atomic_write
from ..storage.trash import soft_delete_file
from ..xml.serialize import element_to_main, main_to_bytes, main_to_element
from ..xml.validator import XMLError, validate
from ..xml.validator import parse as xml_parse
from ._timestamps import now_utc_iso
from ._wear_counts import count_jumps_per_rig, derived_for
from ._write_lock import with_writer_lock

_MAINS_DIR = "inventory/mains"
_TRASH_SUBDIR = "inventory/mains"

_logger = logging.getLogger("backend.services.main")




def _main_path(logbook_root: Path, main_id: UUID) -> Path:
    """Resolve the on-disk path for a main's XML file."""
    return logbook_root / _MAINS_DIR / f"{main_id}.xml"


def _read_main(path: Path) -> Main:
    """Parse + XSD-validate one main XML file."""
    if not path.is_file():
        raise NotFoundError(f"main file not found: {path.name}")
    try:
        element = xml_parse(path.read_bytes())
        validate(element)
    except XMLError as exc:
        raise ValidationFailedError(
            f"main {path.stem} is invalid: {exc}",
        ) from exc
    return element_to_main(element)


def _write_main(logbook_root: Path, m: Main) -> None:
    """Serialize, XSD-validate, and atomically write a Main to disk."""
    element = main_to_element(m)
    validate(element)  # D2: every write XSD-validated before persistence
    path = _main_path(logbook_root, m.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, main_to_bytes(m))


@with_writer_lock
def create_main(
    logbook_root: Path,
    user_id: str,
    payload: MainCreate,
) -> Main:
    """Create a new main at ``inventory/mains/<uuid>.xml``.

    Server-assigns the UUID, stamps timestamps, XSD-validates, and
    writes atomically. Returns the persisted Main.

    Raises ValidationFailedError when Pydantic / XSD reject the shape.
    """
    del user_id  # v0.1: components are shared; reserved for forward compat
    now = now_utc_iso()
    try:
        m = Main(
            id=uuid4(),
            **payload.model_dump(),
            created_at=now,
            updated_at=now,
        )
    except ValidationError as exc:
        raise validation_failed_from_pydantic(exc, "main validation failed") from exc

    try:
        _write_main(logbook_root, m)
    except XMLError as exc:
        raise ValidationFailedError(
            f"generated main XML failed XSD validation: {exc}",
        ) from exc

    _logger.info(
        "main_created",
        extra={
            "main_id": str(m.id),
            "manufacturer": m.manufacturer,
            "model": m.model,
            "size_sqft": m.size_sqft,
        },
    )
    # D35: stamp the response shape on create so the 201 body
    # matches a subsequent GET (including the nested lineset's
    # ``jumps_on_lineset_total`` projection).
    return _with_derived_counts(m, count_jumps_per_rig(logbook_root))


def _with_derived_counts(m: Main, counts_by_rig: dict[UUID, int]) -> Main:
    """Stamp D35 ``jump_count_derived`` / ``_total`` and the nested
    lineset's ``jumps_on_lineset_derived`` / ``_total`` from the
    jumps-per-rig map.

    Per D46 the lineset's per-jump count is meant to be attributed
    via rig-snapshot.xml (R.4) so the count survives swaps and
    relines correctly. Until that ships, v0.1 approximates it as
    "jumps on the rig" — the same number we stamp on the main's
    own ``jump_count_derived``. When the lineset doesn't change
    between jumps (the v0.1 common case), the approximation matches
    exactly; reline + R.4 are what diverge the two values.
    """
    derived = derived_for(counts_by_rig, m.assigned_rig_id)
    update: dict[str, object] = {
        "jump_count_derived": derived,
        "jump_count_total": m.jump_count_initial + derived,
    }
    if m.current_lineset is not None:
        ls = m.current_lineset
        update["current_lineset"] = ls.model_copy(
            update={
                "jumps_on_lineset_derived": derived,
                "jumps_on_lineset_total": ls.jumps_on_lineset_initial + derived,
            },
        )
    return m.model_copy(update=update)


def get_main(
    logbook_root: Path,
    user_id: str,
    main_id: UUID,
) -> Main:
    """Return the main with the given id, or raise NotFoundError.

    Per D35 the response carries ``jump_count_derived`` /
    ``jump_count_total`` from the SQLite jumps index. The nested
    ``current_lineset`` gets its own ``jumps_on_lineset_derived`` /
    ``jumps_on_lineset_total`` populated by the same scan — see
    :func:`_with_derived_counts` for the v0.1 attribution
    approximation.
    """
    del user_id  # v0.1: see create_main
    raw = _read_main(_main_path(logbook_root, main_id))
    return _with_derived_counts(raw, count_jumps_per_rig(logbook_root))


@with_writer_lock
def set_assigned_rig_id(
    logbook_root: Path,
    main_id: UUID,
    rig_id: UUID | None,
) -> Main:
    """Set / clear the main's ``assigned_rig_id`` reference (D37, R.2.0c.iii.a).

    See ``container_service.set_assigned_rig_id`` for the contract —
    same internal seam: rig_service-only, atomic, XSD-validated.
    """
    current = _read_main(_main_path(logbook_root, main_id))
    merged = current.model_copy(
        update={
            "assigned_rig_id": rig_id,
            "updated_at": now_utc_iso(),
        }
    )
    _write_main(logbook_root, merged)
    return merged


def list_mains(
    logbook_root: Path,
    user_id: str,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[Main]:
    """List every main under ``inventory/mains/``.

    Same posture as ``container_service.list_containers`` (R.1a).
    """
    del user_id
    folder = logbook_root / _MAINS_DIR
    if not folder.is_dir():
        return []

    parsed: list[Main] = []
    for xml_path in folder.glob("*.xml"):
        try:
            parsed.append(_read_main(xml_path))
        except ValidationFailedError as exc:
            _logger.warning(
                "main_skip_invalid",
                extra={"main_path": str(xml_path), "reason": str(exc)},
            )
            continue

    # D35: one indexed scan over jumps, then per-main lookup. Each
    # main's nested current_lineset gets the same per-rig count
    # stamped onto ``jumps_on_lineset_derived`` (v0.1 approximation).
    counts = count_jumps_per_rig(logbook_root)
    parsed = [_with_derived_counts(m, counts) for m in parsed]

    parsed.sort(key=lambda m: m.created_at or "", reverse=True)
    if offset:
        parsed = parsed[offset:]
    if limit is not None:
        parsed = parsed[:limit]
    return parsed


@with_writer_lock
def update_main(
    logbook_root: Path,
    user_id: str,
    main_id: UUID,
    payload: MainUpdate,
) -> Main:
    """Full-replace update of a main's editable fields.

    Preserves ``id`` and ``created_at``; bumps ``updated_at``. The
    full lineset state — ``current_lineset`` plus ``lineset_history``
    — is part of the payload. The dedicated reline workflow (move
    ``current_lineset`` into ``lineset_history`` atomically, install
    a new ``current_lineset``) lands as a separate service-layer
    operation in a later phase; this PUT path accepts any shape that
    satisfies the model so the client can drive a reline manually
    by sending the desired final state.

    Lineset id stability: each :class:`Lineset` carries its own
    ``default_factory=uuid4`` UUID. Clients that want to preserve
    the existing ``current_lineset`` UUID through an update must
    echo it in the payload — Pydantic does not magically merge it
    from the on-disk state.

    R.2.0c.iii.b D37 enforcement: a main on a rig cannot transition
    to non-active via PUT (rejected with :class:`ComponentInUse`
    409). ``assigned_rig_id`` is no longer on MainUpdate; PUT bodies
    that include it are 422 at the Pydantic edge. Preserves
    ``assigned_rig_id`` from on-disk; the rig owns that field.
    """
    del user_id
    current = _read_main(_main_path(logbook_root, main_id))

    if (
        current.assigned_rig_id is not None
        and payload.status != ComponentStatus.ACTIVE
    ):
        raise ComponentInUse(
            f"main {main_id} is on rig {current.assigned_rig_id}; "
            "detach (via swap_main, rig delete, or R.5 repack) before "
            "changing status",
            errors=[
                FieldError(
                    pointer="#/status",
                    detail=(
                        f"main is on rig {current.assigned_rig_id}; "
                        "only the active status is allowed while assigned"
                    ),
                ),
            ],
            assigned_rig_id=str(current.assigned_rig_id),
        )

    try:
        merged = Main(
            id=current.id,
            assigned_rig_id=current.assigned_rig_id,
            created_at=current.created_at,
            updated_at=now_utc_iso(),
            **payload.model_dump(),
        )
    except ValidationError as exc:
        raise validation_failed_from_pydantic(exc, "main validation failed") from exc

    try:
        _write_main(logbook_root, merged)
    except XMLError as exc:
        raise ValidationFailedError(
            f"generated main XML failed XSD validation: {exc}",
        ) from exc

    _logger.info(
        "main_updated",
        extra={
            "main_id": str(merged.id),
            "manufacturer": merged.manufacturer,
            "model": merged.model,
            "size_sqft": merged.size_sqft,
            "status": merged.status.value,
        },
    )
    return _with_derived_counts(merged, count_jumps_per_rig(logbook_root))


@with_writer_lock
def delete_main(
    logbook_root: Path,
    user_id: str,
    main_id: UUID,
) -> Path:
    """Soft-delete a main to ``.trash/inventory/mains/`` (D19)."""
    del user_id
    path = _main_path(logbook_root, main_id)
    if not path.is_file():
        raise NotFoundError(f"main {main_id} not found")
    try:
        trashed = soft_delete_file(path, logbook_root, _TRASH_SUBDIR)
    except FileNotFoundError as exc:
        raise NotFoundError(f"main {main_id} not found") from exc

    _logger.info(
        "main_deleted",
        extra={
            "main_id": str(main_id),
            "trashed_to": trashed.relative_to(logbook_root).as_posix(),
        },
    )
    return trashed


