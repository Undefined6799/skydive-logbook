"""OpenAPI spec augmentations.

FastAPI derives the spec from endpoint signatures; this module adds the
things that don't come for free: API metadata, tags, and the shared
``ProblemDetails`` component used by every error response (D16,
RFC 9457). v0.1 has no authentication surface (D48) so no
``securitySchemes`` are registered.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from .errors import PROBLEM_JSON_MEDIA_TYPE

API_TITLE = "Skydive Logbook API"
API_VERSION = "0.1.0"
API_SUMMARY = "Self-hosted skydiving logbook. Public REST API over localhost."


TAGS = [
    {"name": "meta", "description": "Health check and build info."},
    {"name": "jumps", "description": "Create, read, update, delete jumps."},
    {"name": "files", "description": "Upload and download attachments bound to a jump."},
    {"name": "stats", "description": "Aggregated dashboard metrics."},
    {"name": "containers", "description": "Manage containers (D33+D34 rig manager)."},
    {"name": "aads", "description": "Manage automatic activation devices (D33+D34 rig manager)."},
    {"name": "reserves", "description": "Manage reserve canopies (D33+D34 rig manager)."},
    {"name": "mains", "description": "Manage main canopies (D33+D34 rig manager)."},
    {"name": "jumpers", "description": "Manage jumper records (D33 rig manager)."},
    {"name": "rigs", "description": "Manage rig assemblies (D33 + D37 + D38 rig manager)."},
]


# Hand-authored JSON Schema for the error body. We don't rely on Pydantic's
# generated schema because we want OpenAPI consumers to see the RFC 9457
# field semantics verbatim — ``type``/``title``/``status``/``detail``/
# ``instance`` plus our documented extensions. Extensions remain open
# (``additionalProperties: true``) per RFC 9457 §3.2.
PROBLEM_DETAILS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": "ProblemDetails",
    "description": (
        "RFC 9457 problem+json error body. Standard members (type, title, "
        "status, detail, instance) are defined by the RFC; `code`, "
        "`request_id`, and `errors` are documented extensions."
    ),
    "required": ["title", "status", "detail", "code", "request_id"],
    "additionalProperties": True,
    "properties": {
        "type": {
            "type": "string",
            "format": "uri",
            "default": "about:blank",
            "description": "URI reference identifying the problem type. `about:blank` when no dedicated documentation URI is published.",
        },
        "title": {
            "type": "string",
            "description": "Short human-readable summary of the problem type. Constant per problem type.",
        },
        "status": {
            "type": "integer",
            "minimum": 100,
            "maximum": 599,
            "description": "HTTP status code. Advisory only; the response header is authoritative (RFC 9457 §3.1.2).",
        },
        "detail": {
            "type": "string",
            "description": "Human-readable explanation specific to this occurrence.",
        },
        "instance": {
            "type": "string",
            "description": "URI reference identifying this occurrence (typically the request path).",
        },
        "code": {
            "type": "string",
            "description": "Stable machine-readable identifier for this problem type. Branch on this, not `title`.",
            "examples": [
                "not_found",
                "conflict",
                "validation_failed",
                "internal_error",
                "integrity_error",
            ],
        },
        "request_id": {
            "type": "string",
            "format": "uuid",
            "description": "Correlates with the `X-Request-Id` response header for log lookup.",
        },
        "errors": {
            "type": "array",
            "description": "Field-level validation errors, populated when `code == \"validation_failed\"`.",
            "items": {
                "type": "object",
                "required": ["pointer", "detail"],
                "properties": {
                    "pointer": {
                        "type": "string",
                        "description": "RFC 6901 JSON Pointer into the request body.",
                        "examples": ["#/exit_altitude_m"],
                    },
                    "detail": {"type": "string"},
                },
            },
        },
    },
}


def custom_openapi(app: FastAPI) -> dict[str, Any]:
    """Produce the OpenAPI 3.1 document for `app`, with our additions cached on the app."""
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=API_TITLE,
        version=API_VERSION,
        summary=API_SUMMARY,
        routes=app.routes,
        tags=TAGS,
    )

    components = schema.setdefault("components", {})
    components.setdefault("schemas", {})["ProblemDetails"] = PROBLEM_DETAILS_SCHEMA

    # Per D48: v0.1 has no authentication surface — bind_host defaults to
    # 127.0.0.1 (D20) and the deployment posture is single-user loopback.
    # No `securitySchemes` entry is registered here; advertising one would
    # promise an auth defense the code does not enforce. The successor
    # D-entry that ships LAN exposure or multi-user re-adds the scheme
    # together with the middleware that backs it.

    # Shared response declarations so endpoints can reference them with
    # a single line (`responses={"404": {"$ref": "#/components/responses/NotFound"}}`).
    # Defined once so every error-returning path agrees on the media type.
    #
    # 500s collapse under a single ``Internal`` envelope at the OpenAPI
    # level — the wire shape (RFC 9457 problem+json with a ``code``
    # field) is identical for any 500, and per-``code`` discrimination
    # (e.g. ``code == integrity_error`` vs ``code == internal_error``)
    # is what consumers branch on at runtime. Both ``code`` values are
    # listed in the ``examples`` for the schema's ``code`` property
    # above so the spec documents both as possible values without
    # advertising two structurally-identical response components.
    responses = components.setdefault("responses", {})
    for code_ref, http_status, title in [
        ("NotFound",         404, "Not Found"),
        ("Conflict",         409, "Conflict"),
        ("PayloadTooLarge",  413, "Payload Too Large"),
        ("ValidationFailed", 422, "Validation Failed"),
        ("Internal",         500, "Internal Server Error"),
    ]:
        responses[code_ref] = {
            "description": f"{title} ({http_status}). Body is RFC 9457 problem+json.",
            "content": {
                PROBLEM_JSON_MEDIA_TYPE: {
                    "schema": {"$ref": "#/components/schemas/ProblemDetails"},
                },
            },
        }

    # Post-process: FastAPI's ``get_openapi`` walks every route's
    # ``responses=`` dict and stamps a default ``description`` matching
    # the HTTP status code's human name (e.g. ``"Not Found"`` for 404),
    # which is harmless on a normal response object but combines with
    # a ``$ref`` to produce an invalid OpenAPI 3.0 reference object
    # (sibling keys are ignored per the JSON Reference spec, and
    # validators flag the combination as invalid). OpenAPI 3.1 allows
    # merge semantics, but FastAPI still emits 3.0-style here and
    # third-party tools (openapi-typescript, swagger-codegen) handle
    # the sibling-keys-with-$ref case inconsistently. Strip every
    # non-``$ref`` key from any response object that contains
    # ``$ref`` so the on-the-wire spec is unambiguous.
    for _path, item in schema.get("paths", {}).items():
        for method in ("get", "post", "put", "patch", "delete"):
            op = item.get(method)
            if not op:
                continue
            for code, body in list(op.get("responses", {}).items()):
                if isinstance(body, dict) and "$ref" in body:
                    op["responses"][code] = {"$ref": body["$ref"]}

    app.openapi_schema = schema
    return schema


# ---------------------------------------------------------------------------#
# Per-route ``responses=`` helpers
# ---------------------------------------------------------------------------#
#
# FastAPI auto-generates a 200/201 entry per route plus a default 422 for
# body validation. Neither matches our RFC 9457 wire shape. Each route
# attaches one of the dicts below via ``@router.get/post/...(..., responses=ERR_*)``
# to surface the actual error envelopes its handlers can produce.
#
# Naming convention:
#   * ERR_READ    — GET-by-id: 404 + 500
#   * ERR_LIST    — GET-list: 500 (validation is on query params handled
#                   by FastAPI's default 422 envelope; until the
#                   RequestValidationError handler ships, listing this
#                   would advertise two different 422 shapes)
#   * ERR_CREATE  — POST: 409 (conflict / dup), 422, 500
#   * ERR_UPDATE  — PUT / PATCH: 404, 409, 422, 500
#   * ERR_DELETE  — DELETE: 404, 500
#
# A single literal type ``dict[str, dict]`` keeps the values simple — the
# ``$ref`` pointer is the entire payload.

def _ref(name: str) -> dict[str, str]:
    """JSON Pointer to a reusable response declared in ``custom_openapi``.

    Indirection so the FastAPI route decorator gets a plain ``dict``
    payload, not a ``Reference`` model — the schema's
    ``$ref`` member is what get_openapi serializes through.
    """
    return {"$ref": f"#/components/responses/{name}"}


ERR_READ: dict[str | int, dict[str, Any]] = {
    "404": _ref("NotFound"),
    "500": _ref("Internal"),
}
ERR_LIST: dict[str | int, dict[str, Any]] = {
    "500": _ref("Internal"),
}
ERR_CREATE: dict[str | int, dict[str, Any]] = {
    "409": _ref("Conflict"),
    # 413 covers ``Settings.max_request_bytes`` /
    # ``Settings.max_file_bytes`` rejections (Slice 10). Surfaces on
    # any route that accepts a body, but multipart routes (jumps,
    # jumper-attachments) are the realistic raise sites.
    "413": _ref("PayloadTooLarge"),
    "422": _ref("ValidationFailed"),
    "500": _ref("Internal"),
}
# ``ERR_UPDATE`` is also the right choice for sub-resource POSTs that
# create something under a path-parameterised parent — e.g. ``POST
# /jumpers/{jumper_id}/memberships`` creates a membership but can
# 404 on a missing jumper. Don't reach for ``ERR_CREATE`` (which
# omits 404) on those routes.
ERR_UPDATE: dict[str | int, dict[str, Any]] = {
    "404": _ref("NotFound"),
    "409": _ref("Conflict"),
    "413": _ref("PayloadTooLarge"),
    "422": _ref("ValidationFailed"),
    "500": _ref("Internal"),
}
ERR_DELETE: dict[str | int, dict[str, Any]] = {
    "404": _ref("NotFound"),
    "500": _ref("Internal"),
}
