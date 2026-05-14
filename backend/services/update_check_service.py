"""GitHub-backed update-check service.

A user-initiated check that asks GitHub for the latest published
release of ``<owner>/<repo>``, compares its tag to the running app's
version, and reports whether an update is available. Does NOT
download, install, or replace any binary — D14 defers automatic
updates, and the security argument in D52 turns on *unsigned
auto-update being a real attack surface*. A check-and-link feature has
no such surface: the user clicks, sees a version + release notes URL,
and opens the page in their browser to download manually.

Privacy posture:
  * No request leaves the machine until the user clicks the button.
    The endpoint that calls this service is gated on
    :class:`backend.config.Settings.update_check_repo` being non-None;
    if it's None the endpoint returns 503 and the UI hides the
    button entirely.
  * The HTTP call goes only to ``api.github.com``. No telemetry, no
    third-party services, no tracking pixels.
  * No auth header is sent. The request hits GitHub's anonymous
    rate-limit (60/hour/IP) which is plenty for a click-driven check.

Failure posture:
  * Network errors, DNS failures, timeouts → return an
    :class:`UpdateCheckResult` with ``status="error"`` and a
    human-readable detail. The endpoint surfaces that as a 200 with
    the error in the body so the UI can render
    *"Couldn't check right now — try again later"* instead of a
    generic "request failed".
  * GitHub 404 (no releases yet on the repo, or wrong slug) is
    surfaced as ``status="no_releases"`` so the UI can render
    *"No releases published yet"*.
  * GitHub 403 with rate-limit headers is surfaced as
    ``status="rate_limited"`` with the reset time.

The service is a pure function over (repo_slug, current_version,
http_client). Tests inject a transport so no real network call is
made — the same pattern FastAPI's TestClient uses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import httpx

# GitHub Releases API. Returns the latest *published* (non-draft,
# non-prerelease) release; for prereleases the caller would have to
# walk /releases and filter. v0.1 ships with regular releases only,
# so /latest is the right endpoint.
_GITHUB_API = "https://api.github.com"
_LATEST_PATH = "/repos/{repo}/releases/latest"

# Bounded timeout so a hung GitHub doesn't lock the UI. 5s is comfortably
# longer than a healthy round-trip from anywhere with internet but short
# enough that "no internet" feedback is fast.
_TIMEOUT_SECONDS = 5.0

# GitHub release tags conventionally prefix the version with ``v``
# ("v0.1.0", "v1.2.3-beta"). Comparison strips the prefix on both
# sides so ``v0.1.0`` and ``0.1.0`` match.
_TAG_PREFIX_RE = re.compile(r"^v(?=\d)")


UpdateStatus = Literal[
    "up_to_date",
    "update_available",
    "no_releases",
    "rate_limited",
    "error",
]


@dataclass(frozen=True)
class UpdateCheckResult:
    """One update-check response.

    ``status`` is the stable machine identifier the UI branches on.
    ``current`` and ``latest`` are the human-readable version strings
    (tags as published, without the canonicalising ``v`` strip — so
    ``v0.1.0`` round-trips as ``v0.1.0``). ``release_url`` points at
    the release page; the UI opens it in the user's browser for the
    actual download. ``detail`` is a one-line human message for the
    rate-limited / error states.
    """

    status: UpdateStatus
    current: str
    latest: str | None = None
    release_url: str | None = None
    detail: str | None = None


def _canonical(version: str) -> str:
    """Strip a leading ``v`` so ``v0.1.0`` and ``0.1.0`` compare equal."""
    return _TAG_PREFIX_RE.sub("", version.strip())


def check_for_updates(
    *,
    repo_slug: str,
    current_version: str,
    client: httpx.Client | None = None,
) -> UpdateCheckResult:
    """Query GitHub for the latest release of ``repo_slug``.

    ``repo_slug`` is the GitHub ``owner/repo`` pair. ``current_version``
    is the running app's version (typically read from package metadata).
    ``client`` is an injectable HTTPX client — tests pass a
    ``MockTransport``-backed client; production passes ``None`` and we
    construct one with our timeout.

    Always returns an :class:`UpdateCheckResult`; never raises. The
    rationale is symmetry with the REST layer — a check button that
    surfaces "couldn't check" is a better UX than a 500.
    """
    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=_TIMEOUT_SECONDS)
    try:
        url = _GITHUB_API + _LATEST_PATH.format(repo=repo_slug)
        try:
            resp = client.get(
                url,
                headers={
                    # Per GitHub API docs: request the documented schema
                    # version and identify the client. Both improve the
                    # error messages GitHub returns when something goes
                    # wrong.
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "skydive-logbook-update-check",
                },
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            return UpdateCheckResult(
                status="error",
                current=current_version,
                detail=f"could not reach GitHub: {exc.__class__.__name__}",
            )

        if resp.status_code == 404:
            return UpdateCheckResult(
                status="no_releases",
                current=current_version,
                detail=(
                    f"no published releases for {repo_slug} (or repo is "
                    f"private / does not exist)"
                ),
            )
        if resp.status_code == 403 and "rate limit" in resp.text.lower():
            reset = resp.headers.get("X-RateLimit-Reset")
            return UpdateCheckResult(
                status="rate_limited",
                current=current_version,
                detail=(
                    "GitHub rate limit hit; try again later"
                    + (f" (resets at {reset})" if reset else "")
                ),
            )
        if resp.status_code != 200:
            return UpdateCheckResult(
                status="error",
                current=current_version,
                detail=f"GitHub returned HTTP {resp.status_code}",
            )

        try:
            data = resp.json()
        except ValueError:
            return UpdateCheckResult(
                status="error",
                current=current_version,
                detail="GitHub response was not JSON",
            )

        tag = data.get("tag_name")
        url = data.get("html_url")
        if not isinstance(tag, str) or not isinstance(url, str):
            return UpdateCheckResult(
                status="error",
                current=current_version,
                detail="GitHub response missing tag_name or html_url",
            )

        # Tag comparison strips the ``v`` prefix on both sides so a tag
        # of ``v0.1.0`` released against a current version of
        # ``0.1.0.dev0`` correctly reports "update available". We do a
        # string-equality check rather than full semver ordering —
        # GitHub's /latest endpoint already filters to the actually-
        # latest published release, so any inequality means "not the
        # same as what's running."
        if _canonical(tag) == _canonical(current_version):
            return UpdateCheckResult(
                status="up_to_date",
                current=current_version,
                latest=tag,
                release_url=url,
            )
        return UpdateCheckResult(
            status="update_available",
            current=current_version,
            latest=tag,
            release_url=url,
        )
    finally:
        if owns_client:
            client.close()
