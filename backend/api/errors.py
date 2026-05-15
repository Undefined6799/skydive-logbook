"""Structured error responses using RFC 9457 problem+json (D16).

Service code raises typed ``ServiceError`` subclasses. The FastAPI adapter
catches each and maps it to an ``application/problem+json`` body per
RFC 9457 (https://www.rfc-editor.org/rfc/rfc9457.html, formerly RFC 7807).

Response shape::

    {
      "type":        "about:blank",         # see RFC 9457 §3.1.1
      "title":       "Not Found",           # §3.1.3  constant per problem type
      "status":      404,                   # §3.1.2  advisory; HTTP header is truth
      "detail":      "jump 1234 not found", # §3.1.4  instance-specific
      "instance":    "/api/v1/jumps/1234",  # §3.1.5  request URI as opaque id
      "code":        "not_found",           # extension (§3.2): stable machine id
      "request_id":  "b3d9...",             # extension: correlates to X-Request-Id
      "errors":      [...]                  # extension: field validation errors,
                                            # pointer uses RFC 6901 JSON Pointer
    }

Why ``about:blank`` for ``type``:
  RFC 9457 §3.1.1 makes ``type`` optional and defines ``about:blank`` as the
  implicit default. We do not publish dereferenceable error-type URIs in v1
  and the RFC explicitly permits this. The ``code`` extension carries the
  stable machine-readable identifier consumers should branch on; switching
  to documented per-type URIs later is additive (§3.2) and stays in v1.

Media type: ``application/problem+json`` (RFC 9457 §3). No charset
parameter — JSON is UTF-8 per RFC 8259 §8.1.

Error codes are part of the public contract — adding is additive, renaming
is breaking (D18).
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.observability.logging import request_id_var

PROBLEM_JSON_MEDIA_TYPE = "application/problem+json"
"""RFC 9457 §3 media type. Exported so tests and docs can reference it."""


def _escape_pointer_token(token: str) -> str:
    """Escape one path segment per RFC 6901 §3.

    The escape order is mandated: ``~`` first (so the literal ``~``
    becomes ``~0``), THEN ``/`` (so the literal ``/`` becomes ``~1``).
    Reversing the order would produce a pointer that round-trips
    incorrectly: a literal ``/`` would become ``~1``, then the ``~``
    in that ``~1`` would itself be re-escaped to ``~01``, which a
    consumer would decode back to ``~1`` not ``/``.

    Reference: https://www.rfc-editor.org/rfc/rfc6901#section-3
    """
    return token.replace("~", "~0").replace("/", "~1")


def field_pointer(*parts: str | int) -> str:
    """Build an RFC 6901 JSON Pointer from path parts.

    Returns ``"#/"`` prefixed at the front; each ``part`` is converted
    to ``str`` and escaped per :func:`_escape_pointer_token`.

    Today every callsite uses Python identifier-style field names
    (``"jump_number"``, ``"exit_altitude_m"``) and integer list
    indices, neither of which contains ``~`` or ``/`` — so the
    escapes are no-ops. The helper exists so a future field whose
    name does contain those characters (e.g. an XML attribute exposed
    through the API as a literal ``"href"`` token, or a user-named
    custom field if D14's scope ever widens) does not silently emit
    a malformed pointer that an RFC-9457-strict client would reject.

    Examples:
      >>> field_pointer("jump_number")
      '#/jump_number'
      >>> field_pointer("files", 0, "filename")
      '#/files/0/filename'
      >>> field_pointer("custom/path~with~specials")
      '#/custom~1path~0with~0specials'
    """
    return "#/" + "/".join(_escape_pointer_token(str(p)) for p in parts)


class FieldError(BaseModel):
    """One field-level validation failure.

    Shape matches the RFC 9457 §3 validation-error example: a JSON Pointer
    (RFC 6901, https://www.rfc-editor.org/rfc/rfc6901.html) into the
    request body plus a human-readable ``detail``.

    Build the ``pointer`` value via :func:`field_pointer` rather than
    f-strings to ensure RFC 6901 escaping is applied at every
    construction site.
    """
    model_config = ConfigDict(extra="forbid")

    pointer: str = Field(description="RFC 6901 JSON Pointer into the request body, e.g. '#/exit_altitude_m'.")
    detail: str = Field(description="Human-readable explanation of this field's problem.")


class ProblemDetails(BaseModel):
    """RFC 9457 problem details object.

    All standard members are optional per the RFC. We populate the ones a
    consumer will actually use: ``type``, ``title``, ``status``, ``detail``,
    ``instance``. Extensions (``code``, ``request_id``, ``errors``) sit as
    top-level siblings — nesting them would violate §3.2.
    """
    # Pydantic permits extra keys so a future problem type can add another
    # extension without a model change forcing a breaking rename.
    model_config = ConfigDict(extra="allow")

    type: str = "about:blank"
    title: str
    status: int
    detail: str
    instance: str | None = None

    # Extensions. Names are >= 3 chars and match [A-Za-z_][A-Za-z0-9_]* per
    # RFC 9457 §4.2 so the body could be safely rendered as XML later.
    code: str
    request_id: UUID
    errors: list[FieldError] | None = None


# --------------------------------------------------------------------------- #
# Typed service exceptions
# --------------------------------------------------------------------------- #

class ServiceError(Exception):
    """Base for service-layer errors that map to an HTTP problem+json response.

    Subclasses set class attributes for the stable parts of the contract
    (``http_status``, ``code``, ``title``); the instance carries the
    occurrence-specific ``message`` and optional ``details`` dict, plus an
    optional list of field-level errors.
    """
    http_status: int = 500
    code: str = "internal_error"
    title: str = "Internal Server Error"

    def __init__(
        self,
        message: str,
        *,
        errors: list[FieldError] | None = None,
        **details: Any,
    ):
        super().__init__(message)
        self.message = message
        self.details = details
        self.errors = errors


class NotFoundError(ServiceError):
    http_status = 404
    code = "not_found"
    title = "Not Found"


class ConflictError(ServiceError):
    http_status = 409
    code = "conflict"
    title = "Conflict"


class JumpNumberConflict(ConflictError):
    """Two jumps claim the same ``(user_id, jump_number)`` pair (D23)."""
    code = "jump_number_conflict"
    title = "Jump Number Conflict"


class RigNicknameConflict(ConflictError):
    """Two rigs claim the same nickname (D33 + D4 folder uniqueness).

    Rig folders are named after the sanitized nickname per D33's
    ``rigs/<nickname>/`` layout. Two rigs with the same nickname after
    sanitization would collide on ``mkdir(exist_ok=False)``; the
    service translates that into this 409 with a specific code so the
    UI can recover ("rename your rig and try again") without parsing a
    generic conflict message.
    """
    code = "rig_nickname_conflict"
    title = "Rig Nickname Conflict"


class RigComponentSwapUnsupported(ConflictError):
    """Caller attempted to swap a rig's component reference via PUT.

    Per D37, only ``swap_main`` (a dedicated jumper-facing operation)
    can change ``current_main_id``, and ``current_reserve_id`` /
    ``current_aad_id`` / ``current_container_id`` change only through
    a repack event (R.5). A direct PUT that changes any of the four
    refs is rejected here with a 409 so the UI can route the user to
    the right operation rather than silently accepting a swap that
    would bypass the assignment invariants.

    The service-layer raise site attaches a ``FieldError`` whose
    pointer identifies the offending ref (``#/current_main_id`` etc.)
    and whose detail explains what the user should do instead.
    """
    code = "rig_component_swap_unsupported"
    title = "Rig Component Swap Unsupported"


class ComponentAlreadyAssigned(ConflictError):
    """A rig referenced a component that is already on another rig (D37).

    ``create_rig`` enforces D37's "every component is in zero or one
    rigs at any time" invariant. When the caller passes a
    ``current_*_id`` whose component already has ``assigned_rig_id``
    set to a different rig, this 409 surfaces with the existing
    rig's id in the ``errors`` array so the UI can render a precise
    "this component is already on rig X" message.
    """
    code = "component_already_assigned"
    title = "Component Already Assigned"


class ComponentInUse(ConflictError):
    """Component cannot be retired/sold/out-of-service while on a rig (D37).

    ``update_<kind>`` enforces D37's rule that a component currently
    on a rig must be detached (via swap or by deleting the rig)
    before it can transition to a non-active status. The error
    payload identifies the rig holding the component so the UI can
    direct the user there.

    Lands in R.2.0c.iii.b (one slice after the create/delete cascade
    in R.2.0c.iii.a). Defined here so the error class is part of the
    public contract from the moment the cascade ships.
    """
    code = "component_in_use"
    title = "Component In Use"


class ValidationFailedError(ServiceError):
    http_status = 422
    code = "validation_failed"
    title = "Validation Failed"


class PayloadTooLargeError(ServiceError):
    """Request body or a single uploaded file exceeded a configured cap.

    Raised by:

    * :class:`backend.api.rest.RequestSizeLimitMiddleware` when the
      total request body (Content-Length pre-check or streamed
      byte count) exceeds ``Settings.max_request_bytes``.
    * The upload chunk loops in ``backend/api/jumps.py`` and
      ``backend/api/jumpers.py`` when a single file's running byte
      count exceeds ``Settings.max_file_bytes``.

    Both paths surface 413 ``application/problem+json`` per D16
    with ``code=payload_too_large``. The ``details`` keyword extras
    carry the offending size and the cap so clients can render a
    precise message ("video.mp4 is 3.1 GiB; the per-file cap is
    2.0 GiB — split the file or raise the cap in Settings").
    """
    http_status = 413
    code = "payload_too_large"
    title = "Payload Too Large"


def validation_failed_from_pydantic(
    exc: ValidationError, message: str = "validation failed"
) -> ValidationFailedError:
    """Translate a Pydantic ``ValidationError`` to a ``ValidationFailedError``.

    Each Pydantic error becomes a ``FieldError`` whose ``pointer`` is the
    RFC 6901 JSON Pointer built from the error's ``loc`` tuple, and whose
    ``detail`` is the Pydantic message. Services pass a per-entity message
    (``"jumper validation failed"`` etc.) so the 422 body reads cleanly.
    """
    field_errors: list[FieldError] = [
        FieldError(
            pointer=field_pointer(*err.get("loc", ())),
            detail=err.get("msg", "invalid value"),
        )
        for err in exc.errors()
    ]
    return ValidationFailedError(message, errors=field_errors)


class IntegrityError(ServiceError):
    """Disk integrity failure: manifest mismatch, XSD violation on read, etc."""
    http_status = 500
    code = "integrity_error"
    title = "Integrity Error"


class InternalServerError(ServiceError):
    """Catch-all wrapper for unhandled non-ServiceError exceptions (D16).

    Raised by the ``Exception`` handler in ``rest.py`` to wrap a bug that
    escaped the typed-error hierarchy (a stray ``KeyError``, a library
    raising its own base exception, etc.) so the wire shape stays
    ``application/problem+json`` instead of FastAPI's default plaintext 500.

    The class attributes match the base ``ServiceError`` defaults — the
    explicit subclass exists for call-site readability and to give future
    subclass-specific behaviour a home.
    """
    http_status = 500
    code = "internal_error"
    title = "Internal Server Error"


# --------------------------------------------------------------------------- #
# Adapter plumbing
# --------------------------------------------------------------------------- #

def build_problem(
    exc: ServiceError,
    *,
    request_id: UUID | None = None,
    instance: str | None = None,
) -> ProblemDetails:
    """Assemble a ProblemDetails object from a ServiceError.

    Extracted from ``error_response`` so callers can introspect or mutate
    the body (e.g. in tests) without going through JSONResponse.
    """
    body = ProblemDetails(
        title=exc.title,
        status=exc.http_status,
        detail=exc.message,
        instance=instance,
        code=exc.code,
        request_id=request_id or uuid4(),
        errors=exc.errors,
    )
    # Merge ServiceError(.., **details) kwargs as extension members. The
    # RFC treats unknown top-level members as opaque extensions (§3.2);
    # Pydantic's extra="allow" lets us stash them on the model.
    for k, v in exc.details.items():
        # Don't clobber a standard member by accident. Standard names are
        # enumerated by the RFC; collision is a server bug, so raise loudly.
        if k in {"type", "title", "status", "detail", "instance", "code", "request_id", "errors"}:
            raise ValueError(f"ServiceError detail key {k!r} collides with a problem-details member")
        setattr(body, k, v)
    return body


def error_response(
    exc: ServiceError,
    *,
    request_id: UUID | None = None,
    instance: str | None = None,
) -> JSONResponse:
    """Render a ServiceError as an application/problem+json response."""
    body = build_problem(exc, request_id=request_id, instance=instance)
    return JSONResponse(
        status_code=exc.http_status,
        media_type=PROBLEM_JSON_MEDIA_TYPE,
        content=body.model_dump(mode="json", exclude_none=True),
    )


def request_id_of(request: Request) -> UUID:
    """Return the current request's id.

    Reads ``request_id_var`` (D27) — the CorrelationIdMiddleware binds it
    for every HTTP request, and the same var is what ``JsonFormatter``
    reads, so the id on an error body matches the id on every log line
    emitted handling the same request.

    The ``request`` parameter is kept for call-site readability and so a
    future slice could consult request-scoped state; today it's unused.
    Falls back to a fresh UUID if the middleware didn't run (e.g. a unit
    test calling ``error_response`` directly) — ``build_problem`` requires
    a UUID, and minting one keeps the function total.
    """
    del request  # unused; kept for signature stability
    return request_id_var.get() or uuid4()
