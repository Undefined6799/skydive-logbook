"""Structured JSON logging with request_id correlation (D27).

The module owns three things:

1. ``request_id_var`` — a ``ContextVar`` carrying the per-request UUID the
   rest of the app (and D16's problem+json envelope) correlates against.
2. ``JsonFormatter`` — a ``logging.Formatter`` that renders each record as
   a single JSON Lines object (https://jsonlines.org) with the D27
   reserved field set plus any ``extra=`` members.
3. ``configure_logging(level)`` — the idempotent wiring that installs the
   formatter on the root logger and silences ``uvicorn.access`` (we emit
   our own ``http_request`` event, see D27 and slice D).

The middleware that binds ``request_id_var`` to a specific HTTP request
lives in this module too (slice C) — it is the reason the ContextVar is
declared here and not in ``backend.api``: services and scripts can read
the var without importing FastAPI.

Per D27 the format is the contract. Fields:

  timestamp   ISO 8601 UTC with ms precision, 'Z' suffix (matches D17)
  level       'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'
  logger      dotted logger name
  message     the formatted message
  request_id  UUIDv4 string, or null outside a request
  exception   formatted traceback string, only when exc_info is present

Any ``extra={...}`` kwargs land as additional top-level siblings. Passing
an extra whose key is one of the six reserved names above raises
``ValueError`` at format time — the server-bug-fail-loudly discipline
mirrors D16's treatment of colliding problem-details extensions.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import sys
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..config import user_config_dir

# --------------------------------------------------------------------------- #
# Contextvar
# --------------------------------------------------------------------------- #

# PEP 567: task-local storage. Pure ASGI middleware sets this in the outer
# scope before delegating to the inner app; every downstream log record
# picks it up automatically via ``JsonFormatter``. ``None`` is the "outside
# any request" sentinel — startup, shutdown, CLI scripts, and tests without
# a middleware in the stack all emit with ``request_id: null``.
request_id_var: ContextVar[UUID | None] = ContextVar("request_id", default=None)


# --------------------------------------------------------------------------- #
# JSON formatter
# --------------------------------------------------------------------------- #

# The attributes stdlib ``logging.LogRecord`` carries by default. Anything
# on ``record.__dict__`` not in this set was either added via ``extra=``
# or attached by a filter. Source: Python docs, 'LogRecord attributes'
# table (https://docs.python.org/3/library/logging.html#logrecord-attributes).
# ``taskName`` was added in 3.12; keeping it here is harmless on 3.11.
_STDLIB_RECORD_ATTRS: frozenset[str] = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName", "taskName",
})

# Our D27 reserved output keys. Callers must not pass these via ``extra=``;
# collisions raise in ``JsonFormatter.format``.
_D27_RESERVED: frozenset[str] = frozenset({
    "timestamp", "level", "logger", "message", "request_id", "exception",
})


def _iso_utc_ms(created: float) -> str:
    """Render a Unix timestamp as ISO 8601 UTC with millisecond precision.

    ``datetime.isoformat(timespec='milliseconds')`` produces a trailing
    '+00:00' for UTC; we rewrite that to 'Z' for conventional log output.
    """
    dt = datetime.fromtimestamp(created, tz=UTC)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class JsonFormatter(logging.Formatter):
    """Render a ``LogRecord`` as one JSON Lines object per D27.

    Reads ``request_id_var`` at format time — contextvars are per-task
    (PEP 567), so records emitted inside a request's task pick up the
    right id automatically. No filter required.
    """

    def format(self, record: logging.LogRecord) -> str:
        req_id = request_id_var.get()
        payload: dict[str, Any] = {
            "timestamp": _iso_utc_ms(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": str(req_id) if req_id is not None else None,
        }
        if record.exc_info:
            # ``formatException`` returns a multi-line traceback string.
            # json.dumps will escape embedded newlines, so the final JSON
            # line contains exactly one '\n' at the end (see ``_emit_line``
            # in the handler). jq -c stays happy.
            payload["exception"] = self.formatException(record.exc_info)

        # Merge caller-supplied ``extra=`` siblings. Any key not in the
        # stdlib-attribute set and not in our reserved set is user data.
        for key, value in record.__dict__.items():
            if key in _STDLIB_RECORD_ATTRS:
                continue
            if key in _D27_RESERVED:
                raise ValueError(
                    f"log record 'extra' key {key!r} collides with a reserved "
                    f"D27 field; rename it to avoid shadowing the contract"
                )
            payload[key] = value

        # ``ensure_ascii=False`` keeps unicode characters (e.g. a jumper's
        # name spelled with diacritics) human-readable in the output.
        # ``default=str`` covers anything json can't serialize natively
        # (Path, UUID, datetime, Enum, etc.) with a deterministic fallback.
        return json.dumps(payload, ensure_ascii=False, default=str)


# --------------------------------------------------------------------------- #
# Logger configuration
# --------------------------------------------------------------------------- #

# Per-process bookkeeping for the file sink. The path is computed once
# the first time ``configure_logging`` runs with file-sink enabled and
# exposed via ``log_file_path()`` for the JsApi reveal-folder action.
_log_file_path: Path | None = None


def log_dir() -> Path:
    """Resolve the on-disk directory the rotating log handler writes to.

    Sits under :func:`backend.config.user_config_dir` per D20 — the
    app-level config directory (``~/Library/Application Support/skydive-logbook``
    on macOS, ``%APPDATA%\\skydive-logbook`` on Windows,
    ``~/.config/skydive-logbook`` on Linux). Logs are app-level debug
    output, NOT user data, so they do NOT live inside ``logbook_root``
    — putting them there would violate D2's "anyone with a text editor
    and an XSD validator can read and verify the logbook folder" rule
    by polluting it with operational artifacts."""
    return user_config_dir() / "logs"


def log_file_path() -> Path | None:
    """Return the active log file path, or ``None`` if no file sink ran.

    Set by ``configure_logging(file_sink=True)``; consumed by JsApi's
    ``reveal_logs_folder`` so Settings → *Reveal logs folder* opens
    Finder at the right place. ``None`` while running tests or when the
    file sink is explicitly disabled — callers should fall back to
    "no log file in this run" UX rather than guessing the path."""
    return _log_file_path


def configure_logging(level: str = "INFO", file_sink: bool = True) -> None:
    """Install ``JsonFormatter`` on the root logger; silence ``uvicorn.access``.

    Idempotent: calling twice replaces previous handlers rather than
    stacking duplicates. This matters for tests that exercise startup.

    Two handlers, both feeding ``JsonFormatter``:

      * ``StreamHandler`` to stderr — visible when the binary is launched
        from a terminal, lost when launched via Finder double-click on
        macOS / Explorer on Windows (no console attached). Always on.
      * ``RotatingFileHandler`` to ``log_dir() / "skydive-logbook.log"``
        — covers the double-click case so users can hand a log file to a
        bug report instead of a screen recording. Capped at 10 MB × 3
        rotations so a long-running session can't fill the disk.

    Pass ``file_sink=False`` to opt out (tests do this; the file would
    accumulate noise across runs and isn't the surface under test).

    ``uvicorn.access`` is disabled here belt-and-braces; ``main.py`` also
    passes ``access_log=False`` to ``uvicorn.run`` so uvicorn never emits
    those records in the first place. Doing both means the logger stays
    quiet even if something else (a test, a library) re-enables it.

    ``uvicorn`` and ``uvicorn.error`` are *not* silenced — by default they
    carry no handlers and propagate to root, so startup/shutdown notices
    flow through our JSON formatter automatically.
    """
    global _log_file_path

    root = logging.getLogger()

    # Drop any handler a prior call installed. We can't just compare
    # formatter identity because a test may have installed its own handler
    # for capture; clearing is the only safe reset.
    for h in list(root.handlers):
        root.removeHandler(h)

    formatter = JsonFormatter()

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    if file_sink:
        # Computed outside the try so the ``except`` block always has a
        # path to put in the warning even when ``mkdir`` itself raises.
        target_dir = log_dir()
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / "skydive-logbook.log"
            # 10 MB × 3 rotations = ~30 MB ceiling per install. Big enough
            # for a long debugging session, small enough that a forgotten
            # background process can't fill the disk.
            file_handler = logging.handlers.RotatingFileHandler(
                target,
                maxBytes=10 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
            _log_file_path = target
        except OSError as exc:
            # Disk full, permission denied, read-only filesystem, etc.
            # The stderr handler is already installed; surface a single
            # warning and keep going rather than killing app startup
            # because the log directory is unwritable.
            _log_file_path = None
            root.warning(
                "log_file_sink_unavailable",
                extra={"path": str(target_dir), "error": str(exc)},
            )
    else:
        _log_file_path = None

    root.setLevel(level.upper())

    # Silence uvicorn's access logger. D27 replaces it with our own
    # ``http_request`` event emitted from the correlation middleware —
    # contextvar-correlated and free of uvicorn-version-specific
    # record.args shapes.
    access = logging.getLogger("uvicorn.access")
    access.disabled = True


# --------------------------------------------------------------------------- #
# Correlation middleware
# --------------------------------------------------------------------------- #

# ASGI requires header names as lowercase byte strings. Pre-computed once so
# ``send_wrapper`` below stays allocation-free on the hot path.
# https://asgi.readthedocs.io/en/latest/specs/www.html#response-start-send-event
_X_REQUEST_ID_HEADER: bytes = b"x-request-id"

# Logger used for the D27 ``http_request`` event. ``backend.http`` is a
# synthetic namespace: grouping request and service_error events under one
# dotted prefix lets an operator filter ``logger == "backend.http"`` for an
# access-log-shaped feed without scraping the whole stream.
_HTTP_LOGGER = logging.getLogger("backend.http")


class CorrelationIdMiddleware:
    """Bind ``request_id_var`` and emit ``http_request`` for every HTTP request.

    Pure-ASGI (``async def __call__(scope, receive, send)``) on purpose.
    Starlette's ``BaseHTTPMiddleware`` runs the downstream app inside a
    spawned task group — contextvars set inside that task don't propagate
    back to the middleware's own task, and the Starlette maintainers have
    flagged the class for deprecation over exactly this family of issues
    (see encode/starlette#1678, #2160, #1729). A pure-ASGI callable runs in
    the same task as the request handler, so a ``ContextVar`` set here is
    visible to the route, the service layer, every log record emitted
    along the way, and ``backend.api.errors`` when it assembles the
    problem+json body.

    Behaviour per request:

    1. Mint a UUIDv4. (D27 v0.1 does not accept inbound ``X-Request-Id`` —
       echoing an untrusted header blurs server-minted from client-minted
       ids without authentication; deferred to a future slice.)
    2. Set the contextvar and keep the Token for reset.
    3. Wrap ``send`` so the outbound ``http.response.start`` message gains
       an ``X-Request-Id`` header carrying the same id as the body's
       ``request_id`` field (D16), and so we can capture the response
       status for the event record.
    4. Reset the contextvar in a ``finally``, even on crashes — leakage
       across requests would silently misattribute later log lines.
    5. Emit one ``http_request`` record in ``finally`` with method, path,
       status, and duration_ms. D27 replaces uvicorn.access with this so
       every access line is contextvar-correlated.

    Non-HTTP scopes (``lifespan``, ``websocket``) are passed through
    untouched; startup/shutdown records legitimately have ``request_id:
    null`` per the contract.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        req_id = uuid4()
        # latin-1 is the ASGI-spec header-value encoding. UUID strings are
        # plain ASCII so the choice is defensive, not load-bearing.
        req_id_bytes = str(req_id).encode("latin-1")

        # Mutable box for send_wrapper to stash the status code; we need
        # it for the ``http_request`` record emitted in the finally below.
        # Default 500: if the downstream never sends a start message
        # (uncaught exception bubbling past any handler), we still log a
        # meaningful status. The real HTTP response will be whatever the
        # outer server produces.
        status_box: dict[str, int] = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            # Only the first ``http.response.start`` of a response carries
            # headers. ``http.response.body`` and trailers pass through.
            if message["type"] == "http.response.start":
                status_box["status"] = message.get("status", 500)
                # Copy to avoid mutating the downstream app's list; append
                # rather than replace so we don't clobber a route-set
                # header of the same name (none today, but cheap
                # insurance).
                headers: list[tuple[bytes, bytes]] = list(message.get("headers", []))
                headers.append((_X_REQUEST_ID_HEADER, req_id_bytes))
                message = {**message, "headers": headers}
            await send(message)

        # ``perf_counter`` is the stdlib-recommended clock for interval
        # measurement: monotonic, unaffected by wall-clock adjustments
        # (https://docs.python.org/3/library/time.html#time.perf_counter).
        start = time.perf_counter()
        token = request_id_var.set(req_id)
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 3)
            # ``extra=`` siblings land at the top level of the JSON record
            # via JsonFormatter; the message 'http_request' doubles as the
            # event name so a casual grep or jq filter on .message works.
            _HTTP_LOGGER.info(
                "http_request",
                extra={
                    "method": scope.get("method", ""),
                    "path": scope.get("path", ""),
                    "status": status_box["status"],
                    "duration_ms": duration_ms,
                },
            )
            request_id_var.reset(token)
