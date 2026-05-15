"""ASGI middleware that lives in the HTTP request path (Slice 10+).

Currently houses :class:`RequestSizeLimitMiddleware` (Slice 10)
and :class:`IdempotencyKeyMiddleware` (Slice 12, D69). Other
request-shaping middleware lands here as it ships.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..storage.index import open_index
from .errors import PROBLEM_JSON_MEDIA_TYPE

_logger = logging.getLogger("backend.api.middleware")


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


# --------------------------------------------------------------------------- #
# IdempotencyKeyMiddleware (Slice 12, D69)
# --------------------------------------------------------------------------- #

# Bytes of request body fed into the D69 compromise hash. The hash
# is over method + path + user_id + content_length + this many
# bytes of the body. Fits one multipart preamble (boundary + first
# form field name + first ~3.5 KiB of the first form value); rejects
# all realistic retry-by-another-request scenarios. Pre-Slice-12
# discussion in DECISIONS.md §D69 covers why a whole-body hash is
# rejected for multipart uploads.
_HASH_BODY_PREFIX_BYTES = 4096

# Idempotency-Key TTL (D69). One day matches a typical "I'll retry
# tomorrow morning" pattern without unboundedly retaining stale
# response bytes.
_IDEMPOTENCY_TTL_SECONDS = 24 * 3600


class IdempotencyKeyMiddleware:
    """Replay or short-circuit POSTs that carry an ``Idempotency-Key`` (D69).

    On a POST request with an ``Idempotency-Key`` header:

    1. Buffer up to ``_HASH_BODY_PREFIX_BYTES`` of body. Compute
       ``sha256(method + path + user_id + content_length +
       first_4_KiB)`` (D69's compromise hash).
    2. Look up the key in ``idempotency_keys``. On hit:
       * matching hash → replay the stored status / content-type /
         body verbatim. Application is not invoked.
       * differing hash → 422 ``application/problem+json`` with
         ``code=idempotency_key_reuse``. Application is not invoked.
    3. On miss → forward the request to the application, capturing
       its response. On a 2xx response, store the (key, hash,
       response) tuple with a 24 h TTL. Other responses are NOT
       cached — a retry of a 4xx/5xx is still useful.
    4. Once per request, opportunistic cleanup: ``DELETE FROM
       idempotency_keys WHERE expires_at < now()``. Bounded O(log n)
       by the ``idx_idempotency_expires`` index.

    Scope: POST only. Per RFC 9110, GET/HEAD/PUT/DELETE/OPTIONS are
    already idempotent at the protocol level and don't need
    application-layer dedup. POSTs without an Idempotency-Key flow
    through unchanged.

    Storage: SQLite ``idempotency_keys`` table (schema v11). Each
    request opens its own connection — at v0.1's POST volume that's
    negligible overhead and matches the rest of the codebase's
    open-and-close-per-call posture (D26).
    """

    def __init__(self, app: ASGIApp, *, logbook_root: Path) -> None:
        self.app = app
        self.logbook_root = logbook_root

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "").upper()
        if method != "POST":
            await self.app(scope, receive, send)
            return

        # Look for the Idempotency-Key header (case-insensitive per
        # RFC 9110 §5.1). ASGI normalises names to lowercase bytes.
        idem_key: str | None = None
        content_length: int | None = None
        for name, value in scope.get("headers", []):
            if name == b"idempotency-key":
                try:
                    idem_key = value.decode("ascii")
                except UnicodeDecodeError:
                    # Non-ASCII key → invalid; let the request flow
                    # through without idempotency rather than 400.
                    idem_key = None
            elif name == b"content-length":
                try:
                    content_length = int(value)
                except ValueError:
                    content_length = None

        if not idem_key:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        user_id = "default"  # D8: single-user v0.1

        # Step 1: buffer up to _HASH_BODY_PREFIX_BYTES of body for
        # hashing. We replay the buffered messages back to the
        # application via a wrapped ``receive``, so the inner
        # handler sees an unchanged byte stream.
        buffered_messages: list[Message] = []
        buffered_bytes = 0
        ended = False
        while buffered_bytes < _HASH_BODY_PREFIX_BYTES and not ended:
            msg = await receive()
            buffered_messages.append(msg)
            if msg["type"] == "http.request":
                body = msg.get("body", b"")
                buffered_bytes += len(body)
                if not msg.get("more_body", False):
                    ended = True
            else:
                # http.disconnect or unexpected; stop buffering.
                ended = True

        # Compute the hash from the buffered prefix.
        prefix_bytes = b"".join(
            m.get("body", b"")
            for m in buffered_messages
            if m["type"] == "http.request"
        )[:_HASH_BODY_PREFIX_BYTES]
        h_input = (
            method.encode("ascii")
            + b"|" + path.encode("utf-8")
            + b"|" + user_id.encode("ascii")
            + b"|" + str(content_length).encode("ascii")
            + b"|" + prefix_bytes
        )
        request_hash = hashlib.sha256(h_input).hexdigest()

        # Step 2: look up the key.
        now = _utc_now_iso()
        cached = self._lookup(idem_key, now)
        if cached is not None:
            stored_hash, status, ct, body, _expires_at = cached
            if stored_hash == request_hash:
                # Replay the stored response verbatim.
                await self._send_replay(
                    send, status=status, content_type=ct, body=body,
                )
                _logger.info(
                    "idempotency_replay",
                    extra={
                        "idempotency_key": idem_key,
                        "replay_status": status,
                    },
                )
                return
            # Different request body for the same key → 422.
            await self._send_reuse_rejection(
                send, key=idem_key, path=path,
            )
            _logger.warning(
                "idempotency_reuse_rejected",
                extra={
                    "idempotency_key": idem_key,
                    "stored_hash": stored_hash,
                    "incoming_hash": request_hash,
                },
            )
            return

        # Step 3: cache miss — forward the request. Replay the
        # buffered prefix, then drain the rest of the original
        # ``receive``. Capture the response so we can store it on
        # 2xx success.
        replay_index = {"i": 0}

        async def replay_receive() -> Message:
            if replay_index["i"] < len(buffered_messages):
                m = buffered_messages[replay_index["i"]]
                replay_index["i"] += 1
                return m
            return await receive()

        captured_status: int | None = None
        captured_headers: list[tuple[bytes, bytes]] = []
        captured_body_chunks: list[bytes] = []
        response_complete = {"v": False}

        async def capturing_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                nonlocal captured_status
                captured_status = message.get("status")
                # Mutate the outer ``captured_headers`` list (passed
                # by reference) rather than rebinding the name — a
                # plain ``captured_headers = ...`` here would shadow
                # the outer binding.
                captured_headers.clear()
                captured_headers.extend(message.get("headers", []))
            elif message["type"] == "http.response.body":
                body_chunk = message.get("body", b"")
                if body_chunk:
                    captured_body_chunks.append(body_chunk)
                if not message.get("more_body", False):
                    response_complete["v"] = True
            await send(message)

        await self.app(scope, replay_receive, capturing_send)

        # Step 4: store on 2xx if response completed cleanly.
        if (
            response_complete["v"]
            and captured_status is not None
            and 200 <= captured_status < 300
        ):
            content_type: str | None = None
            for hn, hv in captured_headers:
                if hn.lower() == b"content-type":
                    try:
                        content_type = hv.decode("ascii")
                    except UnicodeDecodeError:
                        content_type = None
                    break
            body_bytes = b"".join(captured_body_chunks)
            expires_at = _utc_iso(
                _parse_utc_iso(now)
                + _dt.timedelta(seconds=_IDEMPOTENCY_TTL_SECONDS)
            )
            self._store(
                key=idem_key,
                user_id=user_id,
                request_hash=request_hash,
                status=captured_status,
                content_type=content_type,
                body=body_bytes,
                created_at=now,
                expires_at=expires_at,
            )

        # Step 5: opportunistic cleanup of expired rows.
        self._purge_expired(now)

    # ----- DB helpers ------------------------------------------------------ #

    def _lookup(
        self, key: str, now_iso: str
    ) -> tuple[str, int, str | None, bytes, str] | None:
        """Return (request_hash, status, content_type, body, expires_at)
        for an unexpired row, or None."""
        try:
            result = open_index(self.logbook_root)
        except Exception:  # pragma: no cover - defensive
            _logger.warning("idempotency_lookup_open_failed", exc_info=True)
            return None
        try:
            row = result.conn.execute(
                "SELECT request_hash, response_status, "
                "response_content_type, response_body, expires_at "
                "FROM idempotency_keys "
                "WHERE key = ? AND expires_at > ?",
                (key, now_iso),
            ).fetchone()
        finally:
            result.conn.close()
        if row is None:
            return None
        return (
            row["request_hash"],
            int(row["response_status"]),
            row["response_content_type"],
            bytes(row["response_body"]),
            row["expires_at"],
        )

    def _store(
        self,
        *,
        key: str,
        user_id: str,
        request_hash: str,
        status: int,
        content_type: str | None,
        body: bytes,
        created_at: str,
        expires_at: str,
    ) -> None:
        try:
            result = open_index(self.logbook_root)
        except Exception:  # pragma: no cover - defensive
            _logger.warning("idempotency_store_open_failed", exc_info=True)
            return
        try:
            # ``INSERT OR REPLACE`` so that:
            #   (a) an expired row with the same key gets overwritten
            #       by the fresh successful response (the lookup
            #       already treated the expired row as absent);
            #   (b) a concurrent-in-flight duplicate key doesn't
            #       crash with IntegrityError. The "last writer
            #       wins" outcome is harmless: every in-flight
            #       handler is producing the same logical response
            #       (same hash, by construction), so whichever row
            #       ends up persisted is correct.
            result.conn.execute(
                "INSERT OR REPLACE INTO idempotency_keys "
                "(key, user_id, request_hash, response_status, "
                " response_content_type, response_body, "
                " created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    key, user_id, request_hash, status,
                    content_type, body, created_at, expires_at,
                ),
            )
            result.conn.commit()
        finally:
            result.conn.close()

    def _purge_expired(self, now_iso: str) -> None:
        try:
            result = open_index(self.logbook_root)
        except Exception:  # pragma: no cover - defensive
            return
        try:
            result.conn.execute(
                "DELETE FROM idempotency_keys WHERE expires_at <= ?",
                (now_iso,),
            )
            result.conn.commit()
        finally:
            result.conn.close()

    # ----- response builders ---------------------------------------------- #

    async def _send_replay(
        self,
        send: Send,
        *,
        status: int,
        content_type: str | None,
        body: bytes,
    ) -> None:
        headers: list[tuple[bytes, bytes]] = []
        if content_type:
            headers.append(
                (b"content-type", content_type.encode("ascii", errors="replace"))
            )
        headers.append((b"content-length", str(len(body)).encode("ascii")))
        headers.append((b"idempotent-replayed", b"true"))
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": headers,
        })
        await send({
            "type": "http.response.body",
            "body": body,
            "more_body": False,
        })

    async def _send_reuse_rejection(
        self, send: Send, *, key: str, path: str,
    ) -> None:
        request_id = str(uuid4())
        body = _problem_response_bytes(
            status=422,
            code="idempotency_key_reuse",
            title="Idempotency Key Reuse",
            detail=(
                f"Idempotency-Key {key!r} was previously used with a "
                "different request body. Generate a new key for this "
                "operation."
            ),
            instance=path,
            request_id=request_id,
        )
        await send({
            "type": "http.response.start",
            "status": 422,
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


def _utc_now_iso() -> str:
    """Current UTC time in the project's canonical D17 format."""
    now = _dt.datetime.now(_dt.UTC)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _utc_iso(dt: _dt.datetime) -> str:
    aware = dt.astimezone(_dt.UTC)
    return aware.strftime("%Y-%m-%dT%H:%M:%S.") + f"{aware.microsecond // 1000:03d}Z"


def _parse_utc_iso(s: str) -> _dt.datetime:
    """Parse the project's D17 canonical timestamp back to a UTC datetime."""
    # The format is YYYY-MM-DDTHH:MM:SS.fffZ — drop the trailing 'Z'
    # and parse with %f (microseconds; the trailing zeros pad fine).
    if s.endswith("Z"):
        s = s[:-1]
    return _dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f").replace(
        tzinfo=_dt.UTC,
    )
