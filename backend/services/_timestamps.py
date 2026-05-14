"""Shared timestamp helper for service-layer audit fields.

Every service uses the same ISO 8601 UTC format with millisecond precision
and ``Z`` suffix for ``created_at`` / ``updated_at`` per D17 and D32; this
module is the single source of that format so the index and cross-entity
reads see byte-identical timestamps.
"""
from __future__ import annotations

from datetime import UTC, datetime


def now_utc_iso() -> str:
    """ISO 8601 UTC timestamp with ms precision, ``'Z'`` suffix (D17 / D32)."""
    return (
        datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
