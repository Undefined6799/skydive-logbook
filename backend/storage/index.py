"""SQLite index over the authoritative XML (D3).

The index exists purely to make list / filter / stats queries fast. It
can be deleted at any time — `reindex` rebuilds it from scratch by
walking the jump folders. Every write path must update XML first and
only then the index.

**Schema versioning (D26).** The index carries its schema version in
``PRAGMA user_version``. ``open_index`` is the single enforcement point:

1. ``user_version == 0``       → fresh DB; run ``_SCHEMA``.
2. ``user_version == CURRENT`` → no-op.
3. anything else               → drop every user table, re-run
   ``_SCHEMA``, and let the caller reindex from XML.

We do not ``ALTER TABLE``. The index is rebuildable (D3), so a
drop-and-reindex loop is both correct and cheaper to maintain than a
linear migration history. Bumping ``INDEX_SCHEMA_VERSION`` is the only
mechanism — see D26 for what counts as a schema change.

Every query uses ``?`` parameters (never string-format SQL).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

INDEX_FILENAME = "index.sqlite"

# D26: bump this integer on any schema change that affects reads or
# writes. The list of qualifying changes lives in D26 §"What counts as
# a schema change"; a bump without a D-entry update is a review smell.
#
# v1 → v2 (D23): added ``UNIQUE(user_id, jump_number)`` on ``jumps`` and
# dropped the now-redundant explicit index on the same columns.
# v2 → v3 (Phase 3.1, D4 title): added ``title TEXT`` column to jumps
# as a denormalized cache of the ``<title>`` XML element. list views
# return the title without a per-row XML read; reindex rebuilds the
# column from XML (D3).
# v3 → v4 (2026-04-28): denormalized cache of aircraft / discipline /
# freefall_time_s onto jumps so the JumpsLog UI surfaces them without
# per-row XML reads.
# v4 → v5 (R.D.3, D44): added ``dropzones`` table mirroring the
# ``<dropzone>`` records under ``logbook_root/dropzones/``. Lets the
# DZ picker on the LogJumpModal hit SQLite instead of walking
# ``dropzones/*.xml`` on every keystroke.
# v5 → v6 (R.0.1, D33): dropped the legacy ``equipment`` table. The
# rig manager replaces D14 §3's thin Equipment shape with per-kind
# component XMLs (D34); the inventory tables themselves land in a
# later sub-slice. Bumping forces an existing v5 DB to drop-and-
# reindex on next launch so leftover ``equipment`` rows don't linger.
# v6 → v7 (R.2.2-light.d.1, D33): added ``rig_id`` column on jumps
# so the JumpsLog list view can render a rig column without a
# per-row XML read. Strictly nullable — pre-R.2.2 jumps and quick-log
# jumps without a rig pick carry None, and reindex_from_xml fills
# the column from each jump.xml's optional ``<rig_id>`` element.
# v7 → v8 (D47, Phase D.4): added ``is_tandem`` column on jumps so
# the Phase E currency calculator can count tandem jumps in the
# manufacturer's window without reading every jump.xml. Strictly
# nullable — pre-D47 jumps and non-tandem jumps stay None / 0;
# reindex_from_xml fills from each jump.xml's optional
# ``<is_tandem>`` element. Also added ``jumper_credentials``: a
# denormalized projection across the four credential collections
# that carry an expiry_date (memberships, federation ratings,
# tandem ratings, medicals; CoPs are excluded because they have
# issued_date not expiry, and v0.1 doesn't compute CoP currency).
# Used by expiry-warning queries; the authoritative shape is still
# jumper.xml (D3) — the table is rebuilt by reindex.
# v8 → v9 (D54, Phase 2b): added ``people`` table mirroring the
# ``<person>`` records under ``logbook_root/people/``. Lets the
# group-member and packer pickers on the LogJumpModal hit SQLite
# instead of walking ``people/*.xml`` on every keystroke. Schema
# mirrors ``dropzones`` since both are flat UUID-keyed entities.
# v9 → v10 (D60): two correlated additions shipping together so
# reindex re-walks once:
#   (a) ``dropzone_id`` column on jumps — denormalized cache of
#       <dropzone_id> on the jump (D44). Lets D60's successor-
#       election query ``SELECT dropzone_id, MAX(date) FROM jumps
#       WHERE dropzone_id IN (...) GROUP BY dropzone_id`` run as
#       a single indexed read on starred-DZ delete. Mirrors v6→v7
#       ``rig_id`` (D33). Strictly nullable — pre-D60 jumps and
#       quick-log jumps without a DZ pick stay None.
#   (b) ``starred`` column on dropzones — denormalized cache of
#       <starred> on the dropzone (D60). Lets ``list_dropzones``
#       return DropzoneSummary.starred without a per-row XML
#       parse. INTEGER NOT NULL DEFAULT 0; rebuildable from XML
#       via reindex_from_xml.
INDEX_SCHEMA_VERSION: int = 10

_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS jumps (
    id              TEXT PRIMARY KEY,               -- UUID
    user_id         TEXT NOT NULL DEFAULT 'default', -- D8
    jump_number     INTEGER NOT NULL,
    date            TEXT NOT NULL,                  -- ISO date (D17)
    dropzone        TEXT NOT NULL,
    title           TEXT,                           -- optional, mirrors <title> (D4)
    -- v4 (2026-04-28): denormalized cache of fields the JumpsLog +
    -- JumpsStats UI surface, so client-side filter/search and stats
    -- queries don't need to read every jump.xml. Strictly nullable —
    -- D3's "rebuildable from XML" invariant is preserved by
    -- reindex_from_xml repopulating these on every drop-and-reindex.
    aircraft        TEXT,
    discipline      TEXT,
    freefall_time_s INTEGER,
    -- v7 (R.2.2-light.d.1, D33): denormalized cache of <rig_id> on
    -- the jump. Lets the JumpsLog list view render the main canopy
    -- per row by resolving rig_id → rig.current_main_id → main on
    -- the client without per-row XML reads. Strictly nullable —
    -- pre-R.2.2 jumps and quick-log jumps without a rig pick stay
    -- None.
    rig_id          TEXT,
    -- v8 (D47, Phase D.4): denormalized cache of <is_tandem> on the
    -- jump. Lets the tandem currency calculator (Phase E) count
    -- "tandems in the last 90 days" / "in the last 365 days" via
    -- a single indexed query without reading every jump.xml.
    -- Stored as INTEGER (SQLite's BOOLEAN representation): 1 = true,
    -- 0 / NULL = false. Strictly nullable so pre-D47 jumps and
    -- non-tandem jumps stay None.
    is_tandem       INTEGER,
    -- v10 (D60): denormalized cache of <dropzone_id> on the jump
    -- (D44). Powers the D60 starred-DZ successor-election query
    -- ``SELECT dropzone_id, MAX(date) FROM jumps WHERE dropzone_id
    -- IN (...) GROUP BY dropzone_id`` on the rare path where the
    -- currently-starred dropzone is soft-deleted. Strictly nullable
    -- — pre-D60 jumps and quick-log jumps without a DZ pick stay
    -- None; reindex_from_xml fills from each jump.xml's optional
    -- <dropzone_id>. No dedicated SQL index — the query is bounded
    -- to a small IN-list (number of remaining DZs) and runs only on
    -- the starred-delete path, so a table scan with GROUP BY is
    -- cheap; a future slice can add one if usage shows otherwise.
    dropzone_id     TEXT,
    folder          TEXT NOT NULL,                  -- relative to logbook_root
    schema_ns       TEXT NOT NULL,                  -- XML namespace (D18)
    created_at      TEXT NOT NULL,                  -- UTC ISO 8601 (D17)
    updated_at      TEXT NOT NULL,
    -- D23: jump_number is unique within a logbook per user. Compound
    -- form future-proofs for D8 multi-user; effective-per-user in v0.1
    -- where every row uses user_id='default'. SQLite creates an
    -- automatic index covering this pair, so no explicit index on
    -- (user_id, jump_number) is needed — the constraint doubles as
    -- the lookup index.
    UNIQUE (user_id, jump_number)
);
CREATE INDEX IF NOT EXISTS idx_jumps_user_date ON jumps(user_id, date);
-- v8 (D47, Phase D.4): partial index covering tandem jumps only.
-- The currency calculator's typical query is
-- ``WHERE user_id = ? AND is_tandem = 1 AND date >= ?`` so a partial
-- index on the rare-true case is cheaper than a full index covering
-- the predominantly-NULL column.
CREATE INDEX IF NOT EXISTS idx_jumps_tandem
    ON jumps(user_id, date) WHERE is_tandem = 1;

-- v5 (R.D.3, D44): one row per <dropzone> record on disk. Conceptually
-- shared across users (a DZ is a real-world place, not user data) so
-- there is no user_id column today; if multi-user ever wants per-user
-- DZ visibility, that's a future schema bump and a D-entry.
CREATE TABLE IF NOT EXISTS dropzones (
    id           TEXT PRIMARY KEY,                 -- UUID
    name         TEXT NOT NULL,
    city         TEXT NOT NULL,
    country      TEXT NOT NULL,                    -- ISO 3166-1 alpha-2
    environment  TEXT NOT NULL,                    -- D45 closed enum
    schema_ns    TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    -- v10 (D60): denormalized cache of <starred> on the dropzone.
    -- Lets list_dropzones populate DropzoneSummary.starred without
    -- a per-row XML parse. INTEGER NOT NULL DEFAULT 0 since the
    -- XSD elides the element when false (D60 §Consequences) and
    -- reindex must still produce a defined value for legacy rows.
    -- D60 invariant: at most one row has starred=1 when the table
    -- is non-empty; defensive-clear-then-stamp in set_star keeps
    -- the invariant atomic, and the next mutation heals any drift.
    starred      INTEGER NOT NULL DEFAULT 0
);
-- Sort order for the DZ picker matches list_dropzones (case-insensitive
-- name then city). NOCASE collation lets SQLite use the index for
-- ``ORDER BY name COLLATE NOCASE, city COLLATE NOCASE`` without a
-- runtime sort.
CREATE INDEX IF NOT EXISTS idx_dropzones_name
    ON dropzones(name COLLATE NOCASE, city COLLATE NOCASE);

-- v8 (D47, Phase D.4): denormalized projection across the four
-- credential collections that carry an ``expiry_date``: memberships,
-- federation ratings, tandem ratings, medicals. CoPs are excluded
-- because they have ``issued_date`` instead of an expiry — null-and-
-- void currency is jump-derived (and not computed in v0.1).
--
-- Used by the Profile UI's "expiring in the next 30 days" query
-- (Phase F) and the tandem currency calculator's overlap with
-- federation TI ratings (Phase E). The authoritative shape is
-- jumper.xml; this table is rebuilt by reindex_from_xml on every
-- launch and on-demand via the reindex endpoint.
CREATE TABLE IF NOT EXISTS jumper_credentials (
    id            TEXT PRIMARY KEY,                -- credential's own UUID
    jumper_id     TEXT NOT NULL,                   -- parent jumper's UUID
    kind          TEXT NOT NULL,                   -- 'membership' | 'federation_rating' | 'tandem_rating' | 'medical'
    expiry_date   TEXT NOT NULL,                   -- ISO date (D17)
    -- Per-kind discriminator: ``org`` for membership / federation
    -- rating (CSPA / USPA / OTHER), ``system`` for tandem rating
    -- (upt_sigma / strong_dual_hawk / etc), ``kind`` for medical
    -- (class_iii). Lets the warning UI render
    -- "Your CSPA membership expires in 14 days" with no per-row
    -- XML read.
    discriminator TEXT NOT NULL,
    schema_ns     TEXT NOT NULL
);
-- Compound index for "what's expiring for jumper X soon" — the
-- Phase F warning's primary query. Single-column index on
-- expiry_date is for the cross-jumper "what expires in this window"
-- view (Phase E currency calculator's proximity query).
CREATE INDEX IF NOT EXISTS idx_jumper_credentials_jumper_expiry
    ON jumper_credentials(jumper_id, expiry_date);
CREATE INDEX IF NOT EXISTS idx_jumper_credentials_expiry
    ON jumper_credentials(expiry_date);

-- v9 (D54, Phase 2b): one row per <person> record on disk.
-- Conceptually shared across users (people are real-world contacts,
-- not user-private data) so there is no user_id column today; same
-- posture as dropzones. If multi-user ever wants per-user People
-- visibility, that's a future schema bump and a D-entry.
CREATE TABLE IF NOT EXISTS people (
    id          TEXT PRIMARY KEY,                 -- UUID
    name        TEXT NOT NULL,
    schema_ns   TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
-- Sort order for the people picker matches list_people (case-
-- insensitive name). NOCASE collation lets SQLite use the index
-- for ``ORDER BY name COLLATE NOCASE`` without a runtime sort.
CREATE INDEX IF NOT EXISTS idx_people_name
    ON people(name COLLATE NOCASE);
"""


@dataclass(frozen=True)
class IndexOpenResult:
    """Outcome of ``open_index`` (D26 §Mechanics).

    ``schema_was_rebuilt`` distinguishes three callers might need to act
    differently:

    - ``True``  → tables were just dropped and recreated. The index is
      empty. The caller (typically ``main.py``) must reindex from XML
      before accepting service traffic, or refuse to start.
    - ``False`` → either a fresh DB (``previous_version == 0``) or a
      no-op open of a current-version DB (``previous_version ==
      INDEX_SCHEMA_VERSION``). Fresh-and-empty is intentionally not
      reported as a rebuild: a brand-new logbook has no XML to index
      from, so there is nothing to do.

    ``previous_version`` is what ``PRAGMA user_version`` read *before*
    we wrote the current value. ``0`` on fresh, ``INDEX_SCHEMA_VERSION``
    on a clean re-open, any other integer on a rebuild from that prior
    version.
    """

    conn: sqlite3.Connection
    schema_was_rebuilt: bool
    previous_version: int


def _drop_user_tables(conn: sqlite3.Connection) -> None:
    """Drop every non-internal table from the connection.

    Table names come from ``sqlite_master`` (not user input), so the
    f-string interpolation is safe. Dropping a table cascades to its
    indexes and triggers automatically. If a future schema adds views,
    widen the ``type`` filter to ``('table', 'view')``.
    """
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    for row in rows:
        conn.execute(f"DROP TABLE IF EXISTS {row['name']}")


def open_index(logbook_root: Path) -> IndexOpenResult:
    """Open (or create) the SQLite index at ``<logbook_root>/index.sqlite``.

    Returns an ``IndexOpenResult`` that tells the caller whether a
    schema rebuild just happened. See the three-branch logic documented
    at the top of this module and D26 for rationale.

    Enables WAL mode and foreign keys on every open. Rebuilding from
    XML is safe at any time — see ``scripts/reindex.py``.
    """
    logbook_root = Path(logbook_root)
    logbook_root.mkdir(parents=True, exist_ok=True)
    path = logbook_root / INDEX_FILENAME

    # ``isolation_level=None`` = autocommit; services wrap multi-statement
    # work in explicit transactions.
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row

    # Connection-level PRAGMAs that do not affect on-disk format. These
    # must be set on every open (they do not persist across connections).
    conn.execute("PRAGMA journal_mode = WAL")
    # synchronous=NORMAL under WAL: SQLite fsyncs on checkpoint but not
    # on every commit. Trade-off documented in D3 / D26: XML is the
    # source of truth, index is rebuildable, a crash may lose the most
    # recent index row but never the jump.
    # See https://www.sqlite.org/wal.html §5.
    conn.execute("PRAGMA synchronous = NORMAL")
    # busy_timeout=250ms: cost-free insurance against future intra-
    # process concurrency (D50). Today the writer-lock policy makes
    # SQLITE_BUSY structurally unreachable — only one writer enters
    # the service layer at a time, and reads use their own connections.
    # If a future slice ever holds two write connections in flight
    # (background reindex, async migration, etc.), busy_timeout makes
    # the contention graceful instead of immediate. The 250ms value is
    # SQLite's documented sane-default for desktop apps; large enough
    # to absorb a write burst, small enough that a real deadlock
    # surfaces quickly. See https://www.sqlite.org/pragma.html#pragma_busy_timeout.
    conn.execute("PRAGMA busy_timeout = 250")

    previous_version = conn.execute("PRAGMA user_version").fetchone()[0]

    if previous_version == 0:
        # Branch 1: fresh DB. No tables to drop; install the schema and
        # stamp the version.
        conn.executescript(_SCHEMA)
        _set_user_version(conn, INDEX_SCHEMA_VERSION)
        return IndexOpenResult(
            conn=conn, schema_was_rebuilt=False, previous_version=0
        )

    if previous_version == INDEX_SCHEMA_VERSION:
        # Branch 2: same version on disk and in code. No-op. Re-running
        # ``_SCHEMA`` would be safe (it uses ``IF NOT EXISTS``) but is
        # unnecessary — we skip for clarity.
        return IndexOpenResult(
            conn=conn,
            schema_was_rebuilt=False,
            previous_version=previous_version,
        )

    # Branch 3: version mismatch in either direction. Drop every user
    # table (indexes and triggers cascade), reinstall, restamp. The
    # caller is now responsible for reindexing from XML.
    _drop_user_tables(conn)
    conn.executescript(_SCHEMA)
    _set_user_version(conn, INDEX_SCHEMA_VERSION)
    return IndexOpenResult(
        conn=conn,
        schema_was_rebuilt=True,
        previous_version=previous_version,
    )


def _set_user_version(conn: sqlite3.Connection, version: int) -> None:
    """Write ``PRAGMA user_version = <version>``.

    PRAGMA does not accept ``?`` parameters (SQLite docs §PRAGMA), so we
    interpolate. ``version`` is always an ``int`` from
    ``INDEX_SCHEMA_VERSION``; the interpolation is safe.
    """
    conn.execute(f"PRAGMA user_version = {int(version)}")
