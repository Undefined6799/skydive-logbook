"""OpenAPI per-route responses + operation_id coverage (Slice 4).

The `responses=` declarations and `operation_id=` values across every
route are part of the API contract: downstream SDK generators
(openapi-typescript, openapi-generator) name client methods after
operation_id and dispatch on the declared response codes. These tests
keep the spec honest: every route must declare an operation_id and
must include error-response refs for the status codes its handler
can actually produce.

The complementary structural test is
`test_openapi_problem_details_alignment.py` (Slice 6, separate file).
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.api.rest import create_app


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    """Build the OpenAPI document once per module.

    ``create_app`` wires every entity router; ``custom_openapi``
    augments the auto-generated spec with shared ``responses``
    components plus the ``ERR_*`` refs. This fixture exercises the
    same composition the production server uses.
    """
    app = create_app(mount_frontend=False)
    return app.openapi()


def _operations(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """Flatten (path, method, operation-object) for every route."""
    out: list[tuple[str, str, dict[str, Any]]] = []
    for path, item in spec.get("paths", {}).items():
        for method in ("get", "post", "put", "patch", "delete"):
            op = item.get(method)
            if op is not None:
                out.append((path, method.upper(), op))
    return out


# --------------------------------------------------------------------------- #
# operation_id
# --------------------------------------------------------------------------- #

def test_every_route_declares_operation_id(spec: dict[str, Any]) -> None:
    """Every route under /api/v1/* must declare an explicit ``operation_id``.

    Without it, FastAPI generates one from the handler's qualified
    name (``create_jump_route`` → ``createJumpRoute`` in JS / TS).
    Generated SDK methods carry the noisy suffix; consumers pin to
    it; a later rename of the handler function breaks every client.
    Explicit opids are part of the contract.
    """
    bad: list[tuple[str, str]] = []
    for path, method, op in _operations(spec):
        if not path.startswith("/api/v1/"):
            # /docs, /openapi.json, /redoc, the static SPA mount —
            # the auto-generated FastAPI surface is allowed to use
            # whatever opids FastAPI picks.
            continue
        if not op.get("operationId"):
            bad.append((method, path))
    assert not bad, f"routes missing operation_id: {bad}"


def test_operation_ids_are_unique(spec: dict[str, Any]) -> None:
    """An operation_id collision produces an invalid OpenAPI document.

    Most SDK generators bail on duplicates; some pick one route and
    silently drop the other. Either failure mode is a regression we
    catch here.
    """
    seen: dict[str, tuple[str, str]] = {}
    dups: list[tuple[str, str, str, str]] = []
    for path, method, op in _operations(spec):
        opid = op.get("operationId")
        if not opid:
            continue
        if opid in seen:
            prev_method, prev_path = seen[opid]
            dups.append((opid, prev_method, prev_path, f"{method} {path}"))
        else:
            seen[opid] = (method, path)
    assert not dups, f"duplicate operation_ids: {dups}"


def test_operation_ids_have_no_route_suffix(spec: dict[str, Any]) -> None:
    """Reject opids that still carry the ``_route`` handler-suffix tic.

    A common slip: forgetting to override and getting FastAPI's
    auto-generated ``..._route`` from the handler name. Pin against
    it explicitly.
    """
    bad: list[tuple[str, str, str]] = []
    for path, method, op in _operations(spec):
        opid = op.get("operationId", "")
        if opid.endswith("_route") or opid.endswith("Route"):
            bad.append((method, path, opid))
    assert not bad, f"opids with route suffix: {bad}"


# --------------------------------------------------------------------------- #
# responses
# --------------------------------------------------------------------------- #

_PROBLEM_REF = "#/components/schemas/ProblemDetails"


def test_every_route_declares_500(spec: dict[str, Any]) -> None:
    """Every route can produce a 500 (the catch-all unhandled-exception
    path in ``rest.py`` wraps any escaping exception in problem+json).

    Declaring it on every route makes the wire contract uniform and
    lets SDK generators produce a single error-mapping table.
    """
    bad: list[tuple[str, str]] = []
    for path, method, op in _operations(spec):
        if not path.startswith("/api/v1/"):
            continue
        if path == "/api/v1/health":
            # Liveness probe: a 5xx IS the signal. Documenting an
            # error envelope here would be backwards.
            continue
        responses = op.get("responses", {})
        if "500" not in responses:
            bad.append((method, path))
    assert not bad, f"routes missing 500 declaration: {bad}"


def test_create_routes_declare_409_and_422(spec: dict[str, Any]) -> None:
    """POSTs that create a new resource at a collection root must
    declare 409 (conflict / duplicate) and 422 (validation).

    Per-entity creates can collide on a uniqueness invariant
    (``jump_number``, rig nickname, etc.) and can fail Pydantic /
    XSD validation. Declaring both makes the contract explicit.
    """
    bad: list[tuple[str, str, list[str]]] = []
    for path, method, op in _operations(spec):
        if method != "POST":
            continue
        if not path.startswith("/api/v1/"):
            continue
        # Collection-root POSTs: path contains no ``{`` after the
        # ``/api/v1/<resource>`` prefix. POSTs to sub-paths
        # (``/jumps/{id}/attachments``) are tested separately.
        # Use a simple heuristic: split on '/' and check whether
        # any later segment is a variable.
        segments = path.split("/")[3:]  # drop "", "api", "v1"
        if any(seg.startswith("{") for seg in segments):
            continue
        responses = op.get("responses", {})
        missing = [code for code in ("409", "422") if code not in responses]
        if missing:
            bad.append((method, path, missing))
    assert not bad, f"create routes missing 4xx responses: {bad}"


def test_byid_routes_declare_404(spec: dict[str, Any]) -> None:
    """Every route with a path parameter (typically an id) must declare
    404 — the only error code that means "the addressed resource
    doesn't exist."
    """
    bad: list[tuple[str, str]] = []
    for path, method, op in _operations(spec):
        if not path.startswith("/api/v1/"):
            continue
        if "{" not in path:
            continue
        responses = op.get("responses", {})
        if "404" not in responses:
            bad.append((method, path))
    assert not bad, f"by-id routes missing 404 declaration: {bad}"


def test_error_responses_reference_problem_details_schema(
    spec: dict[str, Any],
) -> None:
    """Every error response component points at the same
    ``ProblemDetails`` schema. Catches accidental drift to inline
    schemas or to a different reusable component.
    """
    components = spec.get("components", {})
    responses = components.get("responses", {})
    assert responses, "expected components.responses with reusable error envelopes"
    expected = {"NotFound", "Conflict", "ValidationFailed", "IntegrityError", "Internal"}
    assert expected <= set(responses), (
        f"missing reusable response components: {expected - set(responses)}"
    )
    for name in expected:
        body = responses[name]
        content = body.get("content", {})
        problem = content.get("application/problem+json", {})
        schema = problem.get("schema", {})
        assert schema.get("$ref") == _PROBLEM_REF, (
            f"{name} response does not $ref ProblemDetails: {schema}"
        )
