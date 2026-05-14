"""``skydive-logbook reindex`` — rebuild the SQLite index from XML (D3, D25).

Thin CLI wrapper around ``backend.services.reindex_service.reindex_from_xml``.

Exit codes (D25):
  0 — reindex completed cleanly.
  1 — reindex aborted (e.g. duplicate jump_number), skipped folders,
      or startup error.

Output convention:
  * Skipped folders → stderr, one line each: ``<folder>: skipped: <reason>``.
  * Timestamp fallbacks → stderr, one line each (WARNING visibility
    without forcing a non-zero exit — fallback is recoverable).
  * Abort reason → stderr, single line.
  * Summary → stdout.

Run with ``python -m backend.scripts.reindex``.
"""
from __future__ import annotations

import sys

from ..config import load_settings
from ..services.reindex_service import reindex_from_xml


def main() -> int:
    settings = load_settings()
    if not settings.logbook_root.is_dir():
        print(
            f"error: logbook folder does not exist: {settings.logbook_root}",
            file=sys.stderr,
        )
        return 1

    report = reindex_from_xml(settings.logbook_root)

    # Skipped + timestamp-fallback entries → stderr. Timestamp
    # fallbacks are warnings, not errors; they don't change the exit
    # code on their own.
    for folder, reason in sorted(report.skipped):
        print(f"{folder}: skipped: {reason}", file=sys.stderr)
    for folder in sorted(report.timestamp_fallbacks):
        print(
            f"{folder}: timestamp fallback (used jump.xml mtime)",
            file=sys.stderr,
        )

    if report.aborted is not None:
        # Abort reason on its own line so shell scripts can ``tail -1``
        # the stderr stream to get the headline failure.
        print(f"aborted: {report.aborted}", file=sys.stderr)

    # Summary → stdout.
    status = (
        "aborted" if report.aborted is not None
        else "clean" if report.clean
        else f"{len(report.skipped)} skipped"
    )
    print(
        f"reindexed {settings.logbook_root}: "
        f"{report.folders_scanned} folder(s), "
        f"{report.jumps_indexed} jump(s) indexed, {status}"
    )

    return 0 if report.clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
