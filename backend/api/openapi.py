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
            "examples": ["not_found", "conflict", "validation_failed", "integrity_error"],
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

    # Shared response declaration so endpoints can reference it with a single
    # line (`responses={"404": {"$ref": "#/components/responses/NotFound"}}`).
    # Defined once, so every error-returning path agrees on the media type.
    responses = components.setdefault("responses", {})
    for code_ref, http_status, title in [
        ("NotFound",         404, "Not Found"),
        ("Conflict",         409, "Conflict"),
        ("ValidationFailed", 422, "Validation Failed"),
        ("IntegrityError",   500, "Integrity Error"),
    ]:
        responses[code_ref] = {
            "description": f"{title} ({http_status}). Body is RFC 9457 problem+json.",
            "content": {
                PROBLEM_JSON_MEDIA_TYPE: {
                    "schema": {"$ref": "#/components/schemas/ProblemDetails"},
                },
            },
        }

    app.openapi_schema = schema
    return schema
