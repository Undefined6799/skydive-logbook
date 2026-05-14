"""Rig service — R.2.0b create/get; R.2.0c.ii list/update/delete (D33, D37, D38).

A rig is a folder-with-manifest under
``logbook_root/rigs/<sanitized-nickname>/``: ``rig.xml`` plus a
``SHA256SUMS`` manifest. The folder layout matches the per-jump layout
(D25) so future additions — seal photos, rigger documents — are
additive without restructuring.

Slice scope:

  * R.2.0b (done): create + get.
  * R.2.0c.ii (this slice): list, update (metadata-only with D37
    swap rejection), delete (folder soft-delete to ``.trash/rigs/``).
  * R.2.0c.iii (next): D37 cross-entity validation on ``create_rig``
    + ``delete_rig`` — component-exists check, mark-assigned,
    clear-on-delete, with new error codes
    ``component_already_assigned`` and ``component_in_use``.

D37 in v0.1 forbids ANY change to the four ``current_*_id`` refs via
a direct PUT. The main is jumper-swappable but only through a
dedicated ``swap_main`` operation (a future slice); the reserve, AAD,
and container change only through a repack event (R.5). The
``update_rig`` flow detects a swap-via-PUT attempt and raises
:class:`RigComponentSwapUnsupported` with a ``FieldError`` pointing
at the offending ref so the UI can route the user to the correct
operation.

Storage shape:

    logbook_root/
      rigs/
        <sanitized-nickname>/
          rig.xml
          SHA256SUMS
      .trash/
        rigs/
          <ts>_<nickname>/<original-folder>/...   # R.2.0c.ii

Invariants (every write):
  * XSD validation BEFORE the atomic write (D2).
  * Atomic write via ``storage.filesystem.atomic_write`` (D10).
  * SHA256SUMS regenerated in the same transaction as ``rig.xml``
    (D5 / D25 — for R.2.0b/.c a rig folder has only ``rig.xml``,
    so ``manifest.generate(folder)`` is sufficient; the recovery-
    path helper ``from_rig_xml`` waits until the folder can grow
    attachments).
  * No SQLite index work in R.2.0c.ii — index tables for rigs land
    in R.3 alongside D35's per-kind ``*_wear`` projections.
  * Folder rename on nickname change is os.rename (POSIX-atomic).
    Order: write rig.xml + SHA256SUMS at the current folder, THEN
    rename. A crash between the two leaves a complete-but-misnamed
    folder which still passes verify; the user (or a future
    folder_reconcile_rigs step) can rename or re-edit.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError

from ..api.errors import (
    ComponentAlreadyAssigned,
    FieldError,
    NotFoundError,
    RigComponentSwapUnsupported,
    RigNicknameConflict,
    ValidationFailedError,
    validation_failed_from_pydantic,
)
from ..models._component_base import ComponentBase, ComponentStatus
from ..models.rig import Rig, RigCreate, RigUpdate
from ..storage import manifest as _manifest
from ..storage.filesystem import atomic_write, sanitize_folder_name
from ..storage.index import open_index
from ..storage.trash import soft_delete
from ..xml.serialize import element_to_rig, rig_to_bytes, rig_to_element
from ..xml.validator import XMLError, validate
from ..xml.validator import parse as xml_parse
from . import aad_service, container_service, main_service, reserve_service
from ._timestamps import now_utc_iso
from ._write_lock import with_writer_lock

# Subdirectory under logbook_root where rig folders live. Matches the
# path bootstrap creates (R.2.0b adds it to bootstrap._SUBDIRS).
_RIGS_DIR = "rigs"
# Same subdir name under .trash/ when a rig is soft-deleted, so the
# trash hierarchy reads ``.trash/rigs/<ts>_<nickname>/...``.
_TRASH_SUBDIR = "rigs"

# The single authoritative XML inside each rig folder. Mirrors
# JUMP_XML_NAME's posture in jump_service.
_RIG_XML_NAME = "rig.xml"

_logger = logging.getLogger("backend.services.rig")


# D37 cross-entity validation (R.2.0c.iii.a). Each entry binds:
#   field_name   — the RigCreate / RigUpdate attribute holding this ref
#   getter       — inventory_service.get_<kind>
#   assigner     — inventory_service.set_assigned_rig_id
#   kind         — human-readable noun for error messages
# Iterating the registry keeps the four checks + assignments
# structurally identical so a future fifth component kind is a
# single new tuple, not four parallel branches. The Callable types
# below are covariant in return — each ``get_<kind>`` returns a
# concrete subclass (Main, Reserve, AAD, Container) all of which
# satisfy ComponentBase, which is the surface this module reads
# (``status`` and ``assigned_rig_id``).
_GetterFn = Callable[[Path, str, UUID], ComponentBase]
_AssignerFn = Callable[[Path, UUID, UUID | None], ComponentBase]
_COMPONENT_REGISTRY: tuple[
    tuple[str, _GetterFn, _AssignerFn, str], ...
] = (
    (
        "current_main_id",
        main_service.get_main,
        main_service.set_assigned_rig_id,
        "main",
    ),
    (
        "current_reserve_id",
        reserve_service.get_reserve,
        reserve_service.set_assigned_rig_id,
        "reserve",
    ),
    (
        "current_aad_id",
        aad_service.get_aad,
        aad_service.set_assigned_rig_id,
        "AAD",
    ),
    (
        "current_container_id",
        container_service.get_container,
        container_service.set_assigned_rig_id,
        "container",
    ),
)


def _validate_component_for_assignment(
    logbook_root: Path,
    field_name: str,
    component_id: UUID,
    component_kind: str,
    getter: _GetterFn,
    rig_id_being_created: UUID | None = None,
) -> None:
    """Per-ref D37 check (R.2.0c.iii.a).

    Confirms the referenced component:
      1. Exists on disk (else 422 — bad ref).
      2. Has ``status == active`` (else 422 — retired/sold/OOS gear
         can't go on a rig).
      3. Has ``assigned_rig_id`` equal to either ``None`` or
         ``rig_id_being_created`` (the second case is for an
         idempotent retry of a partial-write where some components
         already got assigned to this rig). Anything else → 409
         ``component_already_assigned`` with the existing rig id in
         the error.

    Raises:
      ValidationFailedError (422): component missing or wrong status.
      ComponentAlreadyAssigned (409): component is on a different rig.
    """
    pointer = f"#/{field_name}"

    # Step 1: existence.
    try:
        component = getter(logbook_root, "default", component_id)
    except NotFoundError as exc:
        raise ValidationFailedError(
            f"{component_kind} {component_id} does not exist",
            errors=[
                FieldError(
                    pointer=pointer,
                    detail=f"{component_kind} {component_id} not found",
                ),
            ],
        ) from exc

    # Step 2: status check.
    if component.status != ComponentStatus.ACTIVE:
        raise ValidationFailedError(
            f"{component_kind} {component_id} is {component.status.value!r}, "
            "not active — cannot assign to a rig",
            errors=[
                FieldError(
                    pointer=pointer,
                    detail=(
                        f"{component_kind} status is "
                        f"{component.status.value!r}; only active "
                        "components can be assigned to a rig"
                    ),
                ),
            ],
        )

    # Step 3: assignment check.
    existing = component.assigned_rig_id
    if existing is not None and existing != rig_id_being_created:
        raise ComponentAlreadyAssigned(
            f"{component_kind} {component_id} is already assigned to "
            f"rig {existing}",
            errors=[
                FieldError(
                    pointer=pointer,
                    detail=(
                        f"{component_kind} is already on rig {existing}; "
                        "detach it before assigning to another rig"
                    ),
                ),
            ],
            assigned_rig_id=str(existing),
        )


def _read_rig(folder: Path) -> Rig:
    """Parse + XSD-validate the ``rig.xml`` inside ``folder``.

    Raises:
      ``NotFoundError``: ``rig.xml`` is missing (the folder may exist
        from a partial write but the document isn't there).
      ``ValidationFailedError``: hardened parser or XSD rejected the
        contents — disk corruption, hand-edit mistake, or a future
        schema drift. Surfaced as 422 so an operator can re-edit or
        restore from backup.
    Other ``OSError``s propagate.
    """
    rig_xml = folder / _RIG_XML_NAME
    if not rig_xml.is_file():
        raise NotFoundError(f"rig.xml not found in {folder.name}")
    try:
        element = xml_parse(rig_xml.read_bytes())
        validate(element)
    except XMLError as exc:
        raise ValidationFailedError(
            f"rig at {folder.name} is invalid: {exc}",
        ) from exc
    return element_to_rig(element)


def _write_rig_folder(folder: Path, r: Rig) -> None:
    """Write a rig.xml + SHA256SUMS pair into ``folder`` atomically.

    The folder is assumed to exist (the caller did the
    ``mkdir(exist_ok=False)`` collision check). XSD validation runs
    BEFORE either atomic_write so a bad shape leaves no partial
    state — D2 + D10. Order is rig.xml first then SHA256SUMS; a crash
    between the two leaves rig.xml on disk and a missing manifest,
    which the R.2.0c folder-reconcile path will heal by regenerating
    the manifest from the on-disk rig.xml hash.
    """
    element = rig_to_element(r)
    validate(element)  # D2: every write XSD-validated before persistence
    rig_xml_path = folder / _RIG_XML_NAME
    atomic_write(rig_xml_path, rig_to_bytes(r))
    # generate() is the correct write-path call (D5 / manifest.py
    # docstring): the bytes were just written, so hashing what we
    # see on disk and hashing what we just wrote produce the same
    # answer. The recovery-path-shaped from_*_xml helper is only
    # needed when attachments could rot independently — a v0.1 rig
    # folder has none.
    manifest_bytes = _manifest.generate(folder)
    atomic_write(folder / _manifest.MANIFEST_NAME, manifest_bytes)


def _read_all_rigs(logbook_root: Path) -> list[tuple[Path, Rig]]:
    """Walk ``rigs/`` and return ``(folder, Rig)`` for every valid rig.

    Mirrors ``list_rigs`` but returns the folder path alongside the
    parsed Rig so callers (D58 star transitions, future reconcile)
    can write back through ``_write_rig_folder``. Skips partial-
    create stubs and rigs whose XML fails validation, matching
    ``list_rigs``'s tolerant posture. Order is not sorted — callers
    sort if they care.
    """
    rigs_root = logbook_root / _RIGS_DIR
    if not rigs_root.is_dir():
        return []
    out: list[tuple[Path, Rig]] = []
    for folder in rigs_root.iterdir():
        if not folder.is_dir():
            continue
        try:
            out.append((folder, _read_rig(folder)))
        except (NotFoundError, ValidationFailedError) as exc:
            _logger.warning(
                "rig_skip_invalid",
                extra={"rig_folder": str(folder), "reason": str(exc)},
            )
    return out


def _elect_successor_star(
    logbook_root: Path,
    candidates: list[tuple[Path, Rig]],
) -> tuple[Path, Rig]:
    """Pick the next rig to star per D58.

    Election rule:
      1. Most recently jumped — ``MAX(date)`` from the ``jumps``
         index, grouped by ``rig_id``, restricted to the candidate
         rig ids.
      2. Tiebreaker (no jumps logged against any candidate, or
         multiple candidates share the same MAX(date)): rig with
         the latest ``created_at``, then by id for full determinism.

    ``candidates`` must be non-empty. The caller has already filtered
    out the rig being deleted, so every entry is a legitimate
    successor.

    The jumps-index reach is the cross-entity coupling flagged in
    D58: bounded to this single function, only called on the soft-
    delete of a starred rig.
    """
    if not candidates:
        raise ValueError("_elect_successor_star requires non-empty candidates")

    id_to_pair: dict[str, tuple[Path, Rig]] = {
        str(r.id): (folder, r) for folder, r in candidates
    }

    # SQL: MAX(date) per rig_id, scoped to the candidates so the
    # index can use idx_jumps_user_date efficiently. NULL rig_ids
    # (pre-R.2.2 jumps and quick-log jumps) are excluded by the
    # IN-list match — they can't appear in candidate_ids.
    best_id: str | None = None
    best_date: str = ""
    placeholders = ",".join("?" * len(id_to_pair))
    result = open_index(logbook_root)
    try:
        rows = result.conn.execute(
            f"SELECT rig_id, MAX(date) AS last_jump_date "  # noqa: S608 (placeholders, not user input)
            f"FROM jumps WHERE rig_id IN ({placeholders}) "
            f"GROUP BY rig_id",
            tuple(id_to_pair.keys()),
        ).fetchall()
    finally:
        result.conn.close()

    for row in rows:
        if row["last_jump_date"] and row["last_jump_date"] > best_date:
            best_date = row["last_jump_date"]
            best_id = row["rig_id"]

    if best_id is not None:
        return id_to_pair[best_id]

    # D59 amendment: when no remaining candidate has any jumps
    # logged, fall back to the carousel-leftmost rig. The user's
    # mental model of "which rig is mine" is the carousel order —
    # if the star auto-moves, snap to the leftmost remaining rig
    # rather than the most-recently-created.
    #
    # Sort tuple, ASC:
    #   (display_order is None, display_order, created_at, id)
    # Rigs missing display_order (legacy) sort after rigs that
    # have one. Within the present-display_order bucket, lowest
    # value wins (leftmost). Within the legacy bucket, earliest
    # created_at then id keep it deterministic.
    candidates_sorted = sorted(
        candidates,
        key=lambda fr: (
            fr[1].display_order is None,
            fr[1].display_order if fr[1].display_order is not None else 0,
            fr[1].created_at or "",
            str(fr[1].id),
        ),
    )
    return candidates_sorted[0]


def _clear_all_stars(
    logbook_root: Path, *, exclude: UUID | None = None
) -> None:
    """Write ``starred=False`` on every starred rig under ``rigs/``.

    Used by :func:`set_star` to enforce the invariant defensively:
    even if the on-disk state has drifted to multiple starred rigs
    (manual XML edit, pre-D58 file, crash recovery), this clears all
    of them so the caller can stamp the single target without
    leaving stale stars behind.

    ``exclude`` skips one rig id (the upcoming target) so the
    target doesn't need a redundant clear-then-set write.
    """
    now = now_utc_iso()
    for folder, rig in _read_all_rigs(logbook_root):
        if not rig.starred:
            continue
        if exclude is not None and rig.id == exclude:
            continue
        cleared = rig.model_copy(update={"starred": False, "updated_at": now})
        _write_rig_folder(folder, cleared)


@with_writer_lock
def create_rig(
    logbook_root: Path,
    user_id: str,
    payload: RigCreate,
) -> Rig:
    """Create a new rig at ``rigs/<sanitized-nickname>/``.

    Per the D33 + D4 folder-uniqueness rule, two rigs cannot share a
    sanitized nickname. The collision check is the bare ``mkdir
    (exist_ok=False)`` — there's no per-user prefix scan because
    nicknames are not numeric and can't slip past mkdir like jump
    numbers can with the optional title prefix.

    Per D37, ``create_rig`` validates each of the four
    ``current_*_id`` refs (component exists + status active +
    unassigned) BEFORE writing the rig folder, then sets each
    component's ``assigned_rig_id`` to the new rig's id AFTER the
    rig.xml + SHA256SUMS write. Order rationale: writing the rig
    first means a partial-completion crash leaves components
    pointing at no rig (recoverable by clearing them), rather than
    components pointing at a rig that doesn't exist with no way to
    find them.

    ``user_id`` is accepted per D8 but unused in v0.1 — rigs are
    conceptually one jumper's gear, same posture as the inventory
    components.

    Raises:
      ValidationFailedError: Pydantic / XSD rejected the shape, the
        nickname produced an invalid folder name, or one of the
        four component refs doesn't exist / isn't active.
      ComponentAlreadyAssigned: a referenced component is currently
        on a different rig (D37, 409).
      RigNicknameConflict: another rig already lives at the same
        sanitized nickname (409 with code rig_nickname_conflict).
    """
    del user_id  # v0.1: rigs are shared; reserved for forward compat
    now = now_utc_iso()

    # Sanitize the nickname before any other work so a bad name fails
    # cleanly at 422 with a field pointer. ``_rig_folder`` would also
    # raise, but doing it here lets us produce a precise FieldError.
    try:
        sanitized_nickname = sanitize_folder_name(payload.nickname)
    except ValueError as exc:
        raise ValidationFailedError(
            "rig nickname produces an invalid folder name",
            errors=[
                FieldError(
                    pointer="#/nickname",
                    detail=str(exc),
                ),
            ],
        ) from exc

    # D58 auto-star + D59 display_order: one directory walk does
    # double duty under the writer lock — we need both the count
    # (D58) and the max existing order (D59). Rigs missing a
    # display_order (pre-D59 legacy data) count as -1 for the max
    # so the new rig still lands strictly after them.
    existing = _read_all_rigs(logbook_root)
    auto_star = len(existing) == 0
    next_order = (
        max(
            (r.display_order for _, r in existing if r.display_order is not None),
            default=-1,
        )
        + 1
    )

    try:
        r = Rig(
            id=uuid4(),
            **payload.model_dump(),
            starred=auto_star,
            display_order=next_order,
            created_at=now,
            updated_at=now,
        )
    except ValidationError as exc:
        # Defensive — RigCreate already validated the payload. A
        # future field added on Rig but not on RigCreate could trip
        # this.
        raise validation_failed_from_pydantic(exc, "rig validation failed") from exc

    # D37 cross-entity validation (R.2.0c.iii.a). Fail-fast on the
    # first bad ref so the caller sees a precise pointer; a future
    # slice could collect all four into a single response if the
    # multi-error case becomes common.
    for field_name, getter, _assigner, kind in _COMPONENT_REGISTRY:
        component_id = getattr(r, field_name)
        _validate_component_for_assignment(
            logbook_root,
            field_name,
            component_id,
            kind,
            getter,
            rig_id_being_created=r.id,
        )

    folder = logbook_root / _RIGS_DIR / sanitized_nickname
    folder.parent.mkdir(parents=True, exist_ok=True)
    try:
        folder.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise RigNicknameConflict(
            f"a rig with nickname {sanitized_nickname!r} already exists",
            errors=[
                FieldError(
                    pointer="#/nickname",
                    detail=f"already in use: {sanitized_nickname!r}",
                ),
            ],
        ) from exc

    try:
        _write_rig_folder(folder, r)
    except XMLError as exc:
        # XSD rejected the generated XML — bug in serialize, not user
        # input. Surface cleanly anyway. Folder is left as a stub for
        # R.2.0c's reconcile to clean up; matches the partial-create
        # crash posture for jumps.
        raise ValidationFailedError(
            f"generated rig XML failed XSD validation: {exc}",
        ) from exc

    # D37 assignment step. Set each component's assigned_rig_id to
    # the new rig's id. A crash partway through leaves the rig
    # referencing components that don't yet point back; reconcile
    # (a future slice) detects the mismatch and either fixes the
    # components or clears the rig. Per D37: "at worst, some
    # components are re-marked-available on the next reconcile".
    for field_name, _getter, assigner, _kind in _COMPONENT_REGISTRY:
        component_id = getattr(r, field_name)
        assigner(logbook_root, component_id, r.id)

    _logger.info(
        "rig_created",
        extra={
            "rig_id": str(r.id),
            "rig_nickname": sanitized_nickname,
            "jurisdiction": r.jurisdiction.value,
            "repack_history_len": len(r.repack_history),
        },
    )
    return r


def _find_rig_by_id(
    logbook_root: Path, rig_id: UUID
) -> tuple[Path, Rig]:
    """Walk ``rigs/`` and return the (folder, Rig) for ``rig_id``.

    Shared by ``get_rig``, ``update_rig``, and ``delete_rig``: each
    needs to resolve the on-disk folder for an id since the rig
    index doesn't exist yet (R.3 territory). The walk parses each
    candidate ``rig.xml`` until the id matches.

    Raises:
      NotFoundError: no rig with this id exists on disk.
      ValidationFailedError: a candidate rig.xml is invalid; the
        walk surfaces the first bad file and stops, mirroring the
        jump_service.get_jump posture (don't silently mask
        corruption while looking for an unrelated record).
    """
    rigs_root = logbook_root / _RIGS_DIR
    if not rigs_root.is_dir():
        raise NotFoundError(f"rig {rig_id} not found")
    target = str(rig_id)
    for folder in rigs_root.iterdir():
        if not folder.is_dir():
            continue
        rig_xml = folder / _RIG_XML_NAME
        if not rig_xml.is_file():
            # Partial create or out-of-band cleanup; skip rather than
            # 404 the whole call — R.2.0c+ reconcile will surface it.
            continue
        try:
            r = _read_rig(folder)
        except NotFoundError:
            # Race with concurrent delete; treat as not present.
            continue
        if str(r.id) == target:
            return folder, r
    raise NotFoundError(f"rig {rig_id} not found")


@with_writer_lock
def get_rig(
    logbook_root: Path,
    user_id: str,
    rig_id: UUID,
) -> Rig:
    """Return the rig with the given id.

    R.2.0b/.c uses a directory walk via :func:`_find_rig_by_id`. R.3
    will swap this for a SQLite-indexed lookup once the rigs index
    table lands; the walk-based v0.1 path is sufficient for single-
    user workloads (typically 1–3 rigs).

    Raises:
      NotFoundError: no rig with the given id exists on disk.
      ValidationFailedError: a candidate rig.xml is invalid.
    """
    del user_id  # v0.1: see create_rig
    _, r = _find_rig_by_id(logbook_root, rig_id)
    return r


@with_writer_lock
def list_rigs(
    logbook_root: Path,
    user_id: str,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[Rig]:
    """List every rig under ``rigs/``.

    Walks the directory and parses each ``<nickname>/rig.xml`` (D2
    hardened parser + XSD per file). Returns full :class:`Rig`
    objects rather than a compact summary — for v0.1's small fleet
    (1–3 rigs typical) the parse cost is negligible, and the picker
    UI gets every field with no follow-up GET.

    Ordering: by ``display_order`` ASC (D59 — leftmost first), with
    legacy rigs that lack the field sorting after rigs that have
    one. Deterministic tiebreaker chain: missing-vs-present
    display_order first, then earliest ``created_at``, then id.
    Pre-D59 ordering was ``created_at`` DESC; D59 supersedes.

    ``limit`` / ``offset`` apply at the service layer after parsing
    + sorting. SQLite-backed pagination is a future R.3
    optimization.

    Folders that fail XSD validation, or that lack a ``rig.xml``
    (partial-create stubs), are logged at WARNING and skipped — the
    list endpoint stays useful even if one folder is corrupt; an
    operator runs ``verify`` to diagnose.
    """
    del user_id  # v0.1: see create_rig
    rigs_root = logbook_root / _RIGS_DIR
    if not rigs_root.is_dir():
        # Bootstrap should have created this; tolerate the absent
        # case anyway and return empty.
        return []

    parsed: list[Rig] = []
    for folder in rigs_root.iterdir():
        if not folder.is_dir():
            continue
        try:
            parsed.append(_read_rig(folder))
        except NotFoundError as exc:
            # Folder exists but rig.xml is missing — partial create
            # stub. Surface as a warning and keep going.
            _logger.warning(
                "rig_skip_invalid",
                extra={
                    "rig_folder": str(folder),
                    "reason": str(exc),
                },
            )
            continue
        except ValidationFailedError as exc:
            _logger.warning(
                "rig_skip_invalid",
                extra={
                    "rig_folder": str(folder),
                    "reason": str(exc),
                },
            )
            continue

    # D59: sort by display_order ASC, legacy (no display_order)
    # rigs after rigs that have one. The (is_missing, value, …)
    # tuple gives a stable ascending sort without needing
    # ``reverse=True``; missing values bucket to the end via the
    # leading ``True > False`` term. Secondary keys (created_at,
    # id) make the order fully deterministic when two rigs share
    # the same display_order — which can happen transiently during
    # a reorder operation or on hand-edited data.
    parsed.sort(
        key=lambda r: (
            r.display_order is None,
            r.display_order if r.display_order is not None else 0,
            r.created_at or "",
            str(r.id),
        )
    )

    if offset:
        parsed = parsed[offset:]
    if limit is not None:
        parsed = parsed[:limit]
    return parsed


# --------------------------------------------------------------------------- #
# update_rig — D37 swap-via-PUT rejection + atomic folder rename
# --------------------------------------------------------------------------- #

# Map each ``current_*_id`` field to the operation a user must use
# to change it. The detail strings ride on the ``RigComponentSwap
# Unsupported`` error so the UI can surface a precise hint per
# kind: the main has a future swap path; the others wait for R.5.
_SWAP_GUIDANCE: dict[str, str] = {
    "current_main_id": (
        "main canopy swaps go through the dedicated swap_main "
        "operation (jumper-facing per D37); changing this ref via "
        "PUT is not supported"
    ),
    "current_reserve_id": (
        "reserve changes only through a repack event (D37, R.5); "
        "changing this ref via PUT is not supported"
    ),
    "current_aad_id": (
        "AAD changes only through a repack event (D37, R.5); "
        "changing this ref via PUT is not supported"
    ),
    "current_container_id": (
        "container changes only through a repack event (D37, R.5); "
        "changing this ref via PUT is not supported"
    ),
}


def _check_no_swap_via_put(current: Rig, payload: RigUpdate) -> None:
    """Raise :class:`RigComponentSwapUnsupported` if any of the four
    refs differ between the on-disk rig and the PUT payload.

    Per D37: the main is jumper-swappable but only via ``swap_main``
    (a future slice); the other three change only at repack events
    (R.5). Detecting the swap-via-PUT attempt here gives a precise
    error envelope that the UI can route on, rather than silently
    re-writing the rig with a half-valid composition.

    All offending refs are reported in a single error so the user
    sees the full list (e.g. someone hand-editing a body and
    swapping multiple fields gets one response covering all four).
    """
    field_errors: list[FieldError] = []
    for field, guidance in _SWAP_GUIDANCE.items():
        current_val = getattr(current, field)
        payload_val = getattr(payload, field)
        if current_val != payload_val:
            field_errors.append(
                FieldError(
                    pointer=f"#/{field}",
                    detail=guidance,
                )
            )
    if field_errors:
        raise RigComponentSwapUnsupported(
            "rig component swap via PUT is not supported in v0.1",
            errors=field_errors,
        )


@with_writer_lock
def update_rig(
    logbook_root: Path,
    user_id: str,
    rig_id: UUID,
    payload: RigUpdate,
) -> Rig:
    """Apply a metadata edit to an existing rig (R.2.0c.ii, D37, D38).

    v0.1 update surface: nickname, jurisdiction, notes_log. The four
    ``current_*_id`` refs are present on :class:`RigUpdate` (full-
    replace shape) but cannot be changed through this surface — see
    :func:`_check_no_swap_via_put`. The repack history is preserved
    from the on-disk rig (RigUpdate doesn't expose it; D38 makes
    that a R.5 mutation).

    Six-step ordering (mirrors :func:`update_jump`'s D31 ordering
    where applicable):

      1. Look up the current rig and its folder; 404 on miss.
      2. D37 check: any change to a ``current_*_id`` ref → 409
         ``rig_component_swap_unsupported``.
      3. Sanitize the new nickname; 422 on bad chars.
      4. If the nickname produces a different folder name and that
         destination already exists, 409 ``rig_nickname_conflict``.
      5. Build the merged Rig (preserve id+created_at+repack_history,
         bump updated_at). XSD-validate, atomic-write rig.xml +
         SHA256SUMS at the CURRENT folder.
      6. If the folder name changed, ``os.rename`` it. POSIX-atomic;
         the target was confirmed absent in step 4.

    Raises:
      NotFoundError: no rig with this id.
      RigComponentSwapUnsupported: swap-via-PUT attempt.
      RigNicknameConflict: new nickname collides with another rig.
      ValidationFailedError: bad nickname / XSD shape mismatch.
    """
    del user_id  # v0.1: see create_rig

    # Step 1: fetch current rig + folder.
    current_folder, current = _find_rig_by_id(logbook_root, rig_id)

    # Step 2: D37 swap check. All offending refs reported in one go.
    _check_no_swap_via_put(current, payload)

    # Step 3: sanitize the new nickname. ``sanitize_folder_name``
    # rejects forbidden chars / Windows reserved / trailing dot etc.
    try:
        new_sanitized = sanitize_folder_name(payload.nickname)
    except ValueError as exc:
        raise ValidationFailedError(
            "rig nickname produces an invalid folder name",
            errors=[
                FieldError(pointer="#/nickname", detail=str(exc)),
            ],
        ) from exc

    # Step 4: folder collision check (only when the sanitized name
    # changes). ``current_folder.name`` is the existing sanitized
    # nickname on disk — comparing names rather than re-deriving
    # from current.nickname so an out-of-band manual rename still
    # resolves correctly.
    nickname_changed = new_sanitized != current_folder.name
    new_folder = logbook_root / _RIGS_DIR / new_sanitized
    if nickname_changed and new_folder.exists():
        raise RigNicknameConflict(
            f"a rig with nickname {new_sanitized!r} already exists",
            errors=[
                FieldError(
                    pointer="#/nickname",
                    detail=f"already in use: {new_sanitized!r}",
                ),
            ],
        )

    # Step 5: build the merged Rig. id + created_at preserved (D32);
    # updated_at bumped; repack_history preserved (R.5 territory per
    # D38).
    try:
        merged = Rig(
            id=current.id,
            nickname=payload.nickname,
            jurisdiction=payload.jurisdiction,
            current_main_id=current.current_main_id,
            current_reserve_id=current.current_reserve_id,
            current_aad_id=current.current_aad_id,
            current_container_id=current.current_container_id,
            repack_history=current.repack_history,
            notes_log=payload.notes_log,
            created_at=current.created_at,
            updated_at=now_utc_iso(),
        )
    except ValidationError as exc:
        raise validation_failed_from_pydantic(exc, "rig validation failed") from exc

    # Write rig.xml + SHA256SUMS at the CURRENT folder. If this
    # crashes mid-write, _write_rig_folder leaves the previous
    # rig.xml intact (atomic_write semantics) and the manifest is
    # regenerated on next reconcile.
    try:
        _write_rig_folder(current_folder, merged)
    except XMLError as exc:
        raise ValidationFailedError(
            f"generated rig XML failed XSD validation: {exc}",
        ) from exc

    # Step 6: rename the folder if the nickname changed. POSIX-
    # atomic rename; the destination was confirmed absent above.
    if nickname_changed:
        os.rename(current_folder, new_folder)

    _logger.info(
        "rig_updated",
        extra={
            "rig_id": str(merged.id),
            "rig_nickname": new_sanitized,
            "jurisdiction": merged.jurisdiction.value,
            "folder_renamed": nickname_changed,
        },
    )
    return merged


# --------------------------------------------------------------------------- #
# set_star — D58 star transition 2 (idempotent move of the default rig)
# --------------------------------------------------------------------------- #


@with_writer_lock
def set_star(
    logbook_root: Path,
    user_id: str,
    rig_id: UUID,
) -> Rig:
    """Star a rig as the logbook's default for the jump-log form (D58).

    The only mutator for the ``starred`` flag. Idempotent: starring
    the already-starred rig is a no-op write (the response is the
    current rig unchanged). There is no DELETE counterpart — D58
    forbids explicit unstar; the star moves only by starring a
    different rig or by deleting the currently starred one.

    Algorithm (under the writer lock per D50):

      1. Resolve the target rig. 404 if missing.
      2. Walk all rigs and write ``starred=False`` on every rig
         that's currently starred *except* the target. This is
         defensive against invariant drift — a clean state has
         exactly one prior starred rig, but a manual XML edit or a
         crash-recovery state could leave multiple, and we clear
         them all in one pass.
      3. Write the target rig with ``starred=True`` (if not already).

    Crash recovery: a crash between step 2 and step 3 leaves the
    logbook in a "zero starred" intermediate. The LogJumpModal falls
    back to "no preselect" until the user's next ``set_star``, which
    re-runs step 2 (now clearing nothing) and step 3 (now setting
    the target) — self-healing without explicit reindex repair.

    Raises:
      NotFoundError: no rig with this id exists, or it's been
        soft-deleted (``.trash/`` is not walked by
        :func:`_find_rig_by_id`).
    """
    del user_id  # v0.1: see create_rig

    folder, target = _find_rig_by_id(logbook_root, rig_id)

    # Idempotency: if target is already the unique starred rig, no
    # writes. Check the cheap case first to avoid touching disk on a
    # double-click or a re-issued PUT.
    if target.starred:
        # Could still have invariant drift (a second rig also has
        # starred=True). Run the defensive clear to be safe; that
        # write skips the target.
        _clear_all_stars(logbook_root, exclude=target.id)
        # Re-read the target — it didn't change here, but a follow-up
        # observer expects a fresh updated_at if any write happened.
        # In the all-clean case we return the parsed-from-disk Rig.
        return _read_rig(folder)

    # Defensive clear, then stamp the target.
    _clear_all_stars(logbook_root, exclude=target.id)

    starred_target = target.model_copy(
        update={"starred": True, "updated_at": now_utc_iso()},
    )
    _write_rig_folder(folder, starred_target)

    _logger.info(
        "rig_starred",
        extra={
            "rig_id": str(starred_target.id),
            "rig_nickname": folder.name,
        },
    )
    return starred_target


# --------------------------------------------------------------------------- #
# reorder_rigs — D59 user-controlled carousel order
# --------------------------------------------------------------------------- #


@with_writer_lock
def reorder_rigs(
    logbook_root: Path,
    user_id: str,
    rig_ids: list[UUID],
) -> list[Rig]:
    """Rewrite each rig's ``display_order`` to match the caller's list.

    The single mutator for ``display_order`` after ``create_rig``
    stamps the initial value. ``rig_ids[0]`` becomes the leftmost
    rig (``display_order=0``), ``rig_ids[1]`` is next, and so on.

    Validates that ``rig_ids`` is exactly the set of non-trashed
    rig ids on disk:

      * no missing id (would leave a rig at an arbitrary position
        relative to the rest)
      * no extra id (caller asking us to reorder a rig that
        doesn't exist or has been trashed)
      * no duplicate id (ambiguous: which position wins?)

    On validation failure, raises :class:`ValidationFailedError`
    with a precise pointer; the on-disk state is untouched.

    On success, performs ``len(rig_ids)`` atomic writes — one per
    rig — under the writer lock. A crash mid-pass leaves the
    logbook in a partially-reordered state which the next reorder
    call corrects. The list still sorts coherently in the
    intermediate state because ``list_rigs`` is total-ordered.

    Raises:
      ValidationFailedError: ``rig_ids`` doesn't match the on-disk
        set (length mismatch, duplicate, unknown id, missing id).
    """
    del user_id  # v0.1: see create_rig

    existing = _read_all_rigs(logbook_root)
    existing_by_id: dict[UUID, tuple[Path, Rig]] = {
        r.id: (folder, r) for folder, r in existing
    }
    existing_ids = set(existing_by_id.keys())

    requested = list(rig_ids)
    requested_set = set(requested)

    # Validation: cardinality + identity. Each branch surfaces a
    # precise FieldError so the UI can tell the user what went
    # wrong (a partial drag stuck mid-network, a stale list from a
    # concurrent delete, etc.).
    if len(requested) != len(requested_set):
        raise ValidationFailedError(
            "rig_ids contains duplicate entries",
            errors=[
                FieldError(
                    pointer="#/rig_ids",
                    detail="duplicate ids are not allowed",
                ),
            ],
        )
    missing = existing_ids - requested_set
    extra = requested_set - existing_ids
    if missing or extra:
        errors: list[FieldError] = []
        if missing:
            errors.append(
                FieldError(
                    pointer="#/rig_ids",
                    detail=(
                        f"missing {len(missing)} rig id"
                        f"{'s' if len(missing) != 1 else ''}: "
                        f"{sorted(str(m) for m in missing)}"
                    ),
                )
            )
        if extra:
            errors.append(
                FieldError(
                    pointer="#/rig_ids",
                    detail=(
                        f"{len(extra)} unknown rig id"
                        f"{'s' if len(extra) != 1 else ''} "
                        f"(possibly trashed or never existed): "
                        f"{sorted(str(x) for x in extra)}"
                    ),
                )
            )
        raise ValidationFailedError(
            "rig_ids does not match the set of non-trashed rigs",
            errors=errors,
        )

    # Rewrite each rig with its new index. Each call goes through
    # _write_rig_folder → atomic_write + XSD validate (D2 + D10).
    # bumped_at is uniform across the batch so the timestamps
    # reflect "one reorder operation" rather than N separate
    # writes.
    bumped_at = now_utc_iso()
    out: list[Rig] = []
    for index, rid in enumerate(requested):
        folder, rig = existing_by_id[rid]
        # Skip the write when the value is already correct — saves
        # one rewrite per rig that didn't move. The whole batch is
        # already inside the lock so concurrent changes can't slip
        # between this read and the write below.
        if rig.display_order == index:
            out.append(rig)
            continue
        reordered = rig.model_copy(
            update={"display_order": index, "updated_at": bumped_at},
        )
        _write_rig_folder(folder, reordered)
        out.append(reordered)

    _logger.info(
        "rigs_reordered",
        extra={
            "rig_count": len(requested),
            "rig_ids": [str(rid) for rid in requested],
        },
    )
    return out


@with_writer_lock
def delete_rig(
    logbook_root: Path,
    user_id: str,
    rig_id: UUID,
) -> Path:
    """Soft-delete a rig folder to ``.trash/rigs/`` and clear refs (D37).

    R.2.0c.iii.a: per D37, "delete_rig clears assigned_rig_id on
    all four referenced components (returning them to inventory)
    before deleting the rig folder." Order: clear each of the four
    component refs first (atomic per-file write), then soft-delete
    the rig folder.

    Tolerates components that have gone missing out-of-band (e.g.
    a hand-edited inventory or a stale ref from before the D37
    cascade landed): a missing component file logs a WARNING and
    the cascade continues. The rig delete proceeds either way —
    refusing to delete the rig because of an inventory shape
    mismatch would leave the user stuck.

    Raises:
      NotFoundError: no rig with this id (already trashed, never
        existed, or only a partial-create stub remains).
    """
    del user_id  # v0.1: see create_rig
    folder, rig = _find_rig_by_id(logbook_root, rig_id)

    # D58 star auto-move: if the rig being deleted is starred and any
    # other rig remains, elect a successor and star it BEFORE the
    # soft-delete completes. Order rationale: stamping the successor
    # before the trash move means a crash window leaves either
    # "successor starred, target still present and starred" (two
    # starred — heals on next set_star) or "successor starred,
    # target gone" (clean). The alternative ordering would leave
    # "zero starred" in the crash window, which is the failure mode
    # we want to avoid since LogJumpModal then has no preselect.
    if rig.starred:
        candidates = [(f, r) for f, r in _read_all_rigs(logbook_root) if r.id != rig.id]
        if candidates:
            succ_folder, succ = _elect_successor_star(logbook_root, candidates)
            starred_succ = succ.model_copy(
                update={"starred": True, "updated_at": now_utc_iso()},
            )
            _write_rig_folder(succ_folder, starred_succ)
            _logger.info(
                "rig_star_auto_moved",
                extra={
                    "from_rig_id": str(rig.id),
                    "to_rig_id": str(succ.id),
                    "to_rig_nickname": succ_folder.name,
                },
            )
        # If no candidates, this was the only (or last) rig. The
        # invariant ("≥1 rig ⇒ exactly one starred") is trivially
        # satisfied after the delete because zero rigs remain.

    # D37 cascade: clear assigned_rig_id on each of the four refs
    # before moving the folder to trash. Tolerate "component file
    # already missing" — a hand-edited or pre-D37-cascade
    # inventory shouldn't block the rig delete.
    for field_name, _getter, assigner, kind in _COMPONENT_REGISTRY:
        component_id = getattr(rig, field_name)
        try:
            assigner(logbook_root, component_id, None)
        except NotFoundError:
            _logger.warning(
                "rig_delete_component_missing",
                extra={
                    "rig_id": str(rig_id),
                    "component_kind": kind,
                    "component_id": str(component_id),
                },
            )

    try:
        trashed = soft_delete(folder, logbook_root, subdir=_TRASH_SUBDIR)
    except FileNotFoundError as exc:
        # Race with a concurrent delete or out-of-band move.
        raise NotFoundError(f"rig {rig_id} not found") from exc

    _logger.info(
        "rig_deleted",
        extra={
            "rig_id": str(rig_id),
            "rig_nickname": folder.name,
            "trashed_to": str(trashed.relative_to(logbook_root)),
        },
    )
    return trashed


@with_writer_lock
def swap_main(
    logbook_root: Path,
    user_id: str,
    rig_id: UUID,
    new_main_id: UUID,
) -> Rig:
    """Swap a rig's current main canopy (D37, S.1).

    The dedicated jumper-facing operation referenced by D37 and the
    ``RigComponentSwapUnsupported`` error: PUT to ``rig`` cannot
    change ``current_main_id``; this function is the only path.

    Validation:

      * Rig exists (404 on miss).
      * If ``new_main_id`` equals the rig's current ``current_main_id``,
        the call is a no-op and returns the rig as-is. This makes
        the operation idempotent — clients can retry safely.
      * The new main exists (404 → translated to 422 with field
        pointer for parity with create_rig's D37 check).
      * The new main has ``status == active`` (422 ``main is X, not
        active``).
      * The new main is unassigned, OR is already assigned to *this*
        rig (the second case lets a partial-swap retry complete
        cleanly). Any other ``assigned_rig_id`` → 409
        ``component_already_assigned``.

    Three-step disk write, ordered for crash recovery:

      1. ``rig.xml`` first (with the new ``current_main_id``).
        ``_write_rig_folder`` is XSD-validate then ``atomic_write``,
        so a crash here leaves the previous rig.xml intact.
      2. Detach the old main (``main_service.set_assigned_rig_id``
        → ``None``). Tolerate a missing old main (out-of-band
        cleanup or a stale ref pre-D37 cascade).
      3. Attach the new main (``main_service.set_assigned_rig_id``
        → this rig).

    Crash story:

      * Crash after (1), before (2): rig points at new main, but
        old main still has ``assigned_rig_id`` pointing at this rig.
        Reconcile (a future slice) detects ``rig.current_main_id !=
        old_main.id`` && ``old_main.assigned_rig_id == rig.id`` and
        clears the old main.
      * Crash after (2), before (3): rig points at new main, old
        main detached, new main has stale ``assigned_rig_id``
        (None or whatever it was). Reconcile detects ``rig.
        current_main_id == new_main.id`` && ``new_main.
        assigned_rig_id != rig.id`` and re-attaches.

    Both crash states converge on a clean steady state when the
    user retries swap_main with the same ``new_main_id``: the
    no-op fast path or the idempotent ``rig_id_being_created``
    branch in the validator both pass, and the writes are
    re-applied.

    ``user_id`` is accepted per D8 but unused in v0.1.

    Raises:
      NotFoundError: no rig with this id (404).
      ValidationFailedError: new main missing or not active (422).
      ComponentAlreadyAssigned: new main on a different rig (409).
    """
    del user_id  # v0.1: see create_rig

    folder, current = _find_rig_by_id(logbook_root, rig_id)

    # Idempotent fast path: client picked the same main that's
    # already on the rig. No write, no log line — just echo back.
    if new_main_id == current.current_main_id:
        return current

    # D37 validation. ``rig_id_being_created=current.id`` lets us
    # accept a new main whose ``assigned_rig_id`` is *already* this
    # rig (partial-swap recovery) — the validator treats it as the
    # idempotent retry case.
    _validate_component_for_assignment(
        logbook_root,
        field_name="new_main_id",
        component_id=new_main_id,
        component_kind="main",
        getter=main_service.get_main,
        rig_id_being_created=current.id,
    )

    # Build the merged rig with the new ``current_main_id``.
    # Everything else preserved (id, created_at, repack_history,
    # the other three component refs, notes_log). updated_at is
    # bumped to the swap moment.
    old_main_id = current.current_main_id
    try:
        merged = Rig(
            id=current.id,
            nickname=current.nickname,
            jurisdiction=current.jurisdiction,
            current_main_id=new_main_id,
            current_reserve_id=current.current_reserve_id,
            current_aad_id=current.current_aad_id,
            current_container_id=current.current_container_id,
            repack_history=current.repack_history,
            notes_log=current.notes_log,
            created_at=current.created_at,
            updated_at=now_utc_iso(),
        )
    except ValidationError as exc:
        raise validation_failed_from_pydantic(exc, "rig validation failed") from exc

    # Step 1: write rig.xml first. _write_rig_folder XSD-validates
    # before the atomic_write, so a bad shape here leaves the old
    # rig.xml intact.
    try:
        _write_rig_folder(folder, merged)
    except XMLError as exc:
        raise ValidationFailedError(
            f"generated rig XML failed XSD validation: {exc}",
        ) from exc

    # Step 2: detach the old main. Tolerate a missing file —
    # mirroring delete_rig's posture, we don't refuse to complete
    # a swap because of an unrelated inventory shape mismatch.
    try:
        main_service.set_assigned_rig_id(logbook_root, old_main_id, None)
    except NotFoundError:
        _logger.warning(
            "swap_main_old_main_missing",
            extra={
                "rig_id": str(rig_id),
                "old_main_id": str(old_main_id),
            },
        )

    # Step 3: attach the new main. If it was already on this rig
    # (the partial-swap recovery case), set_assigned_rig_id is a
    # no-op write of the same value.
    main_service.set_assigned_rig_id(logbook_root, new_main_id, current.id)

    _logger.info(
        "rig_main_swapped",
        extra={
            "rig_id": str(rig_id),
            "old_main_id": str(old_main_id),
            "new_main_id": str(new_main_id),
        },
    )
    return merged

