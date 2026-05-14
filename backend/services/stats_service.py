"""Dashboard stats (D14 §4).

Compute career-wide aggregations from the on-disk XML. Read-only —
nothing persisted, nothing mutated. Walks the active jumps folder
once per call, parses each ``jump.xml`` through the hardened parser,
and accumulates counts.

**Known performance debt as of 2026-05-14**: the v4 index schema
(D26, 2026-04-28) added ``aircraft``, ``discipline``, and
``freefall_time_s`` columns precisely so stats could read from SQLite
instead of walking XML. This function still walks because the rewrite
to a SQL aggregate is tracked as a separate slice (see
``reviews/2026-05-14-second-opinion.md`` Part 5 Phase 5). When that
slice lands, ``compute_stats`` should perform zero ``xml_parse`` calls
on a logbook with a coherent index, with a fallback to disk-walk only
if the index is missing or empty.

For v0.1's expected logbook sizes (hundreds of jumps, low thousands
at the high end) the current disk walk runs in well under a second.
The fix is a forward improvement, not an emergency.

D14 §4 prescribes "total jumps, total freefall time, jumps by
canopy, jumps this year". This service implements those four plus a
small set of derived stats the dashboard widget surfaces:

  * ``last_90_days`` — for currency display.
  * ``days_since_last_jump`` — for the same.
  * ``year_by_month`` — for the year sparkline.
  * ``by_discipline`` — for the by-discipline bar chart.
  * ``by_dropzone`` — for the by-dropzone bar chart.

``by_canopy`` and ``by_rig`` are deferred until the rig-manager
phases (D33) land — they will resolve through ``rig-snapshot.xml``
per D36 once jump-time snapshots are written in R.2.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from ..xml.serialize import element_to_jump
from ..xml.validator import XMLError, validate
from ..xml.validator import parse as xml_parse

_JUMP_XML_NAME = "jump.xml"
_ACTIVE_JUMPS_DIR = "jumps"


@dataclass(frozen=True)
class CareerStats:
    """Output of :func:`compute_stats`. Plain data — no behavior."""

    total: int
    this_year: int
    last_90_days: int
    # ``None`` when the logbook is empty. Days since the most recent
    # jump's date (local), inclusive — a jump on the current calendar
    # day counts as 0.
    days_since_last_jump: int | None
    # Sum of every jump's ``freefall_time_s`` (seconds). The widget
    # converts to ``Xh Ym``. Zero when the logbook has no jumps with
    # freefall time recorded.
    freefall_seconds: int
    # Twelve entries, one per month of the current calendar year.
    # Index 0 is January, index 11 is December.
    year_by_month: list[int]
    # Sorted descending by count. Each entry is ``[name, count]`` —
    # serializes as a JSON array of pairs to keep the wire shape
    # stable across whatever language consumes it.
    by_discipline: list[list[object]] = field(default_factory=list[list[object]])
    by_dropzone: list[list[object]] = field(default_factory=list[list[object]])


def compute_stats(logbook_root: Path, user_id: str) -> CareerStats:
    """Walk active jumps and aggregate every stat the widget needs.

    ``user_id`` is reserved for the D8 multi-user transition. Today
    every jump folder belongs to ``"default"`` and the parameter is
    accepted but not used to filter — when the storage layer grows a
    per-user prefix, this is the place that branches on it.

    Returns a fully-populated :class:`CareerStats` even on an empty
    logbook (zeros + empty lists) — callers don't need a None branch.
    """
    today = date.today()
    year_start = date(today.year, 1, 1)
    ninety_days_ago = today - timedelta(days=90)

    total = 0
    this_year = 0
    last_90 = 0
    most_recent: date | None = None
    freefall_total = 0
    by_month = [0] * 12
    by_discipline: Counter[str] = Counter()
    by_dropzone: Counter[str] = Counter()

    jumps_dir = logbook_root / _ACTIVE_JUMPS_DIR
    if not jumps_dir.is_dir():
        # Pre-bootstrap or someone deleted ``jumps/``. Treat as empty
        # rather than failing the request.
        return _empty_stats(by_month)

    for folder in jumps_dir.iterdir():
        if not folder.is_dir():
            continue
        jump_xml = folder / _JUMP_XML_NAME
        if not jump_xml.is_file():
            # Skipped rather than aborting — verify/reindex surface
            # invalid folders separately. Stats stay best-effort.
            continue
        try:
            element = xml_parse(jump_xml.read_bytes())
            validate(element)
            jump = element_to_jump(element)
        except XMLError:
            continue
        except Exception:
            # Defensive: if a Pydantic deserialization fails for any
            # reason, skip rather than 500 the whole stats call.
            continue

        total += 1
        if jump.date >= year_start:
            this_year += 1
            by_month[jump.date.month - 1] += 1
        if jump.date >= ninety_days_ago:
            last_90 += 1
        if most_recent is None or jump.date > most_recent:
            most_recent = jump.date
        if jump.freefall_time_s:
            freefall_total += jump.freefall_time_s
        if jump.discipline:
            by_discipline[jump.discipline] += 1
        if jump.dropzone:
            by_dropzone[jump.dropzone] += 1

    days_since = (today - most_recent).days if most_recent is not None else None

    return CareerStats(
        total=total,
        this_year=this_year,
        last_90_days=last_90,
        days_since_last_jump=days_since,
        freefall_seconds=freefall_total,
        year_by_month=by_month,
        by_discipline=[[name, count] for name, count in by_discipline.most_common()],
        by_dropzone=[[name, count] for name, count in by_dropzone.most_common()],
    )


def _empty_stats(by_month: list[int]) -> CareerStats:
    """Zeroed result for an unbootstrapped logbook root."""
    return CareerStats(
        total=0,
        this_year=0,
        last_90_days=0,
        days_since_last_jump=None,
        freefall_seconds=0,
        year_by_month=by_month,
        by_discipline=[],
        by_dropzone=[],
    )
