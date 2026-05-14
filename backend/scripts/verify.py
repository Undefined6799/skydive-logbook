"""``skydive-logbook verify`` — on-demand integrity check (D25).

Thin CLI wrapper around ``backend.storage.verify.verify_logbook``.

Exit codes (D25):
  0 — logbook is clean.
  1 — one or more issues found, or a startup error (e.g. the
      configured logbook folder does not exist).

Output convention (D25):
  * One line per issue, to stderr, in ``<folder>: <kind>: <detail>``
    form. Sorted by folder for stable diffs across runs.
  * Summary to stdout.

Run with ``python -m backend.scripts.verify``.
"""
from __future__ import annotations

import sys

from ..config import load_settings
from ..storage.verify import VerifyIssue, verify_logbook


def _format_issue(issue: VerifyIssue) -> str:
    return f"{issue.folder}: {issue.kind}: {issue.detail}"


def main() -> int:
    settings = load_settings()
    if not settings.logbook_root.is_dir():
        print(
            f"error: logbook folder does not exist: {settings.logbook_root}",
            file=sys.stderr,
        )
        return 1

    report = verify_logbook(settings.logbook_root)

    # Issues → stderr, sorted by folder so CI diffs stay stable.
    for issue in sorted(report.issues, key=lambda i: (i.folder, i.kind)):
        print(_format_issue(issue), file=sys.stderr)

    # Summary → stdout.
    status = "clean" if report.clean else f"{len(report.issues)} issue(s)"
    print(
        f"verified {settings.logbook_root}: "
        f"{report.folders_scanned} folder(s), {status}"
    )

    return 0 if report.clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
