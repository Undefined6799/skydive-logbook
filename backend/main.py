"""Application entry point: acquire the logbook lock, then serve the REST API.

Scaffold: today this runs the REST API under uvicorn. The pywebview shell
that bundles the React SPA on top lands with the packaging work (D11).

Startup order (all under the D9 lock, all before ``uvicorn.run``):

  1. ``bootstrap_logbook`` — install app-shipped XSDs, README, and
     subdirectories (D29).
  2. ``open_index`` — open (or create) the SQLite index, applying the
     D26 drop-and-reindex flow if ``PRAGMA user_version`` disagrees
     with ``INDEX_SCHEMA_VERSION``.
  3. ``reindex_from_xml`` — only when step 2 reports a schema rebuild;
     repopulates the just-emptied tables from authoritative XML on
     disk per D26. Failure here refuses startup (return 1) so the API
     never accepts a request against an empty index after a schema
     bump.
  4. ``folder_reconcile_rigs`` — heal D37 bidirectional rig ↔
     component refs left inconsistent by a partial ``create_rig``
     crash (D70). Idempotent and cheap; non-fatal on failure
     (warning to stderr, continue startup).
  5. ``uvicorn.run`` — serve the REST API.

Why in that order: bootstrap installs the filesystem skeleton the index
and future writes depend on; open_index verifies the DB is at the
current schema version and may rebuild it from scratch; reindex
restores the index from XML; uvicorn starts only after all three have
succeeded.
"""
from __future__ import annotations

import logging
import sys

from .config import load_settings
from .observability.logging import configure_logging
from .services.reindex_service import reindex_from_xml
from .services.rig_reconcile_service import folder_reconcile_rigs
from .storage.bootstrap import bootstrap_logbook
from .storage.index import INDEX_SCHEMA_VERSION, IndexSchemaTooNewError, open_index
from .storage.lockfile import LockError, acquire

_logger = logging.getLogger("backend.main")


def main() -> int:
    settings = load_settings()
    settings.logbook_root.mkdir(parents=True, exist_ok=True)

    # Install the D27 JSON formatter on the root logger *before* uvicorn
    # boots so startup and lifespan records already render as JSON Lines.
    configure_logging(settings.log_level)

    # Defensive WARNING on non-loopback bind. The default posture (D48)
    # is single-user, loopback-only, no auth surface. A user who sets
    # SKYDIVE_BIND_HOST=0.0.0.0 thinking "I want to reach this from my
    # phone" exposes an unauthenticated REST surface to the LAN; the
    # ``Settings.expose_internal_errors`` auto-default helps but is
    # only one of several hardening gaps (no auth, no rate limit, no
    # body-size middleware). Loud warning so the user sees this in
    # the launcher's terminal and in any log shipping.
    if not settings.is_loopback_bind():
        _logger.warning(
            "non_loopback_bind",
            extra={
                "bind_host": settings.bind_host,
                "bind_port": settings.bind_port,
                "message": (
                    "REST API is bound to a non-loopback address with no "
                    "authentication surface. Anyone reachable at this "
                    "address can read and modify the logbook. See "
                    "SECURITY.md and DECISIONS.md §D48."
                ),
            },
        )

    try:
        lock = acquire(settings.logbook_root)
    except LockError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        # Per D29: install the app-shipped XSDs, README, and
        # subdirectories under the logbook root. Idempotent — safe on
        # every launch. Happens under the lock so two instances cannot
        # race here, and before uvicorn.run so the API never accepts a
        # request against a half-initialized folder.
        try:
            bootstrap_logbook(settings.logbook_root)
        except OSError as exc:
            print(
                f"error: cannot set up logbook at {settings.logbook_root}: {exc}",
                file=sys.stderr,
            )
            return 1

        # Per D26: open the index, applying the drop-and-reindex flow
        # if ``PRAGMA user_version`` disagrees with INDEX_SCHEMA_VERSION.
        # We close the connection immediately — services open their own,
        # and opening at startup is a health check + version probe.
        try:
            result = open_index(settings.logbook_root)
        except IndexSchemaTooNewError as exc:
            # On-disk schema is newer than this build's. Silently
            # downgrading would drop columns this build doesn't know
            # how to repopulate from XML — better to refuse with a
            # clear message so the user upgrades the app (or
            # consciously deletes the index file).
            print(f"error: {exc}", file=sys.stderr)
            return 1
        except OSError as exc:
            print(
                f"error: cannot open index at {settings.logbook_root}: {exc}",
                file=sys.stderr,
            )
            return 1
        schema_was_rebuilt = result.schema_was_rebuilt
        try:
            if schema_was_rebuilt:
                # The tables were just dropped and recreated. WARNING (not
                # INFO) because a schema rebuild is a non-routine event an
                # operator should see in the log: their entire index is
                # about to be rebuilt from XML by the reindex step below.
                _logger.warning(
                    "index_schema_rebuilt",
                    extra={
                        "previous_version": result.previous_version,
                        "current_version": INDEX_SCHEMA_VERSION,
                    },
                )
            else:
                _logger.info(
                    "index_opened",
                    extra={
                        "previous_version": result.previous_version,
                        "current_version": INDEX_SCHEMA_VERSION,
                    },
                )
        finally:
            result.conn.close()

        # Per D26 §Mechanics: when the schema was rebuilt, reindex
        # synchronously from XML before accepting requests. If the
        # reindex fails (raises) or aborts (e.g. duplicate jump_number
        # detected per D23/D25), refuse to start with a clear error —
        # serving the API against an empty index would silently hide
        # every existing jump from the user until the next manual
        # reindex.
        if schema_was_rebuilt:
            try:
                report = reindex_from_xml(settings.logbook_root)
            except Exception as exc:
                print(
                    f"error: reindex failed after schema rebuild: {exc}",
                    file=sys.stderr,
                )
                return 1
            if report.aborted is not None:
                # The most common cause is a duplicate jump_number across
                # two folders (D25). The user must intervene — we surface
                # the abort message verbatim so the offending folders are
                # named on stderr.
                print(
                    f"error: reindex aborted: {report.aborted}",
                    file=sys.stderr,
                )
                return 1
            _logger.info(
                "reindex_completed",
                extra={
                    "folders_scanned": report.folders_scanned,
                    "jumps_indexed": report.jumps_indexed,
                    "skipped": len(report.skipped),
                    "dropzones_indexed": report.dropzones_indexed,
                    "jumper_credentials_indexed": (
                        report.jumper_credentials_indexed
                    ),
                },
            )

        # Per D70: heal rig ↔ component bidirectional refs left
        # inconsistent by a partial ``create_rig`` (rig.xml written
        # but the component-assignment loop crashed mid-way). The
        # reconcile is idempotent and cheap on a healthy logbook —
        # one parse per rig folder plus one walk of inventory. It
        # runs after the index is open and (if applicable) reindexed
        # so its own ``set_assigned_rig_id`` writes can update the
        # inventory's wear-count projections via the same code path
        # the regular service writes use.
        try:
            folder_reconcile_rigs(settings.logbook_root)
        except Exception as exc:
            # A reconcile failure should not block startup — the
            # logbook is still readable, just with the same
            # inconsistency it had before the boot. Print and
            # continue; the operator can run ``verify`` to see what
            # tripped the reconcile.
            print(
                f"warning: rig reconcile failed: {exc}",
                file=sys.stderr,
            )

        import uvicorn
        # ``log_config=None`` keeps uvicorn from calling ``dictConfig`` and
        # wiping out the handler we just installed — uvicorn's default
        # config replaces root handlers on boot. ``access_log=False``
        # suppresses uvicorn's per-request access lines; D27's
        # ``http_request`` event (slice D) carries the same information
        # with request_id correlation.
        uvicorn.run(
            "backend.api.rest:app",
            host=settings.bind_host,
            port=settings.bind_port,
            log_config=None,
            access_log=False,
        )
    finally:
        lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
