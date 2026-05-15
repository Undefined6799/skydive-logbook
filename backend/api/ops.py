"""Operations endpoints — Verify, Reindex, Stats, Check for updates.

These wrap existing service-layer primitives (``verify_logbook``,
``reindex_from_xml``, ``compute_stats``, ``check_for_updates``) that
previously had only CLI entry points or no UI surface at all. The
desktop UI's Settings panel calls these so the user doesn't have to
drop to a terminal for routine maintenance:

  * ``GET  /api/v1/verify`` — D2 / D25 integrity walk. Read-only.
    Returns the same shape the CLI prints: ``folders_scanned``,
    ``clean`` boolean, ``issues[]`` with ``folder`` / ``kind`` /
    ``detail`` per finding.
  * ``POST /api/v1/reindex`` — D26 / D3 rebuild of the SQLite index
    from on-disk XML. Used after an interrupted create or a manual
    folder edit. Returns ``folders_scanned``, ``jumps_indexed``,
    ``skipped[]``, and either ``aborted: null`` (clean) or
    ``aborted: "<reason>"`` (e.g. duplicate jump_number).
  * ``GET  /api/v1/updates/check`` — User-initiated lookup against
    GitHub Releases. Surfaces "you have v0.1.0, latest is v0.1.2,
    here's the download page". D14 still defers automatic updates
    (silent binary replacement); this endpoint is *manual update
    with a helpful nudge*, a different feature. Returns 503
    ``update_check_disabled`` when no repo is configured so the UI
    can hide the button.

All synchronous. For a v0.1 logbook with hundreds of jumps these are
well under a second; large logbooks (10k+) will need a streamed-
response variant later.
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..config import Settings
from ..services.reindex_service import reindex_from_xml
from ..services.stats_service import compute_stats
from ..services.update_check_service import check_for_updates
from ..storage.verify import verify_logbook
from .deps import get_logbook_root, get_settings, get_user_id
from .errors import ServiceError
from .openapi import ERR_LIST, ERR_UPDATE

router = APIRouter(prefix="/api/v1", tags=["ops"])


class VerifyIssueResponse(BaseModel):
    folder: str
    kind: str
    detail: str


class VerifyResponse(BaseModel):
    folders_scanned: int
    clean: bool
    issues: list[VerifyIssueResponse]


class ReindexResponse(BaseModel):
    folders_scanned: int
    jumps_indexed: int
    skipped: list[tuple[str, str]]
    timestamp_fallbacks: list[str]
    aborted: str | None
    clean: bool


class StatsResponse(BaseModel):
    """Career-wide aggregations rendered by the Jumps → Stats sub-tab.

    Wire format mirrors :class:`backend.services.stats_service.CareerStats`
    one-for-one — the route does no transformation. ``by_*`` arrays
    come pre-sorted by descending count so the frontend can render
    them in order without re-sorting.
    """

    total: int
    this_year: int
    last_90_days: int
    days_since_last_jump: int | None
    freefall_seconds: int
    year_by_month: list[int]
    by_discipline: list[list[object]]
    by_dropzone: list[list[object]]


@router.get(
    "/verify",
    response_model=VerifyResponse,
    operation_id="run_verify",
    responses=ERR_LIST,
    summary="Run integrity verification (D2 / D25)",
    description=(
        "Walks every jump folder and runs the D25 checks: XSD-valid "
        "``jump.xml``, manifest sha256s match the canonical record, "
        "no orphan files, no cross-folder duplicate "
        "``(user_id, jump_number)``. Read-only. Returns a structured "
        "report — ``clean: true`` when no issues were found."
    ),
)
def verify_route(
    logbook_root: Path = Depends(get_logbook_root),
) -> VerifyResponse:
    report = verify_logbook(logbook_root)
    return VerifyResponse(
        folders_scanned=report.folders_scanned,
        clean=report.clean,
        issues=[
            VerifyIssueResponse(folder=i.folder, kind=i.kind, detail=i.detail)
            for i in report.issues
        ],
    )


@router.get(
    "/stats",
    response_model=StatsResponse,
    operation_id="get_stats",
    responses=ERR_LIST,
    summary="Career-wide jump aggregations (D14 §4)",
    description=(
        "Walks every jump folder once, parses each ``jump.xml``, and "
        "returns counts. D14 §4's ``total jumps``, ``total freefall "
        "time``, and ``jumps this year`` plus a few derived fields the "
        "Stats widget surfaces (``last_90_days``, "
        "``days_since_last_jump``, ``year_by_month``, "
        "``by_discipline``, ``by_dropzone``).\n\n"
        "``by_canopy`` and ``by_rig`` are deferred to the rig-manager "
        "phases (D33) — they resolve through ``rig-snapshot.xml`` per "
        "D36 once jump-time snapshots land in R.2."
    ),
)
def stats_route(
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> StatsResponse:
    s = compute_stats(logbook_root, user_id)
    return StatsResponse(
        total=s.total,
        this_year=s.this_year,
        last_90_days=s.last_90_days,
        days_since_last_jump=s.days_since_last_jump,
        freefall_seconds=s.freefall_seconds,
        year_by_month=s.year_by_month,
        by_discipline=s.by_discipline,
        by_dropzone=s.by_dropzone,
    )


class UpdateCheckResponse(BaseModel):
    """Result of a user-clicked "Check for updates" call.

    ``status`` is the stable machine identifier — UIs branch on it:

      * ``"up_to_date"``      — running the latest published release
      * ``"update_available"`` — newer release exists
      * ``"no_releases"``     — repo has no releases yet
      * ``"rate_limited"``    — GitHub anonymous rate limit hit
      * ``"error"``           — network failure or unexpected response

    ``release_url`` is the GitHub release page; the UI opens it in
    the user's browser for the actual download — there's no in-app
    binary replacement (D14 defers that).
    """

    status: str
    current: str
    latest: str | None = None
    release_url: str | None = None
    detail: str | None = None


class UpdateCheckDisabled(ServiceError):
    """Raised when ``Settings.update_check_repo`` is unset.

    A 503 problem+json (D16) so the frontend can hide the button when
    update checks aren't configured for this build / deployment. Per
    D16 ``code`` is what clients branch on.
    """
    http_status = 503
    code = "update_check_disabled"
    title = "Update check disabled"


def _current_app_version() -> str:
    """Return the running package version, or ``"unknown"`` on miss.

    A missed lookup happens in unusual installs (editable install in
    a worktree without metadata, frozen binary with stripped dist-
    info). We return ``"unknown"`` rather than raising so the
    endpoint still functions — the comparison just won't match
    anything, so the response is always ``update_available``.
    """
    try:
        return _pkg_version("skydive-logbook")
    except PackageNotFoundError:
        return "unknown"


@router.get(
    "/updates/check",
    response_model=UpdateCheckResponse,
    operation_id="check_for_updates",
    responses=ERR_LIST,
    summary="User-initiated check for app updates",
    description=(
        "Calls the GitHub Releases API for the configured repo and "
        "reports whether the running app is up to date. Triggered by "
        "the Settings → *Check for updates* button. No binary "
        "replacement (D14 defers automatic updates); the response "
        "carries ``release_url`` so the UI can open the release page "
        "in the user's browser for a manual download.\n\n"
        "Returns 503 ``update_check_disabled`` when "
        "``Settings.update_check_repo`` is unset. The UI is expected "
        "to hide the button in that case."
    ),
)
def update_check_route(
    settings: Settings = Depends(get_settings),
) -> UpdateCheckResponse:
    repo = settings.update_check_repo
    if not repo:
        raise UpdateCheckDisabled(
            "update checks are not configured for this build"
        )
    result = check_for_updates(
        repo_slug=repo,
        current_version=_current_app_version(),
    )
    return UpdateCheckResponse(
        status=result.status,
        current=result.current,
        latest=result.latest,
        release_url=result.release_url,
        detail=result.detail,
    )


@router.post(
    "/reindex",
    response_model=ReindexResponse,
    operation_id="run_reindex",
    responses=ERR_UPDATE,
    summary="Rebuild the SQLite index from XML on disk (D3 / D26)",
    description=(
        "Walks every active jump folder, parses each ``jump.xml``, and "
        "upserts the matching index row. Use after an interrupted "
        "create — the XML is on disk but the row never landed — or "
        "after manually editing folder contents. Aborts (no partial "
        "writes) on duplicate ``(user_id, jump_number)``; the abort "
        "reason names both folders so the user can renumber or trash "
        "one and rerun."
    ),
)
def reindex_route(
    logbook_root: Path = Depends(get_logbook_root),
) -> ReindexResponse:
    report = reindex_from_xml(logbook_root)
    return ReindexResponse(
        folders_scanned=report.folders_scanned,
        jumps_indexed=report.jumps_indexed,
        skipped=report.skipped,
        timestamp_fallbacks=report.timestamp_fallbacks,
        aborted=report.aborted,
        clean=report.clean,
    )
