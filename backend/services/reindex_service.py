"""Rebuild the SQLite index from jump.xml files on disk (D3, D25, D32).

The index is rebuildable from XML by design (D3). ``reindex_from_xml``
is the operation that does the rebuilding — a no-op on a healthy
logbook where the index is already current, and a full resurrection
on a logbook whose index was deleted, corrupted, or just hit the
D26 drop-and-reindex path via an ``INDEX_SCHEMA_VERSION`` bump.

Contract:

  * Walk ``<logbook_root>/jumps/*/``. Trash (``.trash/``) is NOT
    reindexed — deleted jumps stay deleted per D19.
  * For each folder: ``folder_reconcile`` (so a half-written
    manifest heals before the read), then parse + XSD-validate
    ``jump.xml`` through the hardened parser (D2), then upsert the
    row into the ``jumps`` table.
  * Skip folders whose ``jump.xml`` is missing, unparseable, or
    XSD-invalid. These are the "incomplete folder" and "invalid
    xml" crash states from D25's table — ``verify`` reports them,
    reindex ignores them.
  * D32 timestamps: use ``<created_at>`` / ``<updated_at>`` from the
    XML when present. When either is missing, fall back to the
    ``jump.xml`` file mtime for both and emit a
    ``reindex_timestamp_fallback`` WARNING log naming the folder.
  * D25's "abort on duplicate ``<jump_number>``" rule: two active
    folders claiming the same (user_id, jump_number) is a data
    integrity problem the app refuses to auto-resolve. The first
    folder landed wins the upsert; the second is reported via a
    ``DuplicateJumpNumberError`` and the reindex aborts.

Per D8 every jump is owned by ``user_id='default'`` in v0.1. When
multi-user lands, the folder layout will encode ownership and the
scan loop gains a per-user dimension.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..models.common import SCHEMA_NAMESPACE_V1
from ..models.dropzone import Dropzone
from ..models.jump import Jump
from ..models.jumper import Jumper
from ..models.person import Person
from ..storage.index import INDEX_SCHEMA_VERSION, open_index
from ..storage.reconcile import folder_reconcile
from ..xml.serialize import (
    element_to_dropzone,
    element_to_jump,
    element_to_jumper,
    element_to_person,
)
from ..xml.validator import XMLError
from ..xml.validator import parse as xml_parse
from ..xml.validator import validate as xml_validate

_ACTIVE_JUMPS_DIR = "jumps"
_JUMP_XML_NAME = "jump.xml"
_DROPZONES_DIR = "dropzones"
_JUMPERS_DIR = "jumpers"
_JUMPER_XML_NAME = "jumper.xml"
_PEOPLE_DIR = "people"
# v0.1 owner — D8 default. When multi-user lands, derive per-folder.
_DEFAULT_USER_ID = "default"

_logger = logging.getLogger("backend.services.reindex")


class DuplicateJumpNumberError(Exception):
    """Two active folders claim the same ``(user_id, jump_number)``.

    D23/D25 refuse to auto-resolve this. The error names both
    folders so the user can pick one to renumber or move to
    ``.trash/``.
    """

    def __init__(self, user_id: str, jump_number: int, folders: list[str]):
        self.user_id = user_id
        self.jump_number = jump_number
        self.folders = folders
        super().__init__(
            f"duplicate jump_number {jump_number} for user {user_id!r} in: "
            f"{', '.join(folders)}"
        )


@dataclass(frozen=True)
class ReindexReport:
    """Return shape of :func:`reindex_from_xml`.

    Callers (CLI, tests, future REST endpoint) branch on the fields:
    a clean reindex has ``aborted is None`` and ``len(skipped) == 0``.
    Anything else warrants an operator look.
    """

    # How many entries in ``jumps/`` we walked, regardless of outcome.
    folders_scanned: int
    # How many jumps we upserted into the index.
    jumps_indexed: int
    # Folders we walked past without indexing, with reason strings.
    # Usually "missing jump.xml", "invalid xml", "xsd invalid".
    skipped: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    # Folders that needed file-mtime fallback for D32 timestamps.
    # Informational — not an error, just worth knowing.
    timestamp_fallbacks: list[str] = field(default_factory=list[str])
    # R.D.3 (D44): how many <dropzone> records we walked + indexed.
    # Tracked separately from the jump counters because dropzones live
    # in a different folder shape and have their own skip semantics.
    dropzones_scanned: int = 0
    dropzones_indexed: int = 0
    # Files in ``dropzones/`` we walked past without indexing, with
    # reason strings. Mirrors the jump skipped tuple shape.
    dropzones_skipped: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    # D47, Phase D.4: jumper-credentials projection counters. The
    # ``jumpers_scanned`` count is jumper folders walked; the
    # ``jumper_credentials_indexed`` is the row count written to
    # ``jumper_credentials`` (one per membership / federation
    # rating / tandem rating / medical record on every jumper).
    jumpers_scanned: int = 0
    jumper_credentials_indexed: int = 0
    # Jumper folders skipped due to missing or invalid jumper.xml.
    jumpers_skipped: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    # D54 (Phase 2b): how many <person> records we walked + indexed.
    # Tracked separately from the other counters because people live
    # in their own folder shape and have their own skip semantics.
    people_scanned: int = 0
    people_indexed: int = 0
    # Files in ``people/`` we walked past without indexing, with reason
    # strings. Mirrors the dropzone skipped tuple shape.
    people_skipped: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    # None on clean completion; reason string on early abort.
    # Today the only abort reason is ``DuplicateJumpNumberError``.
    aborted: str | None = None

    @property
    def clean(self) -> bool:
        """No aborts and no skipped folders — a fully recoverable logbook."""
        return (
            self.aborted is None
            and not self.skipped
            and not self.dropzones_skipped
            and not self.jumpers_skipped
            and not self.people_skipped
        )


def _mtime_iso(path: Path) -> str:
    """File mtime in D17 canonical form (ISO UTC ms ``'Z'``).

    Used as the D32 timestamp fallback when ``jump.xml`` is missing
    ``<created_at>`` or ``<updated_at>``. Matches the format
    ``_now_utc_iso`` produces in ``jump_service`` so index rows are
    interchangeable regardless of origin.
    """
    ts = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return ts.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _load_jump_from_folder(folder: Path) -> Jump | None:
    """Parse + validate a folder's jump.xml. Returns ``None`` if anything fails.

    Keeps the skip-decision out of the main loop and gives each
    branch its own (folder, reason) record. ``folder_reconcile`` is
    called only once the file at least exists — reconcile of a
    folder missing ``jump.xml`` would raise.
    """
    jump_xml = folder / _JUMP_XML_NAME
    if not jump_xml.is_file():
        return None
    try:
        # D25: heal a stale manifest before the read. Reconcile is
        # idempotent and cheap (no attachment rehash).
        folder_reconcile(folder, logbook_root=folder.parent.parent)
        element = xml_parse(jump_xml.read_bytes())
        xml_validate(element)
    except (XMLError, OSError):
        return None
    try:
        return element_to_jump(element)
    except Exception:  # pydantic or deserialization failure
        return None


def reindex_from_xml(logbook_root: Path) -> ReindexReport:
    """Rebuild ``jumps`` table rows from every valid jump folder on disk.

    Idempotent: a second consecutive run against an unchanged logbook
    upserts every row to the same values and returns an identical
    report. Safe at any time — reads XML under the hardened parser
    (D2), writes only to the SQLite index.

    Raises ``DuplicateJumpNumberError`` via ``ReindexReport.aborted``
    when two folders claim the same ``(user_id, jump_number)``.
    """
    logbook_root = Path(logbook_root)
    jumps_dir = logbook_root / _ACTIVE_JUMPS_DIR

    if not jumps_dir.is_dir():
        # A bootstrapped root always has ``jumps/``; its absence is
        # nevertheless a legitimate state (e.g. freshly-created root
        # that hasn't been bootstrapped yet). Return an empty report
        # rather than failing.
        return ReindexReport(folders_scanned=0, jumps_indexed=0)

    # First pass: load every valid jump into memory, tracking both
    # successes and skips. A two-pass design is the simplest way to
    # give duplicate detection a complete view before we start
    # writing — and to keep reports accurate even when we abort.
    entries: list[tuple[Path, Jump]] = []
    skipped: list[tuple[str, str]] = []
    folders_scanned = 0

    for folder in sorted(jumps_dir.iterdir()):
        if not folder.is_dir():
            continue
        folders_scanned += 1
        rel = f"{_ACTIVE_JUMPS_DIR}/{folder.name}"
        jump = _load_jump_from_folder(folder)
        if jump is None:
            # Differentiate missing vs invalid in the skip reason so
            # an operator knows whether to look for the file or fix
            # its contents.
            if not (folder / _JUMP_XML_NAME).is_file():
                skipped.append((rel, "missing jump.xml"))
            else:
                skipped.append((rel, "invalid or XSD-noncompliant jump.xml"))
            _logger.info(
                "reindex_folder_skipped",
                extra={
                    "folder": rel,
                    "reason": skipped[-1][1],
                },
            )
            continue
        entries.append((folder, jump))

    # Duplicate detection BEFORE any upsert. Abort cleanly with a
    # report that names both offending folders so the operator can
    # act. We don't partially-apply — the first offending pair stops
    # the whole reindex.
    by_user_number: dict[tuple[str, int], list[str]] = {}
    for folder, jump in entries:
        key = (_DEFAULT_USER_ID, jump.jump_number)
        rel = f"{_ACTIVE_JUMPS_DIR}/{folder.name}"
        by_user_number.setdefault(key, []).append(rel)

    for (user_id, jump_number), folder_list in by_user_number.items():
        if len(folder_list) > 1:
            _logger.error(
                "reindex_duplicate_jump_number",
                extra={
                    "user_id": user_id,
                    "jump_number": jump_number,
                    "folders": folder_list,
                },
            )
            return ReindexReport(
                folders_scanned=folders_scanned,
                jumps_indexed=0,
                skipped=skipped,
                aborted=(
                    f"duplicate jump_number {jump_number} for user "
                    f"{user_id!r} across {len(folder_list)} folders: "
                    f"{', '.join(folder_list)}"
                ),
            )

    # Second pass: upsert. ``INSERT OR REPLACE`` by primary key
    # (``id``) makes reindex safe to re-run; a row that no longer
    # matches its XML gets overwritten with the current XML values.
    # UNIQUE(user_id, jump_number) is guaranteed not to collide by
    # the pre-scan above.
    result = open_index(logbook_root)
    timestamp_fallbacks: list[str] = []
    try:
        # Wrap in a single transaction so a mid-loop error doesn't
        # leave the index in a partially-rebuilt state. Consistent
        # with the autocommit-at-the-connection-level convention
        # (``isolation_level=None`` in open_index) — we explicitly
        # BEGIN + COMMIT here.
        result.conn.execute("BEGIN")
        try:
            for folder, jump in entries:
                rel = f"{_ACTIVE_JUMPS_DIR}/{folder.name}"
                # D32: prefer XML timestamps; fall back to file mtime
                # of jump.xml if either is missing.
                if jump.created_at is not None and jump.updated_at is not None:
                    created_at = jump.created_at
                    updated_at = jump.updated_at
                else:
                    mtime = _mtime_iso(folder / _JUMP_XML_NAME)
                    created_at = jump.created_at or mtime
                    updated_at = jump.updated_at or mtime
                    timestamp_fallbacks.append(rel)
                    _logger.warning(
                        "reindex_timestamp_fallback",
                        extra={
                            "folder": rel,
                            "fallback": "jump.xml mtime",
                            "created_at_present": jump.created_at is not None,
                            "updated_at_present": jump.updated_at is not None,
                        },
                    )

                result.conn.execute(
                    "INSERT OR REPLACE INTO jumps "
                    "(id, user_id, jump_number, date, dropzone, title, "
                    "aircraft, discipline, freefall_time_s, rig_id, "
                    "is_tandem, dropzone_id, folder, schema_ns, "
                    "created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(jump.id),
                        _DEFAULT_USER_ID,
                        jump.jump_number,
                        jump.date.isoformat(),
                        jump.dropzone,
                        jump.title,
                        jump.aircraft,
                        jump.discipline,
                        jump.freefall_time_s,
                        # v7 (R.2.2-light.d.1): cache rig_id from
                        # jump.xml. None for legacy jumps without the
                        # element.
                        str(jump.rig_id) if jump.rig_id else None,
                        # v8 (D47, Phase D.4): cache <is_tandem>.
                        # None / False both map to NULL.
                        1 if jump.is_tandem else None,
                        # v10 (D60): cache <dropzone_id> from jump.xml.
                        # None for legacy / quick-log jumps without
                        # a DZ pick.
                        str(jump.dropzone_id) if jump.dropzone_id else None,
                        rel,
                        SCHEMA_NAMESPACE_V1,
                        created_at,
                        updated_at,
                    ),
                )
            result.conn.execute("COMMIT")
        except Exception:
            result.conn.execute("ROLLBACK")
            raise

        # R.D.3: rebuild the dropzones table from disk, on the same
        # connection but in its own transaction so a corrupt
        # dropzone XML can't roll back the jump upsert. The two
        # entity types are independent — a logbook with bad
        # dropzones still has indexable jumps.
        dz_scanned, dz_indexed, dz_skipped = _reindex_dropzones(
            logbook_root, result.conn
        )
        # D47, Phase D.4: rebuild the jumper_credentials projection
        # from every jumper.xml on disk. Same posture as the
        # dropzone rebuild — independent of jumps, in its own
        # transaction, so a corrupt jumper can't poison jump or
        # dropzone state. Cops are deliberately excluded from the
        # projection (they have issued_date, not expiry_date).
        jumpers_scanned, creds_indexed, jumpers_skipped = (
            _reindex_jumper_credentials(logbook_root, result.conn)
        )
        # D54 (Phase 2b): rebuild the people table from disk. Same
        # posture as the dropzone rebuild — independent transaction
        # so a corrupt person.xml can't poison jump / dropzone state.
        people_scanned, people_indexed, people_skipped = _reindex_people(
            logbook_root, result.conn
        )
    finally:
        result.conn.close()

    _logger.info(
        "reindex_completed",
        extra={
            "folders_scanned": folders_scanned,
            "jumps_indexed": len(entries),
            "skipped": len(skipped),
            "timestamp_fallbacks": len(timestamp_fallbacks),
            "dropzones_scanned": dz_scanned,
            "dropzones_indexed": dz_indexed,
            "dropzones_skipped": len(dz_skipped),
            "jumpers_scanned": jumpers_scanned,
            "jumper_credentials_indexed": creds_indexed,
            "jumpers_skipped": len(jumpers_skipped),
            "people_scanned": people_scanned,
            "people_indexed": people_indexed,
            "people_skipped": len(people_skipped),
            "index_schema_version": INDEX_SCHEMA_VERSION,
        },
    )

    return ReindexReport(
        folders_scanned=folders_scanned,
        jumps_indexed=len(entries),
        skipped=skipped,
        timestamp_fallbacks=timestamp_fallbacks,
        dropzones_scanned=dz_scanned,
        dropzones_indexed=dz_indexed,
        dropzones_skipped=dz_skipped,
        jumpers_scanned=jumpers_scanned,
        jumper_credentials_indexed=creds_indexed,
        jumpers_skipped=jumpers_skipped,
        people_scanned=people_scanned,
        people_indexed=people_indexed,
        people_skipped=people_skipped,
    )


def _load_dropzone_from_file(path: Path) -> Dropzone | None:
    """Parse + XSD-validate a dropzone XML. Returns ``None`` on any failure.

    Same pattern as ``_load_jump_from_folder``: hardened parser, XSD
    validation, deserialize. A malformed file is reported via the
    skipped list, not raised — the reindex should index whatever it
    can rather than abort on one bad file.
    """
    try:
        element = xml_parse(path.read_bytes())
        xml_validate(element)
    except (XMLError, OSError):
        return None
    try:
        return element_to_dropzone(element)
    except Exception:  # pydantic or deserialization failure
        return None


def _load_jumper_from_folder(folder: Path) -> Jumper | None:
    """Parse + XSD-validate ``folder/jumper.xml``. Returns ``None`` on any failure.

    Same skip-on-failure posture as ``_load_jump_from_folder`` and
    ``_load_dropzone_from_file``: a malformed jumper is reported via
    the skip list, not raised. Reindex indexes whatever it can.
    """
    jumper_xml = folder / _JUMPER_XML_NAME
    if not jumper_xml.is_file():
        return None
    try:
        element = xml_parse(jumper_xml.read_bytes())
        xml_validate(element)
    except (XMLError, OSError):
        return None
    try:
        return element_to_jumper(element)
    except Exception:  # pydantic or deserialization failure
        return None


def _reindex_jumper_credentials(
    logbook_root: Path,
    conn: sqlite3.Connection,
) -> tuple[int, int, list[tuple[str, str]]]:
    """Walk ``jumpers/<uuid>/jumper.xml`` and project credentials into SQLite.

    Returns ``(jumpers_scanned, credentials_indexed, jumpers_skipped)``.

    Per D47 Phase D.4 the projection covers exactly the four
    credential collections that carry an ``expiry_date``:

      * memberships → kind = 'membership',           discriminator = org
      * ratings     → kind = 'federation_rating',    discriminator = org
      * tandem_ratings → kind = 'tandem_rating',     discriminator = system
      * medicals    → kind = 'medical',              discriminator = kind

    CoPs are intentionally absent — they have ``issued_date`` and
    null-and-void currency derived from jump activity (a future
    calculator slice).

    Pre-clears the projection table so an out-of-band jumper
    deletion (e.g. moved to ``.trash/``) drops its rows on the next
    reindex. Same posture as the dropzone rebuild.
    """
    jumpers_dir = logbook_root / _JUMPERS_DIR
    skipped: list[tuple[str, str]] = []

    conn.execute("BEGIN")
    try:
        # Wipe the projection so out-of-band edits drop their rows.
        # The table is rebuildable per D3.
        conn.execute("DELETE FROM jumper_credentials")

        if not jumpers_dir.is_dir():
            conn.execute("COMMIT")
            return (0, 0, skipped)

        scanned = 0
        indexed = 0
        for entry in sorted(jumpers_dir.iterdir()):
            if not entry.is_dir():
                # Stray flat ``<uuid>.xml`` files are pre-D47-migration
                # leftovers; bootstrap migrates them on next launch
                # via storage.jumper_migration. Skip in this pass.
                continue
            scanned += 1
            rel = f"{_JUMPERS_DIR}/{entry.name}"
            jumper = _load_jumper_from_folder(entry)
            if jumper is None:
                if not (entry / _JUMPER_XML_NAME).is_file():
                    skipped.append((rel, "missing jumper.xml"))
                else:
                    skipped.append((rel, "invalid or XSD-noncompliant jumper.xml"))
                _logger.info(
                    "reindex_jumper_skipped",
                    extra={"folder": rel, "reason": skipped[-1][1]},
                )
                continue

            # Membership rows.
            for m in jumper.memberships:
                conn.execute(
                    "INSERT OR REPLACE INTO jumper_credentials "
                    "(id, jumper_id, kind, expiry_date, discriminator, schema_ns) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(m.id),
                        str(jumper.id),
                        "membership",
                        m.expiry_date.isoformat(),
                        m.org.value,
                        SCHEMA_NAMESPACE_V1,
                    ),
                )
                indexed += 1
            # Federation rating rows.
            for r in jumper.ratings:
                conn.execute(
                    "INSERT OR REPLACE INTO jumper_credentials "
                    "(id, jumper_id, kind, expiry_date, discriminator, schema_ns) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(r.id),
                        str(jumper.id),
                        "federation_rating",
                        r.expiry_date.isoformat(),
                        r.org.value,
                        SCHEMA_NAMESPACE_V1,
                    ),
                )
                indexed += 1
            # Tandem rating rows.
            for t in jumper.tandem_ratings:
                conn.execute(
                    "INSERT OR REPLACE INTO jumper_credentials "
                    "(id, jumper_id, kind, expiry_date, discriminator, schema_ns) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(t.id),
                        str(jumper.id),
                        "tandem_rating",
                        t.expiry_date.isoformat(),
                        t.system.value,
                        SCHEMA_NAMESPACE_V1,
                    ),
                )
                indexed += 1
            # Medical rows.
            for med in jumper.medicals:
                conn.execute(
                    "INSERT OR REPLACE INTO jumper_credentials "
                    "(id, jumper_id, kind, expiry_date, discriminator, schema_ns) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(med.id),
                        str(jumper.id),
                        "medical",
                        med.expiry_date.isoformat(),
                        med.kind.value,
                        SCHEMA_NAMESPACE_V1,
                    ),
                )
                indexed += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return (scanned, indexed, skipped)


def _reindex_dropzones(
    logbook_root: Path,
    conn: sqlite3.Connection,
) -> tuple[int, int, list[tuple[str, str]]]:
    """Walk ``dropzones/*.xml`` and upsert each into the index.

    Returns ``(scanned, indexed, skipped)``. ``skipped`` is a list of
    ``(relative_path, reason)`` tuples with the same shape as the
    jump skipped list.

    Pre-clears the ``dropzones`` table so files removed from disk
    (e.g. moved to ``.trash/`` out-of-band, or hand-deleted) drop
    out of the index on the next reindex. Safe under D3: the file
    contents on disk are authoritative; the index is rebuildable.
    """
    dz_dir = logbook_root / _DROPZONES_DIR
    skipped: list[tuple[str, str]] = []

    conn.execute("BEGIN")
    try:
        # Wipe the table so out-of-band file deletions drop their
        # rows. Cheaper than DELETE FROM ... WHERE id NOT IN (...)
        # for the typical case where the table has been recently
        # rebuilt by D26.
        conn.execute("DELETE FROM dropzones")

        if not dz_dir.is_dir():
            # Pre-bootstrap state — no folder, no rows. Same
            # treatment as a missing ``jumps/`` directory.
            conn.execute("COMMIT")
            return (0, 0, skipped)

        scanned = 0
        indexed = 0
        for entry in sorted(dz_dir.iterdir()):
            if not entry.is_file() or entry.suffix != ".xml":
                continue
            scanned += 1
            rel = f"{_DROPZONES_DIR}/{entry.name}"
            dz = _load_dropzone_from_file(entry)
            if dz is None:
                skipped.append((rel, "invalid or XSD-noncompliant dropzone XML"))
                _logger.info(
                    "reindex_dropzone_skipped",
                    extra={"file": rel, "reason": skipped[-1][1]},
                )
                continue

            # D32 fallback: file mtime when XML doesn't carry
            # timestamps. Same posture as jump reindex.
            if dz.created_at is not None and dz.updated_at is not None:
                created_at = dz.created_at
                updated_at = dz.updated_at
            else:
                mtime = _mtime_iso(entry)
                created_at = dz.created_at or mtime
                updated_at = dz.updated_at or mtime
                _logger.warning(
                    "reindex_dropzone_timestamp_fallback",
                    extra={
                        "file": rel,
                        "fallback": "dropzone xml mtime",
                        "created_at_present": dz.created_at is not None,
                        "updated_at_present": dz.updated_at is not None,
                    },
                )

            conn.execute(
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
                    created_at,
                    updated_at,
                    # v10 (D60): cache <starred> from dropzone.xml.
                    # Pre-D60 files default to False (the element is
                    # elided when False) so the column gets 0; D60
                    # files with <starred>true</starred> get 1.
                    1 if dz.starred else 0,
                ),
            )
            indexed += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return (scanned, indexed, skipped)


def _load_person_from_file(path: Path) -> Person | None:
    """Parse + XSD-validate a person XML. Returns ``None`` on any failure.

    Same pattern as ``_load_dropzone_from_file``: hardened parser, XSD
    validation, deserialize. A malformed file is reported via the
    skipped list, not raised — the reindex should index whatever it
    can rather than abort on one bad file.
    """
    try:
        element = xml_parse(path.read_bytes())
        xml_validate(element)
    except (XMLError, OSError):
        return None
    try:
        return element_to_person(element)
    except Exception:  # pydantic / deserialization failure
        return None


def _reindex_people(
    logbook_root: Path,
    conn: sqlite3.Connection,
) -> tuple[int, int, list[tuple[str, str]]]:
    """Walk ``people/*.xml`` and upsert each into the index (D54).

    Returns ``(scanned, indexed, skipped)``. ``skipped`` is a list of
    ``(relative_path, reason)`` tuples with the same shape as the
    dropzone skipped list.

    Pre-clears the ``people`` table so files removed from disk
    (e.g. moved to ``.trash/`` out-of-band, or hand-deleted) drop
    out of the index on the next reindex. Safe under D3: the file
    contents on disk are authoritative; the index is rebuildable.
    """
    people_dir = logbook_root / _PEOPLE_DIR
    skipped: list[tuple[str, str]] = []

    conn.execute("BEGIN")
    try:
        # Wipe the table so out-of-band file deletions drop their
        # rows. Cheaper than DELETE FROM ... WHERE id NOT IN (...)
        # for the typical case where the table has been recently
        # rebuilt by D26.
        conn.execute("DELETE FROM people")

        if not people_dir.is_dir():
            # Pre-bootstrap state — no folder, no rows. Same
            # treatment as a missing ``jumps/`` or ``dropzones/``
            # directory.
            conn.execute("COMMIT")
            return (0, 0, skipped)

        scanned = 0
        indexed = 0
        for entry in sorted(people_dir.iterdir()):
            if not entry.is_file() or entry.suffix != ".xml":
                continue
            scanned += 1
            rel = f"{_PEOPLE_DIR}/{entry.name}"
            person = _load_person_from_file(entry)
            if person is None:
                skipped.append((rel, "invalid or XSD-noncompliant person XML"))
                _logger.info(
                    "reindex_person_skipped",
                    extra={"file": rel, "reason": skipped[-1][1]},
                )
                continue

            # D32 fallback: file mtime when XML doesn't carry
            # timestamps. Same posture as the dropzone reindex.
            if person.created_at is not None and person.updated_at is not None:
                created_at = person.created_at
                updated_at = person.updated_at
            else:
                mtime = _mtime_iso(entry)
                created_at = person.created_at or mtime
                updated_at = person.updated_at or mtime
                _logger.warning(
                    "reindex_person_timestamp_fallback",
                    extra={
                        "file": rel,
                        "fallback": "person xml mtime",
                        "created_at_present": person.created_at is not None,
                        "updated_at_present": person.updated_at is not None,
                    },
                )

            conn.execute(
                "INSERT OR REPLACE INTO people "
                "(id, name, schema_ns, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    str(person.id),
                    person.name,
                    SCHEMA_NAMESPACE_V1,
                    created_at,
                    updated_at,
                ),
            )
            indexed += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return (scanned, indexed, skipped)
