"""Person service — all business logic for the D54 person entity.

Mirrors ``dropzone_service`` (D7, D44): every public function takes
``logbook_root: Path`` (D2) and ``user_id: str`` (D8) so the REST
adapter (Phase 2c) stays a thin translator. In v0.1 ``user_id`` is
accepted for forward compatibility but not used to scope visibility —
people are real-world contacts (jumpers you've flown with, packers
who packed for you), conceptually shared across users on the same
machine. When multi-user lands a future D-entry decides whether
People become per-user, per-tenant, or stay shared.

Storage shape per D54:

    logbook_root/
      people/
        <uuid>.xml         # one flat file per person (mirrors D44)
      .trash/
        people/
          <ts>_<uuid>.xml  # post-soft-delete (D19 pattern)

Invariants:
  * XSD validation on every write (D2).
  * Atomic write via ``storage.filesystem.atomic_write`` (D10).
  * NFC normalization on the name field on every write (D4) so the
    same logical name lands on equal bytes regardless of input form.
  * Soft delete via ``storage.trash.soft_delete_file`` (D19). Jumps
    that reference the deleted person keep their UUID; resolution
    is soft (D54 §Decision) so stale refs render as "Unknown person
    <short-uuid>" until the user edits them or recreates the Person.
  * No SHA256SUMS — people are flat single files; manifest integrity
    belongs on folder-with-attachments entities (jumps).
  * Listing reads from the SQLite ``people`` index (Phase 2b) using
    the ``idx_people_name`` covering index — no per-row XML parse.
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
from ..models.person import Person, PersonCreate, PersonSummary, PersonUpdate
from ..storage.filesystem import atomic_write, normalize_nfc
from ..storage.index import open_index
from ..storage.trash import soft_delete_file
from ..xml.serialize import (
    element_to_person,
    person_to_bytes,
    person_to_element,
)
from ..xml.validator import XMLError, validate
from ..xml.validator import parse as xml_parse
from ._timestamps import now_utc_iso
from ._write_lock import with_writer_lock

_PEOPLE_DIR = "people"
_TRASH_SUBDIR = "people"

_logger = logging.getLogger("backend.services.people")




def _person_path(logbook_root: Path, person_id: UUID) -> Path:
    """Resolve the on-disk path for a person's XML file.

    Uses the UUID directly as the filename (no sanitization needed
    because UUIDs are guaranteed safe). Caller is responsible for
    ensuring ``logbook_root / people`` exists.
    """
    return logbook_root / _PEOPLE_DIR / f"{person_id}.xml"


def _read_person(path: Path) -> Person:
    """Parse + XSD-validate one ``person.xml`` file.

    Raises ``NotFoundError`` if the file is missing,
    ``ValidationFailedError`` if the contents don't validate, and
    propagates other ``OSError``s (permission, I/O) unmodified —
    those are infrastructure problems the API layer surfaces as 500s.
    """
    if not path.is_file():
        raise NotFoundError(f"person file not found: {path.name}")
    try:
        element = xml_parse(path.read_bytes())
        validate(element)
    except XMLError as exc:
        # Disk corruption or hand-edit broke the XML. Service layer
        # surfaces it as a 422 for the API; an operator can re-edit
        # or delete the file and rebuild from a backup.
        raise ValidationFailedError(
            f"person {path.stem} is invalid: {exc}",
        ) from exc
    return element_to_person(element)


def _write_person(logbook_root: Path, p: Person) -> None:
    """Serialize, XSD-validate, and atomically write a Person to disk.

    Mirrors the dropzone_service pattern: validate the produced XML
    BEFORE the atomic write so a failed XSD check leaves the previous
    file (if any) untouched. D2 + D10 invariants both apply.
    """
    element = person_to_element(p)
    validate(element)  # D2: every write XSD-validated before persistence
    path = _person_path(logbook_root, p.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, person_to_bytes(p))


def _upsert_index_row(logbook_root: Path, p: Person) -> None:
    """INSERT OR REPLACE the row for ``p`` in the ``people`` index.

    Called from create/update after the XML write has succeeded —
    D3 ordering: XML first, index second. A crash between the two
    leaves the file on disk and the index stale; the next
    ``reindex_from_xml`` reconciles.

    ``created_at`` and ``updated_at`` MUST be present on ``p`` —
    they're NOT NULL in the schema. The service stamps them on every
    create/update; reindex falls back to file mtime if a hand-edited
    XML omits them (parallel to dropzone_service / D32).
    """
    assert p.created_at is not None and p.updated_at is not None, (
        "service-authored Person must carry both timestamps before index write"
    )
    result = open_index(logbook_root)
    try:
        result.conn.execute(
            "INSERT OR REPLACE INTO people "
            "(id, name, schema_ns, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                str(p.id),
                p.name,
                SCHEMA_NAMESPACE_V1,
                p.created_at,
                p.updated_at,
            ),
        )
    finally:
        result.conn.close()


def _delete_index_row(logbook_root: Path, person_id: UUID) -> None:
    """DELETE the row for ``person_id`` from the ``people`` index.

    Called from delete_person after the file has moved into trash.
    A crash between the move and the delete leaves the index
    pointing at a now-trashed UUID; the next reindex_from_xml
    notices the missing file and removes the row.
    """
    result = open_index(logbook_root)
    try:
        result.conn.execute(
            "DELETE FROM people WHERE id = ?", (str(person_id),)
        )
    finally:
        result.conn.close()


@with_writer_lock
def create_person(
    logbook_root: Path,
    user_id: str,
    payload: PersonCreate,
) -> Person:
    """Create a new person record at ``people/<uuid>.xml``.

    Server-assigns the UUID, NFC-normalizes the name (D4), stamps
    ``created_at`` / ``updated_at``, validates against the XSD, and
    writes the file atomically. Returns the persisted Person (with
    server-assigned id and timestamps).

    Raises:
      ValidationFailedError: when Pydantic / XSD reject the shape.
        The REST layer's existing handler turns this into a 422
        problem+json with field pointers per D16.
    """
    del user_id  # v0.1: people are shared; reserved for forward compat
    now = now_utc_iso()
    try:
        p = Person(
            id=uuid4(),
            name=normalize_nfc(payload.name),
            notes=payload.notes,
            created_at=now,
            updated_at=now,
        )
    except ValidationError as exc:
        # Should not happen — PersonCreate already validated the
        # caller's payload. Defensive: a future field added on
        # Person but not on PersonCreate could trip this.
        raise validation_failed_from_pydantic(exc, "person validation failed") from exc

    try:
        _write_person(logbook_root, p)
    except XMLError as exc:
        # Generated XML failed XSD validation. Means a model field
        # passed Pydantic but is shaped wrong for the schema — bug
        # in serialize, not user input. Still surface cleanly.
        raise ValidationFailedError(
            f"generated person XML failed XSD validation: {exc}",
        ) from exc

    # D3 ordering: index after XML so a crash between the two leaves
    # the authoritative file on disk; reindex_from_xml repopulates
    # the missing row on next launch / verify.
    _upsert_index_row(logbook_root, p)

    # ``name`` is reserved by ``LogRecord``. Use ``person_name`` so
    # structured logging doesn't blow up the request — same lesson
    # as dropzone_service's ``dropzone_name`` collision.
    _logger.info(
        "person_created",
        extra={
            "person_id": str(p.id),
            "person_name": p.name,
        },
    )
    return p


def get_person(
    logbook_root: Path,
    user_id: str,
    person_id: UUID,
) -> Person:
    """Return the person with the given id, or raise NotFoundError."""
    del user_id  # v0.1: see create_person
    return _read_person(_person_path(logbook_root, person_id))


def list_people(
    logbook_root: Path,
    user_id: str,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[PersonSummary]:
    """List people as compact summaries, ordered by name (case-insensitive).

    Phase 2b implementation reads from the SQLite ``people`` index
    (D3) using the ``idx_people_name`` covering index — no per-row
    XML parse, no filesystem walk. The People picker on the
    LogJumpModal can call this on every keystroke without paying a
    parse cost.

    The index is rebuildable from XML (D3) — if it gets stale or
    deleted, ``open_index`` rebuilds the schema and the next
    ``reindex_from_xml`` pass repopulates rows.
    """
    del user_id  # v0.1: see create_person
    result = open_index(logbook_root)
    try:
        sql = (
            "SELECT id, name "
            "FROM people "
            "ORDER BY name COLLATE NOCASE"
        )
        params: tuple[int, ...] = ()
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params = (limit, offset)
        elif offset:
            # SQLite requires LIMIT to use OFFSET; -1 means "no
            # bound" per https://www.sqlite.org/lang_select.html
            sql += " LIMIT -1 OFFSET ?"
            params = (offset,)
        rows = result.conn.execute(sql, params).fetchall()
    finally:
        result.conn.close()

    return [
        PersonSummary(
            id=UUID(row["id"]),
            name=row["name"],
        )
        for row in rows
    ]


@with_writer_lock
def update_person(
    logbook_root: Path,
    user_id: str,
    person_id: UUID,
    payload: PersonUpdate,
) -> Person:
    """Full-replace update of a person record's editable fields.

    Preserves ``id`` and ``created_at``; bumps ``updated_at``.
    Re-validates Pydantic + XSD before writing. Like
    ``update_dropzone`` / ``update_jump`` (D31), this is a full
    replace — clients send the current record back with the desired
    diff applied.
    """
    del user_id  # v0.1: see create_person
    current = _read_person(_person_path(logbook_root, person_id))
    try:
        merged = Person(
            id=current.id,                  # immutable
            name=normalize_nfc(payload.name),
            notes=payload.notes,
            created_at=current.created_at,  # immutable
            updated_at=now_utc_iso(),
        )
    except ValidationError as exc:
        raise validation_failed_from_pydantic(exc, "person validation failed") from exc

    try:
        _write_person(logbook_root, merged)
    except XMLError as exc:
        raise ValidationFailedError(
            f"generated person XML failed XSD validation: {exc}",
        ) from exc

    # D3 ordering: XML first, then index. INSERT OR REPLACE handles
    # both the row-exists (normal update) and row-missing (a prior
    # crash left the index stale) cases identically.
    _upsert_index_row(logbook_root, merged)

    _logger.info(
        "person_updated",
        extra={
            "person_id": str(merged.id),
            "person_name": merged.name,
        },
    )
    return merged


@with_writer_lock
def delete_person(
    logbook_root: Path,
    user_id: str,
    person_id: UUID,
) -> Path:
    """Soft-delete a person to ``.trash/people/`` per D19 / D54.

    Returns the new path inside ``.trash`` so callers can log the
    move. Jumps that reference ``<packed_by>`` or
    ``<group_members>`` keep their UUIDs; the soft-resolution rule
    (D54 §Decision) renders them as ``Unknown person <short-uuid>``
    until the user edits them or recreates the Person.

    No cascade — that would conflate "clean up People list" with
    "delete jumps", which is the wrong UX (parallel to D44's same
    decision for dropzones).

    Raises NotFoundError when the file doesn't exist.
    """
    del user_id  # v0.1: see create_person
    path = _person_path(logbook_root, person_id)
    if not path.is_file():
        raise NotFoundError(f"person {person_id} not found")
    try:
        trashed = soft_delete_file(path, logbook_root, _TRASH_SUBDIR)
    except FileNotFoundError as exc:
        # Race with a concurrent delete (multi-process or out-of-band
        # filesystem move). End state is the same as success from
        # the caller's perspective, but surface honestly.
        raise NotFoundError(f"person {person_id} not found") from exc

    # D3 ordering: trash move first (file leaves the active set),
    # then index DELETE. A crash between the two leaves the index
    # pointing at a now-trashed UUID; the next reindex_from_xml
    # notices the missing file and removes the row.
    _delete_index_row(logbook_root, person_id)

    _logger.info(
        "person_deleted",
        extra={
            "person_id": str(person_id),
            "trashed_to": trashed.relative_to(logbook_root).as_posix(),
        },
    )
    return trashed


# --------------------------------------------------------------------------- #
# Soft resolution (D54 §Decision)
# --------------------------------------------------------------------------- #
#
# Jump-side references (``<packed_by>``, ``<group_members>``) carry
# UUIDs that may or may not resolve to an active Person record. Per
# D54 a stale ref does NOT raise — the UI renders it as
# ``Unknown person <short-uuid>`` so a hand-edited or half-imported
# logbook stays loadable. ``resolve_person_names`` is the helper any
# consumer (REST response composition, CLI, future read-side
# projections) calls to map a list of UUIDs onto display labels in a
# single SQLite query.

# Length of the short-UUID suffix on the unknown label. 8 hex
# characters = the first dash-delimited segment of a canonical UUID
# string ("12345678-..."). Long enough to disambiguate within a
# single user's career-worth of People; short enough to read at a
# glance in a list view. Kept as a module constant so the same
# label format applies everywhere.
_SHORT_UUID_LEN = 8


def _unknown_label(person_id: UUID) -> str:
    """Display label for an unresolved Person reference (D54).

    The format is ``Unknown person <8-hex-prefix>``; the hex prefix
    is the leading segment of the canonical UUID string. Returning a
    deterministic label makes equality comparisons in tests cheap and
    keeps screen-reader output consistent across re-renders.
    """
    return f"Unknown person {str(person_id)[:_SHORT_UUID_LEN]}"


def resolve_person_names(
    logbook_root: Path,
    person_ids: list[UUID] | tuple[UUID, ...],
) -> dict[UUID, str]:
    """Resolve a batch of Person UUIDs to display labels.

    Returns a dict mapping every input UUID to either the Person's
    ``name`` (when the UUID resolves to an active record) or to
    ``Unknown person <short-uuid>`` (when the UUID has been deleted,
    never existed, or hasn't yet been indexed).

    A single SQLite query covers the full input batch — cheaper than
    calling ``get_person`` per id, and avoids the per-call XSD
    parse. The query reads from the ``people`` index (Phase 2b) so a
    rebuildable index drift would surface here too; reindex from XML
    re-populates rows on next launch.

    Empty input returns an empty dict. Duplicate UUIDs collapse to a
    single entry naturally (dicts dedupe by key).

    This is the read-side counterpart to D54's "soft resolution"
    contract: stale refs never raise, they degrade gracefully into a
    legible fallback label. Use it from Jump-detail composition,
    stats projections, or anywhere the UI surfaces ``packed_by`` /
    ``group_members`` (D53) by name rather than UUID.
    """
    if not person_ids:
        return {}

    # Deduplicate up-front so the SQL ``IN`` clause stays compact
    # when the caller passes the same UUID twice (e.g. packed_by ==
    # one of group_members).
    unique_ids: list[UUID] = []
    seen: set[UUID] = set()
    for pid in person_ids:
        if pid not in seen:
            seen.add(pid)
            unique_ids.append(pid)

    # Build a parameterized ``IN`` clause. Each UUID becomes a bound
    # parameter — no string interpolation, no SQL injection surface.
    placeholders = ",".join("?" for _ in unique_ids)
    sql = f"SELECT id, name FROM people WHERE id IN ({placeholders})"
    params = tuple(str(pid) for pid in unique_ids)

    result = open_index(logbook_root)
    try:
        rows = result.conn.execute(sql, params).fetchall()
    finally:
        result.conn.close()

    resolved: dict[UUID, str] = {UUID(row["id"]): row["name"] for row in rows}

    # Fill in fallback labels for the IDs that didn't resolve. Pass
    # over the ORIGINAL input order so a caller that built a list
    # gets a dict whose iteration order matches its input — pleasant
    # for test assertions and for "preserve user-visible ordering"
    # callers.
    return {
        pid: resolved.get(pid) or _unknown_label(pid)
        for pid in unique_ids
    }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
