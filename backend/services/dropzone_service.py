"""Dropzone service — all business logic for the D44 dropzone entity.

Mirrors ``jump_service`` (D7): every public function takes
``logbook_root: Path`` (D2) and ``user_id: str`` (D8) so the REST
adapter stays a thin translator. In v0.1 ``user_id`` is accepted for
forward compatibility but not used to scope visibility — a dropzone is
a real-world location (its environment is a function of the place,
not the jumper), so DZs are conceptually shared across users on the
same machine. When multi-user lands, a future D-entry will decide
whether DZs become per-user, per-tenant, or stay shared.

Storage shape per D44:

    logbook_root/
      dropzones/
        <uuid>.xml         # one flat file per DZ
      .trash/
        dropzones/
          <ts>_<uuid>.xml/<uuid>.xml   # post-soft-delete (D19 pattern)

Invariants:
  * XSD validation on every write (D2).
  * Atomic write via ``storage.filesystem.atomic_write`` (D10).
  * Soft delete via ``storage.trash.soft_delete_file`` (D19).
  * No SHA256SUMS — dropzones are flat single files; manifest
    integrity belongs on folder-with-attachments entities (jumps,
    eventually rigs). XSD validation + the hardened parser (D2) are
    the integrity surface here.
  * Listing walks the ``dropzones/`` directory in v0.1 (R.D.1). R.D.3
    swaps in a SQLite ``dropzones`` table for O(rows) at SQLite speed.
"""
from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError

from ..api.errors import (
    NotFoundError,
    ValidationFailedError,
    validation_failed_from_pydantic,
)
from ..models.common import SCHEMA_NAMESPACE_V1
from ..models.dropzone import (
    Dropzone,
    DropzoneCreate,
    DropzoneSummary,
    DropzoneUpdate,
    Environment,
)
from ..storage.filesystem import atomic_write
from ..storage.index import open_index
from ..storage.trash import soft_delete_file
from ..xml.serialize import (
    dropzone_to_bytes,
    dropzone_to_element,
    element_to_dropzone,
)
from ..xml.validator import XMLError, validate
from ..xml.validator import parse as xml_parse
from ._timestamps import now_utc_iso
from ._write_lock import with_writer_lock

_DROPZONES_DIR = "dropzones"
_TRASH_SUBDIR = "dropzones"

_logger = logging.getLogger("backend.services.dropzone")




def _dropzone_path(logbook_root: Path, dropzone_id: UUID) -> Path:
    """Resolve the on-disk path for a dropzone's XML file.

    Uses the UUID directly as the filename (no sanitization needed
    because UUIDs are guaranteed safe). Caller is responsible for
    ensuring ``logbook_root / dropzones`` exists.
    """
    return logbook_root / _DROPZONES_DIR / f"{dropzone_id}.xml"


def _read_dropzone(path: Path) -> Dropzone:
    """Parse + XSD-validate one ``dropzone.xml`` file.

    Raises ``NotFoundError`` if the file is missing,
    ``ValidationFailedError`` if the contents don't validate, and
    propagates other ``OSError``s (permission, I/O) unmodified — those
    are infrastructure problems the API layer surfaces as 500s.
    """
    if not path.is_file():
        raise NotFoundError(f"dropzone file not found: {path.name}")
    try:
        element = xml_parse(path.read_bytes())
        validate(element)
    except XMLError as exc:
        # Disk corruption or hand-edit broke the XML. Service layer
        # surfaces it as a 422 for the API; an operator can re-edit
        # or delete the file and rebuild from a backup.
        raise ValidationFailedError(
            f"dropzone {path.stem} is invalid: {exc}",
        ) from exc
    return element_to_dropzone(element)


def _write_dropzone(logbook_root: Path, dz: Dropzone) -> None:
    """Serialize, XSD-validate, and atomically write a Dropzone to disk.

    Mirrors the jump_service pattern: validate the produced XML
    BEFORE the atomic write so a failed XSD check leaves the previous
    file (if any) untouched. D2 + D10 invariants both apply.
    """
    element = dropzone_to_element(dz)
    validate(element)  # D2: every write XSD-validated before persistence
    path = _dropzone_path(logbook_root, dz.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, dropzone_to_bytes(dz))


def _upsert_index_row(logbook_root: Path, dz: Dropzone) -> None:
    """INSERT OR REPLACE the row for ``dz`` in the ``dropzones`` index.

    Called from create_dropzone and update_dropzone after the XML write
    has succeeded — D3 ordering: XML first, index second. A crash
    between the two leaves the file on disk and the index stale; the
    next ``reindex_from_xml`` reconciles it.

    ``created_at`` and ``updated_at`` MUST be present on ``dz`` —
    they're NOT NULL in the schema. The service stamps them on every
    create/update; reindex falls back to file mtime if a hand-edited
    XML omits them (parallel to jump_service / D32).

    D60: writes the ``starred`` column from ``dz.starred``. INSERT OR
    REPLACE overwrites the prior row (if any), so a star transfer
    that re-upserts both the cleared and the newly-stamped DZ produces
    a coherent index state in two writes.
    """
    assert dz.created_at is not None and dz.updated_at is not None, (
        "service-authored Dropzone must carry both timestamps before index write"
    )
    result = open_index(logbook_root)
    try:
        result.conn.execute(
            "INSERT OR REPLACE INTO dropzones "
            "(id, name, city, country, environment, schema_ns, "
            "created_at, updated_at, starred) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(dz.id),
                dz.name,
                dz.city,
                dz.country,
                dz.environment.value,
                SCHEMA_NAMESPACE_V1,
                dz.created_at,
                dz.updated_at,
                # D60: 1 = starred, 0 = not. The column is NOT NULL
                # DEFAULT 0 (v10 schema), so an explicit write keeps
                # the value coherent with the on-disk <starred>.
                1 if dz.starred else 0,
            ),
        )
    finally:
        result.conn.close()


def _delete_index_row(logbook_root: Path, dropzone_id: UUID) -> None:
    """DELETE the row for ``dropzone_id`` from the ``dropzones`` index.

    Called from delete_dropzone after the file has moved into trash.
    A crash between the move and the delete leaves the index pointing
    at a now-trashed UUID; the next reindex notices the missing file
    and removes the row.
    """
    result = open_index(logbook_root)
    try:
        result.conn.execute(
            "DELETE FROM dropzones WHERE id = ?", (str(dropzone_id),)
        )
    finally:
        result.conn.close()


# --------------------------------------------------------------------------- #
# D60 — Starred-dropzone helpers (auto-star, set_star, successor election)
# --------------------------------------------------------------------------- #


def _count_dropzones(logbook_root: Path) -> int:
    """Return the number of non-trashed dropzones via the SQLite index.

    Used by D60 transition 1 (create-auto-star): the first dropzone in
    a fresh logbook is born starred; subsequent creates leave the
    existing star alone. The index is the canonical projection for
    "what DZs are live" — the trash directory lives outside the
    ``dropzones`` table.
    """
    result = open_index(logbook_root)
    try:
        row = result.conn.execute(
            "SELECT COUNT(*) AS n FROM dropzones"
        ).fetchone()
        return int(row["n"])
    finally:
        result.conn.close()


def _read_starred_ids(logbook_root: Path) -> list[UUID]:
    """Return the ids of every currently-starred dropzone (via index).

    Driven by ``starred = 1`` on the dropzones table (v10). A
    well-shaped logbook returns 0 or 1 entries; >1 indicates drift
    (hand-edited XML, partial-write crash before reindex healed it,
    pre-D60 file with a `<starred>` element manually set on multiple
    rows). The caller (``_clear_all_stars``) is intentionally
    defensive against that case — clearing every starred row keeps
    set_star's two-write transition self-healing.
    """
    result = open_index(logbook_root)
    try:
        rows = result.conn.execute(
            "SELECT id FROM dropzones WHERE starred = 1"
        ).fetchall()
        return [UUID(r["id"]) for r in rows]
    finally:
        result.conn.close()


def _clear_all_stars(
    logbook_root: Path, *, exclude: UUID | None = None
) -> None:
    """Write ``starred=False`` on every currently-starred dropzone.

    Used by :func:`set_star` to enforce the D60 invariant defensively
    — even if the on-disk state has drifted to multiple starred DZs
    (manual XML edit, pre-D60 file, crash recovery), this clears all
    of them so the caller can stamp the single target without
    leaving stale stars behind. Each rewrite goes through
    :func:`_write_dropzone` (XSD validate + atomic write per D2/D10)
    and ``_upsert_index_row`` (D3 ordering: XML first then index).

    ``exclude`` skips one dropzone id (typically the upcoming
    set_star target) so the target doesn't need a redundant
    clear-then-set write.

    Caller must already hold the writer lock (D50). Every public
    transition function in this module is ``@with_writer_lock``
    decorated, so the helpers run under the same acquisition.
    """
    now = now_utc_iso()
    for starred_id in _read_starred_ids(logbook_root):
        if exclude is not None and starred_id == exclude:
            continue
        try:
            current = _read_dropzone(_dropzone_path(logbook_root, starred_id))
        except NotFoundError:
            # Index points at a file that no longer exists — index
            # drift the next reindex will heal. Skip rather than
            # raise; D60 transitions should never refuse to converge
            # because of an unrelated stale index row.
            _logger.warning(
                "dropzone_clear_star_missing",
                extra={"dropzone_id": str(starred_id)},
            )
            continue
        cleared = current.model_copy(
            update={"starred": False, "updated_at": now},
        )
        _write_dropzone(logbook_root, cleared)
        _upsert_index_row(logbook_root, cleared)


def _elect_successor_star(
    logbook_root: Path, candidate_ids: list[UUID]
) -> UUID | None:
    """Pick the next dropzone to star per D60 transition 3.

    Election rule:

      1. *Most recently jumped at.* ``SELECT dropzone_id, MAX(date)
         FROM jumps WHERE dropzone_id IN (?,?,...) GROUP BY
         dropzone_id`` over the candidate ids. The candidate with
         the latest ``MAX(date)`` wins. Powered by the v10
         ``jumps.dropzone_id`` column (D33 precedent for rigs).
      2. *Alphabetical tiebreaker.* When no candidate has any jumps
         logged against it (or several share the same MAX(date)),
         fall back to ``ORDER BY name COLLATE NOCASE, city COLLATE
         NOCASE, id``. This matches list_dropzones's on-screen
         order so the star moves to the alphabetical-first
         remaining DZ — the natural mental-model default for a
         picker. Dropzones do not have a display_order (D59 is
         rig-only), so the alphabetical surface is the canonical
         "first" to fall back to.

    Returns ``None`` when ``candidate_ids`` is empty — the caller
    handles the "last DZ deleted, no successor to elect" case
    (D60 invariant trivially satisfied with zero DZs remaining).
    """
    if not candidate_ids:
        return None

    placeholders = ",".join("?" * len(candidate_ids))
    str_ids = [str(cid) for cid in candidate_ids]
    result = open_index(logbook_root)
    try:
        # Primary rule: latest jump per candidate.
        rows = result.conn.execute(
            # placeholders is built from len(candidate_ids), not user
            # input, so the f-string is safe (matches the rig
            # _elect_successor_star pattern).
            f"SELECT dropzone_id, MAX(date) AS last_jump_date "  # noqa: S608
            f"FROM jumps WHERE dropzone_id IN ({placeholders}) "
            f"GROUP BY dropzone_id",
            tuple(str_ids),
        ).fetchall()
        best_id: str | None = None
        best_date: str = ""
        for row in rows:
            if row["last_jump_date"] and row["last_jump_date"] > best_date:
                best_date = row["last_jump_date"]
                best_id = row["dropzone_id"]
        if best_id is not None:
            return UUID(best_id)

        # Tiebreaker: alphabetical from the dropzones index. Pulls
        # the lowest (name, city, id) tuple among the candidates.
        row = result.conn.execute(
            f"SELECT id FROM dropzones WHERE id IN ({placeholders}) "  # noqa: S608
            f"ORDER BY name COLLATE NOCASE, city COLLATE NOCASE, id "
            f"LIMIT 1",
            tuple(str_ids),
        ).fetchone()
        if row is not None:
            return UUID(row["id"])
    finally:
        result.conn.close()

    # Defensive: candidates were supplied but none resolve to a row
    # in the dropzones index (index drift). The next reindex heals;
    # surface as "no successor" so delete_dropzone falls back to the
    # zero-starred intermediate.
    return None


@with_writer_lock
def create_dropzone(
    logbook_root: Path,
    user_id: str,
    payload: DropzoneCreate,
) -> Dropzone:
    """Create a new dropzone record at ``dropzones/<uuid>.xml``.

    Server-assigns the UUID, stamps ``created_at`` / ``updated_at``
    via ``_now_utc_iso``, validates against the XSD, and writes the
    file atomically. Returns the persisted Dropzone (with the
    server-assigned id and timestamps).

    Raises:
      ValidationFailedError: when Pydantic / XSD reject the shape.
        The REST layer's existing handler turns this into a 422
        problem+json with field pointers per D16.
    """
    del user_id  # v0.1: dropzones are shared; reserved for forward compat
    now = now_utc_iso()
    # D60 transition 1: auto-star when the logbook is empty. The count
    # is read inside the writer lock so concurrent creates can't both
    # see "zero" and each stamp themselves starred (D50 makes that
    # structurally unreachable in v0.1, but the explicit ordering
    # documents the contract). Subsequent creates carry the default
    # starred=False so the existing star is left untouched.
    auto_star = _count_dropzones(logbook_root) == 0
    try:
        dz = Dropzone(
            id=uuid4(),
            **payload.model_dump(),
            starred=auto_star,
            created_at=now,
            updated_at=now,
        )
    except ValidationError as exc:
        # Should not happen — DropzoneCreate already validated the
        # caller's payload. Defensive: a future field added on
        # Dropzone but not on DropzoneCreate could trip this.
        raise validation_failed_from_pydantic(exc, "dropzone validation failed") from exc

    try:
        _write_dropzone(logbook_root, dz)
    except XMLError as exc:
        # Generated XML failed XSD validation. Means a model field
        # passed Pydantic but is shaped wrong for the schema — bug
        # in serialize, not user input. Still surface cleanly.
        raise ValidationFailedError(
            f"generated dropzone XML failed XSD validation: {exc}",
        ) from exc

    # D3 ordering: index after XML so a crash between the two leaves
    # the authoritative file on disk; reindex_from_xml repopulates the
    # missing row on next launch / verify.
    _upsert_index_row(logbook_root, dz)

    # ``name`` is reserved by ``LogRecord`` (it's the logger's own
    # name field). Use ``dropzone_name`` so structured logging
    # doesn't blow up the request — same lesson as task #45's
    # ``filename`` collision in jump_service.
    _logger.info(
        "dropzone_created",
        extra={
            "dropzone_id": str(dz.id),
            "dropzone_name": dz.name,
            "country": dz.country,
            "environment": dz.environment.value,
        },
    )
    return dz


def get_dropzone(
    logbook_root: Path,
    user_id: str,
    dropzone_id: UUID,
) -> Dropzone:
    """Return the dropzone with the given id, or raise NotFoundError."""
    del user_id  # v0.1: see create_dropzone
    return _read_dropzone(_dropzone_path(logbook_root, dropzone_id))


def list_dropzones(
    logbook_root: Path,
    user_id: str,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[DropzoneSummary]:
    """List dropzones as compact summaries, ordered by name.

    R.D.3 implementation reads from the SQLite ``dropzones`` index
    (D3) using the ``idx_dropzones_name`` covering index — no per-row
    XML parse, no filesystem walk. The DZ picker on the LogJumpModal
    can call this on every keystroke without paying a parse cost.

    The index is rebuildable from XML (D3) — if it gets stale or
    deleted, ``open_index`` rebuilds the schema and the next
    ``reindex_from_xml`` pass repopulates rows. The list reflects
    whatever the index currently knows; mid-write inconsistencies
    are bounded by the create/update/delete services maintaining
    the index in step with disk (D3 ordering: XML first, index
    second).
    """
    del user_id  # v0.1: see create_dropzone
    result = open_index(logbook_root)
    try:
        # NOCASE collation matches the idx_dropzones_name index
        # definition so SQLite uses the index for ORDER BY without a
        # runtime sort. limit/offset apply at the SQL boundary so
        # large logbooks don't materialize the full set in Python.
        # D60: ``starred`` joins the projection so the LogJumpModal
        # can find the default DZ in one round-trip. Column is
        # NOT NULL DEFAULT 0 (v10 schema) so the value is always a
        # well-defined integer.
        sql = (
            "SELECT id, name, city, country, environment, starred "
            "FROM dropzones "
            "ORDER BY name COLLATE NOCASE, city COLLATE NOCASE"
        )
        params: tuple[int, ...] = ()
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params = (limit, offset)
        elif offset:
            # SQLite requires LIMIT to use OFFSET; -1 means "no
            # bound" per https://www.sqlite.org/lang_select.html
            # §LIMIT ("If the LIMIT expression evaluates to a
            # negative value, then there is no upper bound").
            sql += " LIMIT -1 OFFSET ?"
            params = (offset,)
        rows = result.conn.execute(sql, params).fetchall()
    finally:
        result.conn.close()

    return [
        DropzoneSummary(
            id=UUID(row["id"]),
            name=row["name"],
            city=row["city"],
            country=row["country"],
            environment=Environment(row["environment"]),
            # D60: SQLite returns INTEGER 0/1; coerce to bool for the
            # response shape. ``bool(0)`` and ``bool(1)`` give the
            # expected mapping; ``bool(None)`` (impossible since
            # NOT NULL) would map to False as a defensive fallback.
            starred=bool(row["starred"]),
        )
        for row in rows
    ]


@with_writer_lock
def update_dropzone(
    logbook_root: Path,
    user_id: str,
    dropzone_id: UUID,
    payload: DropzoneUpdate,
) -> Dropzone:
    """Full-replace update of a dropzone record's editable fields (D44).

    Preserves ``id`` and ``created_at``; bumps ``updated_at``.
    Re-validates Pydantic + XSD before writing. Like ``update_jump``
    (D31), this is a full replace — clients send the current record
    back with the desired diff applied.
    """
    del user_id  # v0.1: see create_dropzone
    current = _read_dropzone(_dropzone_path(logbook_root, dropzone_id))
    try:
        merged = Dropzone(
            id=current.id,                  # immutable
            created_at=current.created_at,  # immutable
            updated_at=now_utc_iso(),
            # D60: ``starred`` is service-controlled — DropzoneUpdate
            # deliberately does not expose it (mutation only via
            # PUT /dropzones/{id}/star). Preserve the current value
            # so a metadata edit cannot silently drop the star and
            # leave the logbook in a "zero starred" intermediate.
            starred=current.starred,
            **payload.model_dump(),
        )
    except ValidationError as exc:
        raise validation_failed_from_pydantic(exc, "dropzone validation failed") from exc

    try:
        _write_dropzone(logbook_root, merged)
    except XMLError as exc:
        raise ValidationFailedError(
            f"generated dropzone XML failed XSD validation: {exc}",
        ) from exc

    # D3 ordering: XML first, then index. INSERT OR REPLACE handles
    # both the row-exists (normal update) and row-missing (a prior
    # crash left the index stale) cases identically.
    _upsert_index_row(logbook_root, merged)

    _logger.info(
        "dropzone_updated",
        extra={
            "dropzone_id": str(merged.id),
            # See create_dropzone — "name" collides with LogRecord.name.
            "dropzone_name": merged.name,
            "country": merged.country,
            "environment": merged.environment.value,
        },
    )
    return merged


@with_writer_lock
def delete_dropzone(
    logbook_root: Path,
    user_id: str,
    dropzone_id: UUID,
) -> Path:
    """Soft-delete a dropzone to ``.trash/dropzones/`` per D19 / D44.

    Returns the new path inside ``.trash`` so callers can log the
    move. Jumps that reference ``<dropzone_id>`` keep the reference;
    the wear math (D45) falls back to the main's default flags on
    next reindex. No cascade — that conflates "clean up DZ list"
    with "delete jumps", which is the wrong UX (D44 §Alternatives).

    Raises NotFoundError when the file doesn't exist.
    """
    del user_id  # v0.1: see create_dropzone
    path = _dropzone_path(logbook_root, dropzone_id)
    if not path.is_file():
        raise NotFoundError(f"dropzone {dropzone_id} not found")

    # D60 transition 3: auto-move the star to a successor BEFORE the
    # soft-delete commits when the target is starred and other
    # dropzones remain. Order rationale (mirrors rig_service): stamping
    # the successor before the trash move means a crash window leaves
    # either "successor starred + target still present and starred"
    # (two stars — heals on the next set_star) or "successor starred,
    # target gone" (clean). The alternative ordering would leave "zero
    # starred" in the crash window — the failure mode we want to
    # avoid since LogJumpModal then has no preselect.
    current = _read_dropzone(path)
    if current.starred:
        # Candidate set = every other DZ in the index. Pulled from
        # the SQLite projection (canonical "live DZs" surface per D3).
        result = open_index(logbook_root)
        try:
            rows = result.conn.execute(
                "SELECT id FROM dropzones WHERE id != ?",
                (str(dropzone_id),),
            ).fetchall()
        finally:
            result.conn.close()
        candidate_ids = [UUID(r["id"]) for r in rows]

        successor_id = _elect_successor_star(logbook_root, candidate_ids)
        if successor_id is not None:
            try:
                succ = _read_dropzone(_dropzone_path(logbook_root, successor_id))
            except NotFoundError:
                # Index drift — successor row exists but the file is
                # gone. Skip the transfer; the post-delete state is
                # "zero starred" which the next set_star heals. The
                # next reindex also removes the orphan index row.
                _logger.warning(
                    "dropzone_star_successor_missing",
                    extra={
                        "from_dropzone_id": str(dropzone_id),
                        "to_dropzone_id": str(successor_id),
                    },
                )
            else:
                starred_succ = succ.model_copy(
                    update={
                        "starred": True,
                        "updated_at": now_utc_iso(),
                    },
                )
                _write_dropzone(logbook_root, starred_succ)
                _upsert_index_row(logbook_root, starred_succ)
                _logger.info(
                    "dropzone_star_auto_moved",
                    extra={
                        "from_dropzone_id": str(dropzone_id),
                        "to_dropzone_id": str(successor_id),
                        "to_dropzone_name": starred_succ.name,
                    },
                )
        # If no successor (last DZ in the logbook), the invariant
        # "≥1 DZ ⇒ exactly one starred" is trivially satisfied
        # after the delete because zero DZs remain.

    try:
        trashed = soft_delete_file(path, logbook_root, _TRASH_SUBDIR)
    except FileNotFoundError as exc:
        # Race with a concurrent delete (multi-process or out-of-band
        # filesystem move). The end state is the same as success
        # from the caller's perspective, but surface honestly.
        raise NotFoundError(f"dropzone {dropzone_id} not found") from exc

    # D3 ordering: trash move first (file leaves the active set), then
    # index DELETE. A crash between the two leaves the index pointing
    # at a now-trashed UUID; the next reindex_from_xml notices the
    # missing file and removes the row.
    _delete_index_row(logbook_root, dropzone_id)

    _logger.info(
        "dropzone_deleted",
        extra={
            "dropzone_id": str(dropzone_id),
            "trashed_to": trashed.relative_to(logbook_root).as_posix(),
        },
    )
    return trashed


# --------------------------------------------------------------------------- #
# set_star — D60 transition 2 (idempotent move of the default DZ)
# --------------------------------------------------------------------------- #


@with_writer_lock
def set_star(
    logbook_root: Path,
    user_id: str,
    dropzone_id: UUID,
) -> Dropzone:
    """Star a dropzone as the logbook's default for the jump-log form (D60).

    The only mutator for the ``starred`` flag. Idempotent: starring
    the already-starred dropzone is a no-op write (the response is
    the current DZ unchanged). There is no DELETE counterpart — D60
    forbids explicit unstar; the star moves only by starring a
    different DZ or by deleting the currently starred one.

    Algorithm (under the writer lock per D50):

      1. Resolve the target dropzone. 404 if missing or trashed.
      2. Walk every currently-starred DZ (via the index) and write
         ``starred=False`` on each *except* the target. This is
         defensive against invariant drift — a clean state has
         exactly one prior starred DZ, but a manual XML edit or a
         crash-recovery state could leave multiple, and we clear
         them all in one pass.
      3. Write the target dropzone with ``starred=True`` (if not
         already starred on disk).

    Crash recovery: a crash between step 2 and step 3 leaves the
    logbook in a "zero starred" intermediate. The LogJumpModal falls
    back to "no preselect" until the user's next ``set_star``, which
    re-runs step 2 (now clearing nothing) and step 3 (now setting
    the target) — self-healing without explicit reindex repair.

    Each disk write goes through ``_write_dropzone`` (XSD validate
    BEFORE atomic_write per D2 + D10) and ``_upsert_index_row``
    (D3 ordering: XML first, then index). Same posture as every
    other dropzone-service write.

    Raises:
      NotFoundError: no dropzone with this id exists, or it has been
        soft-deleted (``.trash/dropzones/`` is not walked here).
    """
    del user_id  # v0.1: see create_dropzone

    target_path = _dropzone_path(logbook_root, dropzone_id)
    if not target_path.is_file():
        raise NotFoundError(f"dropzone {dropzone_id} not found")
    target = _read_dropzone(target_path)

    # Idempotency: if target is already starred, only run the
    # defensive clear (other DZs might also be starred from drift)
    # and return the current state. No write on the target itself.
    if target.starred:
        _clear_all_stars(logbook_root, exclude=target.id)
        # Re-read in case the defensive clear was the only write —
        # if the target's row was untouched, this returns target
        # as-is; if the file changed for some other reason between
        # the read and the lock, the caller gets the freshest copy.
        return _read_dropzone(target_path)

    # Defensive clear of every starred DZ (skipping the target),
    # then stamp the target. Two-write transition; the crash window
    # between them is "zero starred" which heals on the next mutation.
    _clear_all_stars(logbook_root, exclude=target.id)

    starred_target = target.model_copy(
        update={"starred": True, "updated_at": now_utc_iso()},
    )
    _write_dropzone(logbook_root, starred_target)
    _upsert_index_row(logbook_root, starred_target)

    _logger.info(
        "dropzone_starred",
        extra={
            "dropzone_id": str(starred_target.id),
            "dropzone_name": starred_target.name,
        },
    )
    return starred_target


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
