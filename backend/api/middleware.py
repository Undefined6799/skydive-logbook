"""ASGI middleware that lives in the HTTP request path (Slice 10+).

Currently houses :class:`RequestSizeLimitMiddleware`, which enforces
``Settings.max_request_bytes`` before the application sees the body.
Other request-shaping middleware lands here as it ships (D48 LAN
exposure hardening, etc.).
"""
from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import PROBLEM_JSON_MEDIA_TYPE


class _RequestTooLarge(Exception):
    """Internal signal that the wrapped ``receive`` has seen too many
    bytes. Re-raised by the capped-receive coroutine and caught by
    :class:`RequestSizeLimitMiddleware` to emit a 413 response.

    Not a public error type — it never crosses the API boundary;
    the middleware converts it to RFC 9457 problem+json before
    sending. Service code raises the public
    :class:`backend.api.errors.PayloadTooLargeError` instead.
    """

    def __init__(self, consumed: int) -> None:
        super().__init__(f"request body exceeds limit at {consumed} bytes")
        self.consumed = consumed


def _problem_response_bytes(
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
    instance: str,
    request_id: str,
) -> bytes:
    """Build a UTF-8 JSON byte string for an RFC 9457 problem+json body.

    Lives here rather than reusing ``errors.build_problem`` because
    middleware runs outside the FastAPI exception-handler scope —
    importing ``error_response`` would pull a JSONResponse object
    we then have to serialise back to bytes anyway. The shape stays
    aligned with what ``error_response`` produces because both
    consume the same ``ProblemDetails`` field set.
    """
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": instance,
        "code": code,
        "request_id": request_id,
    }
    return json.dumps(body).encode("utf-8")


class RequestSizeLimitMiddleware:
    """Reject HTTP requests whose body exceeds ``max_bytes`` (Slice 10).

    Two enforcement points:

    1. **``Content-Length`` pre-check.** If the header is present and
       declares a value greater than ``max_bytes``, send 413
       immediately — no body read. Covers the typical
       browser-issued multipart upload where the client knows the
       payload size up front.
    2. **Streaming check.** Wrap the ASGI ``receive`` callable and
       accumulate the byte count across every ``http.request``
       message. If the running total exceeds ``max_bytes`` mid-
       stream, raise :class:`_RequestTooLarge`; the catch sends a
       413 if the application hasn't already started its response.
       Covers ``Transfer-Encoding: chunked`` requests (no
       Content-Length) and the rare case of a client lying about
       Content-Length.

    Per-file enforcement is **separate** and happens inside the
    multipart-aware upload chunk loops (jumps.py / jumpers.py) via
    :class:`backend.api.errors.PayloadTooLargeError`. The two layers
    together cover "one giant file" (per-file cap) and "lots of
    files totalling too much" (per-request cap).

    Wire shape: 413 ``application/problem+json`` with
    ``code=payload_too_large``. The body is constructed inline
    rather than going through FastAPI's exception handlers because
    ASGI middleware lives outside that scope (per the existing
    rest.py comment about ``ServerErrorMiddleware``).
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        if max_bytes < 1:
            raise ValueError(
                f"max_bytes must be positive; got {max_bytes!r}"
            )
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] != "http":
            # WebSockets / lifespan messages — pass through.
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # ---- Step 1: Content-Length pre-check ----
        # The first match short-circuits with a 413 before the body
        # is read. Header names are lowercase per ASGI spec.
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = None
                if declared is not None and declared > self.max_bytes:
                    await self._send_413(send, path=path, consumed=declared)
                    return
                break

        # ---- Step 2: streaming check via wrapped receive ----
        # Count bytes as they pass through. When the cumulative count
        # exceeds the cap, raise a sentinel that the outer try/except
        # converts to a 413 response.
        consumed = 0

        async def capped_receive() -> Message:
            nonlocal consumed
            msg = await receive()
            if msg["type"] == "http.request":
                body = msg.get("body", b"")
                consumed += len(body)
                if consumed > self.max_bytes:
                    raise _RequestTooLarge(consumed)
            return msg

        # Track whether the inner app has already started sending a
        # response. If it has, we cannot also send a 413 (would
        # produce a "response already started" ASGI error). In that
        # case we re-raise and let the server close the connection.
        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, capped_receive, tracking_send)
        except _RequestTooLarge as exc:
            if not response_started:
                await self._send_413(send, path=path, consumed=exc.consumed)
            else:
                # The application started its response before we
                # noticed the overrun — we can't take it back.
                # Re-raise so the server logs the issue; the client
                # sees a truncated response, which is the best we
                # can do at that point.
                raise

    async def _send_413(
        self, send: Send, *, path: str, consumed: int
    ) -> None:
        """Emit a 413 problem+json response and stop."""
        request_id = str(uuid4())
        body = _problem_response_bytes(
            status=413,
            code="payload_too_large",
            title="Payload Too Large",
            detail=(
                f"request body exceeds the {self.max_bytes}-byte cap "
                f"(observed at least {consumed} bytes). Raise "
                "Settings.max_request_bytes or split the upload."
            ),
            instance=path,
            request_id=request_id,
        )
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", PROBLEM_JSON_MEDIA_TYPE.encode("ascii")),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"x-request-id", request_id.encode("ascii")),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body,
            "more_body": False,
        })
