"""FastAPI adapter. Thin by design (D7) — translate, call service, translate back.

Scaffold note: task #3 wires the app shell, middleware, OpenAPI customization,
and the error-handling plumbing. Task #4 wires the first jump endpoints
onto the service layer.
"""
from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.observability.logging import CorrelationIdMiddleware

from .aads import router as aads_router
from .containers import router as containers_router
from .dropzones import router as dropzones_router
from .errors import (
    InternalServerError,
    ServiceError,
    error_response,
    request_id_of,
)
from .jumpers import router as jumpers_router
from .jumps import router as jumps_router
from .mains import router as mains_router
from .onboarding import router as onboarding_router
from .openapi import custom_openapi
from .ops import router as ops_router
from .people import router as people_router
from .reserves import router as reserves_router
from .rigs import router as rigs_router

# D27: ``service_error`` emits on the same logger as ``http_request`` so an
# operator tailing ``logger == "backend.http"`` sees both events for a
# failing request. See observability.logging.CorrelationIdMiddleware.
_logger = logging.getLogger("backend.http")


def create_app(*, mount_frontend: bool = True) -> FastAPI:
    """Build the FastAPI app.

    ``mount_frontend`` controls whether the built React SPA at
    ``frontend/dist/`` is mounted as a catch-all at ``/``. Production
    callers leave it ``True`` so the desktop pywebview window can
    load the SPA from the same uvicorn process. Tests that register
    ad-hoc ``/_test/...`` routes on the returned app must pass
    ``mount_frontend=False`` — otherwise the catch-all StaticFiles
    mount swallows the request before the test handler can match
    (with ``html=True``, an unknown path returns 404 instead of
    falling through to FastAPI's router).
    """
    app = FastAPI(
        title="Skydive Logbook API",
        version="0.1.0",
        docs_url="/docs",
        # Unversioned OpenAPI URL so third-party tooling can discover the
        # current API shape without hard-coding a version. When v2 ships,
        # /openapi.json serves v2's spec while /api/v1/... routes remain
        # mounted for old clients (D18).
        # See: https://swagger.io/specification/ §4.8 (Server basePath
        # guidance) and common REST discovery practice.
        openapi_url="/openapi.json",
    )

    # Bind request_id for every HTTP request and attach the matching
    # X-Request-Id response header. Pure-ASGI implementation because
    # Starlette's BaseHTTPMiddleware breaks ContextVar propagation (D27).
    app.add_middleware(CorrelationIdMiddleware)

    # CORS for the Vite dev server (frontend/) running on a different
    # origin than uvicorn during development. The packaged pywebview
    # build serves the static frontend from the same origin (file:// or
    # localhost), where CORS doesn't apply, so this middleware is a
    # development affordance only — narrow the origin list, don't widen.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # Surface Location (POST 201 redirects) and X-Request-Id (D27
        # correlation) to JS clients which can't read them by default.
        expose_headers=["Location", "X-Request-Id"],
    )

    @app.exception_handler(ServiceError)
    async def on_service_error(  # pyright: ignore[reportUnusedFunction]  # registered via decorator
        request: Request, exc: ServiceError
    ) -> JSONResponse:
        # Emit the D27 ``service_error`` event before returning. Level
        # mirrors the HTTP status class: 5xx is an operator-should-see
        # problem (ERROR); 4xx is a caller-did-something-wrong
        # observation (WARNING). The ``exc_info`` flag attaches the
        # traceback for 5xx so a single line in the log has enough
        # context to debug server bugs without spelunking into handlers.
        level = logging.ERROR if exc.http_status >= 500 else logging.WARNING
        _logger.log(
            level,
            "service_error",
            extra={"code": exc.code, "http_status": exc.http_status},
            exc_info=exc if exc.http_status >= 500 else None,
        )

        # `instance` per RFC 9457 §3.1.5 — the request URI serves as an
        # opaque occurrence identifier. Path is sufficient; scheme/host
        # would leak backend routing on proxied deployments.
        return error_response(
            exc,
            request_id=request_id_of(request),
            instance=request.url.path,
        )

    @app.exception_handler(Exception)
    async def on_unhandled_exception(  # pyright: ignore[reportUnusedFunction]  # registered via decorator
        request: Request, exc: Exception
    ) -> JSONResponse:
        # Print the full traceback to stderr so a user running the
        # desktop launcher in a terminal can see it without tailing
        # the JSON log. The structured logger below still emits the
        # event; this is purely for human visibility.
        print(
            f"\n=== unhandled exception in {request.url.path} ===",
            file=sys.stderr,
            flush=True,
        )
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        print("=== end traceback ===\n", file=sys.stderr, flush=True)

        # Per D16: application/problem+json is the ONLY error shape at
        # the API boundary. The ServiceError handler above catches every
        # typed error; this catch-all closes the gap so that bugs
        # outside the typed hierarchy (a stray ``KeyError`` in a
        # handler, a library raising its own uncaught exception) still
        # surface as problem+json rather than FastAPI's default
        # ``PlainTextResponse("Internal Server Error", 500)``.
        #
        # Dispatch notes:
        #
        # * FastAPI dispatches exception handlers by MRO specificity, so
        #   any ``ServiceError`` subclass routes through
        #   ``on_service_error`` above before this one ever sees it.
        # * Starlette's ``HTTPException`` and FastAPI's
        #   ``RequestValidationError`` retain their own default
        #   handlers — acceptable under the narrow reading of D16 (the
        #   *service-layer* error envelope is RFC 9457) and already
        #   documented in ``backend/api/jumps.py`` for path-param 422s.
        # * An ``@app.exception_handler(Exception)`` registration is
        #   routed into Starlette's ``ServerErrorMiddleware`` (see
        #   ``starlette.applications.Starlette._build_middleware_stack``),
        #   which sits *outside* our ``CorrelationIdMiddleware``. The
        #   response from this handler therefore bypasses the
        #   ``send_wrapper`` that normally stamps ``X-Request-Id`` — we
        #   mirror the id into the response headers explicitly below so
        #   a failing request still correlates with the log line.
        #
        # The full traceback goes to the log stream via ``exc_info=``
        # below, correlated by ``request_id`` so an operator greps the
        # log for the id from the response body. The response body
        # itself surfaces a SHORT ``ExcType: message`` form in
        # ``detail`` (see comment on ``detail = ...`` below) — the
        # full traceback / frame names stay out of the wire.
        _logger.error(
            "unhandled_exception",
            extra={"code": "internal_error", "http_status": 500},
            exc_info=exc,
        )
        request_id = request_id_of(request)
        # v0.1 is a single-user desktop app bound to loopback (D20).
        # Surface the exception type and message in the response body
        # so the user can read it in the modal/error banner without
        # tailing logs. When v0.1 grows beyond loopback (multi-user,
        # remote API), this branch tightens to honor D16's safety
        # concern about leaking internal state.
        detail = f"{type(exc).__name__}: {exc}"
        response = error_response(
            InternalServerError(detail),
            request_id=request_id,
            instance=request.url.path,
        )
        # See dispatch note above — send_wrapper doesn't run for this
        # path, so the header is set directly. Matches the on-the-wire
        # shape a ServiceError response gets via CorrelationIdMiddleware.
        response.headers["X-Request-Id"] = str(request_id)
        return response

    @app.get("/api/v1/health", tags=["meta"])
    async def health() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]  # registered via @app.get
        """Liveness probe. Returns `{"status": "ok"}` when the API is up."""
        return {"status": "ok"}

    # Jump routes (Phase 3.2). Rig-manager (D33) + stats routers land
    # in later phases; OpenAPI generation runs against whatever is
    # wired at boot time.
    app.include_router(jumps_router)
    # Dropzone routes (R.D.2, D44).
    app.include_router(dropzones_router)
    # People routes (D54, Phase 2c). Same flat-entity posture as
    # dropzones — no multipart, no attachments. Powers the
    # group-member and packer pickers on the LogJumpModal.
    app.include_router(people_router)
    # Inventory component routes (R.1, D33+D34). Each kind has its
    # own router so the OpenAPI tags stay clean and the per-kind
    # service module is the single owner of its surface area.
    app.include_router(containers_router)
    app.include_router(aads_router)
    app.include_router(reserves_router)
    app.include_router(mains_router)
    # Jumper routes (R.2.0c.i, D33). Flat single-file entity, no
    # multipart, no SHA256SUMS — same posture as the inventory
    # components.
    app.include_router(jumpers_router)
    # Rig routes (R.2.0c.iv, D33+D37+D38). Folder-with-manifest
    # entity. POST validates the four component refs per D37 and
    # sets each component's assigned_rig_id; DELETE clears them.
    app.include_router(rigs_router)
    # Verify + reindex (route-bound wrappers around storage.verify
    # and services.reindex_service — backend logic was already there
    # for the CLIs, this just exposes the same to the desktop UI).
    app.include_router(ops_router)
    # First-run wizard state (D64). Owns the sentinel; the per-step
    # creates reuse the existing entity routers above.
    app.include_router(onboarding_router)

    # Mount the built React frontend at /, so the same uvicorn process
    # serves the API and the SPA. Catch-all mount goes AFTER api routes
    # and after the openapi/docs routes registered by FastAPI itself —
    # Starlette matches in registration order, so /api/v1/* and /docs
    # match before this mount falls through. ``html=True`` returns
    # index.html for unmatched paths so client-side React routing keeps
    # working on hard refresh.
    #
    # Mount only when the dist folder exists. In dev (no build run yet)
    # the mount is skipped and the frontend is served by Vite at :5173,
    # which the CORS block above allows. Tests pass
    # ``mount_frontend=False`` to suppress the mount entirely so
    # ad-hoc ``/_test/...`` routes registered on the returned app
    # are reachable.
    if mount_frontend:
        _dist = (
            Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
        )
        if _dist.is_dir() and any(_dist.iterdir()):
            app.mount("/", StaticFiles(directory=str(_dist), html=True), name="frontend")

    app.openapi = lambda: custom_openapi(app)
    return app


app = create_app()
