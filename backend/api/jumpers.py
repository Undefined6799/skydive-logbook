"""REST endpoints for jumpers (R.2.0c.i, D33; D47 attachments in C.4).

Thin by design (D7): each handler translates HTTP to a service call,
lets ``ServiceError`` subclasses bubble up to the ``on_service_error``
handler in ``rest.py`` which emits RFC 9457 problem+json (D16).

Routes:

  * ``POST   /api/v1/jumpers``                                   → ``create_jumper``.
    JSON body matching ``JumperCreate``. 201 + ``Location`` header.
  * ``GET    /api/v1/jumpers``                                   → ``list_jumpers``,
    paginated. Returns full ``Jumper`` shape.
  * ``GET    /api/v1/jumpers/{jumper_id}``                       → ``get_jumper``.
  * ``PUT    /api/v1/jumpers/{jumper_id}``                       → ``update_jumper``.
    JSON body matching ``JumperUpdate`` — full replace, identity-only.
  * ``DELETE /api/v1/jumpers/{jumper_id}``                       → ``delete_jumper``.
    204 on success; soft-delete to ``.trash/jumpers/`` (D19).
  * ``POST   /api/v1/jumpers/{jumper_id}/attachments``           → ``add_attachment_to_jumper``.
    ``multipart/form-data`` with one ``file`` part. Per D47 / C.4,
    one attachment per request — credential cards arrive one-per-
    credential, batch upload is not a v0.1 use case.
  * ``DELETE /api/v1/jumpers/{jumper_id}/attachments/{attachment_id}``
                                                                 → ``delete_attachment_from_jumper``.
    Hard delete; refuses if any credential's ``card_attachment_id``
    references the attachment (409, FieldError per reference).
  * Credential CRUD (D47, Phases D.1 and D.2). Each of the five
    collections — memberships, cops, ratings, tandem-ratings,
    medicals — exposes the same three-verb shape:
        POST   /jumpers/{id}/<collection>             — append one
        PUT    /jumpers/{id}/<collection>/{record_id} — full replace
        DELETE /jumpers/{id}/<collection>/{record_id} — remove one
    The body shape on POST/PUT is the matching ``*Create`` Pydantic
    model (no ``id`` field — server mints on POST, URL path id wins
    on PUT). All five surface the same cross-reference 422 when
    ``card_attachment_id`` doesn't match an attachment on the
    jumper.
  * ``PATCH  /jumpers/{id}/tandem-ratings/{id}/currency-reset`` —
    the one tandem-rating-specific operation (D47, Phase D.3).
    Stamps ``currency_reset_at`` to today's UTC date so the Phase E
    currency calculator suppresses the not-current warning after a
    supervised re-currency jump.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status

from ..models.jumper import (
    CopCreate,
    FederationRatingCreate,
    Jumper,
    JumperCreate,
    JumperUpdate,
    MedicalCreate,
    MembershipCreate,
    TandemRatingCreate,
)
from ..services import jumper_credential_service, jumper_service
from ..services.jumper_service import Upload
from .deps import get_logbook_root, get_user_id

router = APIRouter(prefix="/api/v1/jumpers", tags=["jumpers"])

# 64 KiB per read — bounded memory on multi-megabyte credential card
# uploads, large enough to avoid per-chunk syscall overhead on small
# files. Mirrors the constant in api/jumps.py.
_UPLOAD_CHUNK_SIZE = 64 * 1024


def _upload_chunks(upload: UploadFile) -> Iterator[bytes]:
    """Yield ``upload.file`` in fixed-size chunks (D21 streaming).

    UploadFile wraps a SpooledTemporaryFile so reads are already
    bounded in memory at the framework layer; we chunk here for
    ``atomic_write_stream``'s loop. Reads happen from the
    synchronous ``.file`` attribute so this generator composes with
    sync route handlers (D7).
    """
    f = upload.file
    while True:
        chunk = f.read(_UPLOAD_CHUNK_SIZE)
        if not chunk:
            return
        yield chunk


@router.post(
    "",
    response_model=Jumper,
    status_code=status.HTTP_201_CREATED,
    summary="Create a jumper",
    description=(
        "Persist a new jumper record. Body is JSON matching "
        "``JumperCreate``. The server mints the ``id`` (UUIDv4), "
        "stamps ``created_at`` / ``updated_at``, and stamps "
        "``exit_weight_updated_at`` to today's UTC date if the "
        "caller didn't supply one (D33: the staleness clock starts "
        "when the user enters the weight). Writes "
        "``jumpers/<uuid>/jumper.xml`` + ``SHA256SUMS`` atomically "
        "(D10) after XSD validation (D2), creating an empty "
        "``attachments/`` subfolder for credential cards (D47, "
        "Phase C.1). Returns the canonical ``Jumper``."
    ),
)
def create_jumper_route(
    response: Response,
    payload: JumperCreate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jumper:
    created = jumper_service.create_jumper(logbook_root, user_id, payload)
    response.headers["Location"] = f"/api/v1/jumpers/{created.id}"
    return created


@router.get(
    "",
    response_model=list[Jumper],
    summary="List jumpers",
    description=(
        "Return every jumper under ``jumpers/``, newest first by "
        "``created_at``. v0.1 walks the directory and parses each "
        "XML (D2 hardened parser + XSD per file); a future phase "
        "will swap in a SQLite index without changing this contract. "
        "Use ``limit`` + ``offset`` for pagination."
    ),
)
def list_jumpers_route(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> list[Jumper]:
    return jumper_service.list_jumpers(
        logbook_root, user_id, limit=limit, offset=offset
    )


@router.get(
    "/{jumper_id}",
    response_model=Jumper,
    summary="Read a jumper by id",
    description=(
        "Fetch the full jumper including every optional field. The "
        "XML is parsed through the hardened parser and XSD-validated "
        "(D2). Returns 404 with ``code=not_found`` when the id has "
        "no record (or has been soft-deleted to "
        "``.trash/jumpers/``)."
    ),
)
def get_jumper_route(
    jumper_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jumper:
    return jumper_service.get_jumper(logbook_root, user_id, jumper_id)


@router.put(
    "/{jumper_id}",
    response_model=Jumper,
    summary="Update a jumper",
    description=(
        "Full replace. Body is JSON matching ``JumperUpdate`` — every "
        "editable field must be supplied. ``id`` and ``created_at`` "
        "are preserved server-side; ``updated_at`` is bumped. "
        "**Auto-bump rule for ``exit_weight_updated_at``** (D33): if "
        "``exit_weight_lb`` differs from the on-disk value AND the "
        "caller did NOT supply ``exit_weight_updated_at``, the server "
        "stamps it to today's UTC date — the 365-day staleness clock "
        "resets on every weight change. An explicit caller-supplied "
        "date wins (used-gear correction path). A weight-unchanged "
        "metadata edit (e.g. just the name) preserves the on-disk "
        "stamp."
    ),
)
def update_jumper_route(
    jumper_id: UUID,
    payload: JumperUpdate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jumper:
    return jumper_service.update_jumper(
        logbook_root, user_id, jumper_id, payload
    )


@router.delete(
    "/{jumper_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Soft-delete a jumper (D19)",
    description=(
        "Move ``jumpers/<uuid>/`` to "
        "``.trash/jumpers/<timestamp>_<uuid>/``. The whole folder "
        "(jumper.xml + SHA256SUMS + attachments/) goes together "
        "(D47, Phase C.1). "
        "Subsequent ``GET`` returns 404 and ``list`` no longer shows "
        "the jumper. **No cascade**: historical jumps and their "
        "rig snapshots keep their denormalized jumper data per D36 "
        "(snapshots are immutable post-create), and other entities "
        "do not reference the jumper by id in v0.1. The user can "
        "restore by moving the file back."
    ),
)
def delete_jumper_route(
    jumper_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Response:
    jumper_service.delete_jumper(logbook_root, user_id, jumper_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{jumper_id}/attachments",
    response_model=Jumper,
    summary="Attach one file (credential card / medical) to a jumper (D47)",
    description=(
        "Multipart POST that streams one uploaded file into the "
        "jumper's ``attachments/`` subfolder, appends an "
        "``<attachment>`` entry with a server-minted UUID to "
        "``jumper.xml``, and regenerates ``SHA256SUMS`` from the new "
        "XML claims (D25 recovery form). The on-disk filename is "
        "``<attachment_uuid>__<sanitized-filename>`` so two uploads "
        "sharing a user filename never collide.\n\n"
        "**One file per request** in v0.1. Credentials reference an "
        "attachment by id via ``card_attachment_id``, and the user "
        "flow is one credential = one card. Batch upload would add "
        "complexity without solving a real use case; if a future "
        "feature needs it, this endpoint is additive (a new ``files`` "
        "field would not break existing callers).\n\n"
        "Returns the full ``Jumper`` with the new attachment in its "
        "``attachments`` list — same shape as ``GET`` returns. The "
        "caller can read the new attachment's ``id`` from the "
        "response and use it as a credential's ``card_attachment_id`` "
        "in a follow-up request (Phase D)."
    ),
)
def add_attachment_route(
    jumper_id: UUID,
    file: UploadFile = File(  # noqa: B008
        ...,
        description="One file to attach. Filename goes through D4 sanitization.",
    ),
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jumper:
    upload = Upload(
        filename=file.filename or "",
        content_type=file.content_type,
        chunks=_upload_chunks(file),
    )
    return jumper_service.add_attachment_to_jumper(
        logbook_root, user_id, jumper_id, upload
    )


@router.delete(
    "/{jumper_id}/attachments/{attachment_id}",
    response_model=Jumper,
    summary="Remove one attachment from a jumper (D47)",
    description=(
        "Hard-delete one attachment: removes the ``<attachment>`` "
        "entry from ``jumper.xml``, regenerates ``SHA256SUMS``, and "
        "unlinks the file from ``attachments/``.\n\n"
        "**Refuses with 409** when any credential's "
        "``card_attachment_id`` references this attachment — clear "
        "the reference first (Phase D credential endpoints) or the "
        "attachment cannot be removed. The 409 payload's ``errors`` "
        "array carries one ``FieldError`` per reference with a "
        "JSON Pointer like ``#/memberships/0/card_attachment_id`` so "
        "the caller can route the user to the offending credential "
        "without parsing a generic conflict message.\n\n"
        "Returns the updated ``Jumper`` (without the attachment) on "
        "success. No soft-delete — individual attachments are small "
        "and typically restorable from external backup; the folder-"
        "level soft-delete (``DELETE /jumpers/{id}``) covers the "
        "wholesale loss case."
    ),
)
def delete_attachment_route(
    jumper_id: UUID,
    attachment_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jumper:
    return jumper_service.delete_attachment_from_jumper(
        logbook_root, user_id, jumper_id, attachment_id
    )


# --------------------------------------------------------------------------- #
# Memberships (D47, Phase D.1)
# --------------------------------------------------------------------------- #

@router.post(
    "/{jumper_id}/memberships",
    response_model=Jumper,
    status_code=status.HTTP_201_CREATED,
    summary="Add a federation membership to a jumper (D47)",
    description=(
        "JSON body matching ``MembershipCreate``: the server mints a "
        "fresh membership UUID and appends the record to "
        "``<jumper>/<memberships>``. Returns the full updated "
        "``Jumper``.\n\n"
        "If ``card_attachment_id`` is set, it must reference an "
        "attachment already uploaded to this jumper — upload the "
        "card first via ``POST /jumpers/{id}/attachments``, then "
        "include the returned attachment id here. A non-matching id "
        "returns 422 with ``#/card_attachment_id`` so the caller can "
        "fix the reference without parsing a generic message.\n\n"
        "Cross-field rules enforced at Pydantic (per D47): "
        "``org=OTHER`` requires ``org_other``; ``org=CSPA``/``USPA`` "
        "forbid ``org_other``."
    ),
)
def add_membership_route(
    jumper_id: UUID,
    payload: MembershipCreate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jumper:
    return jumper_credential_service.add_membership_to_jumper(
        logbook_root, user_id, jumper_id, payload
    )


@router.put(
    "/{jumper_id}/memberships/{membership_id}",
    response_model=Jumper,
    summary="Replace one membership by id (D47)",
    description=(
        "Full-replace one membership in the jumper's list. The "
        "membership id comes from the URL path; the body's "
        "``MembershipCreate`` shape supplies every editable field. "
        "404 when no membership with this id is on the jumper.\n\n"
        "Same ``card_attachment_id`` cross-reference rule as POST: "
        "an unknown id returns 422."
    ),
)
def update_membership_route(
    jumper_id: UUID,
    membership_id: UUID,
    payload: MembershipCreate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jumper:
    return jumper_credential_service.update_membership_on_jumper(
        logbook_root, user_id, jumper_id, membership_id, payload
    )


@router.delete(
    "/{jumper_id}/memberships/{membership_id}",
    response_model=Jumper,
    summary="Remove one membership by id (D47)",
    description=(
        "Hard-delete one membership from the jumper's list. The "
        "underlying attachment (if the membership had a "
        "``card_attachment_id``) is preserved on disk; delete it "
        "separately via ``DELETE /jumpers/{id}/attachments/{att_id}``."
    ),
)
def delete_membership_route(
    jumper_id: UUID,
    membership_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jumper:
    return jumper_credential_service.delete_membership_from_jumper(
        logbook_root, user_id, jumper_id, membership_id
    )


# --------------------------------------------------------------------------- #
# CoPs (D47, Phase D.2)
# --------------------------------------------------------------------------- #

@router.post(
    "/{jumper_id}/cops",
    response_model=Jumper,
    status_code=status.HTTP_201_CREATED,
    summary="Add a Certificate of Proficiency / license to a jumper (D47)",
    description=(
        "JSON body matching ``CopCreate``. ``level`` must match the "
        "per-org closed enum: CSPACopLevel for ``CSPA`` (solo / a / "
        "b / c / d), USPACopLevel for ``USPA`` (a / b / c / d), free "
        "text up to 40 chars for ``OTHER``. Returns the full updated "
        "``Jumper``."
    ),
)
def add_cop_route(
    jumper_id: UUID,
    payload: CopCreate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jumper:
    return jumper_credential_service.add_cop_to_jumper(
        logbook_root, user_id, jumper_id, payload
    )


@router.put(
    "/{jumper_id}/cops/{cop_id}",
    response_model=Jumper,
    summary="Replace one CoP by id (D47)",
)
def update_cop_route(
    jumper_id: UUID,
    cop_id: UUID,
    payload: CopCreate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jumper:
    return jumper_credential_service.update_cop_on_jumper(
        logbook_root, user_id, jumper_id, cop_id, payload
    )


@router.delete(
    "/{jumper_id}/cops/{cop_id}",
    response_model=Jumper,
    summary="Remove one CoP by id (D47)",
)
def delete_cop_route(
    jumper_id: UUID,
    cop_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jumper:
    return jumper_credential_service.delete_cop_from_jumper(
        logbook_root, user_id, jumper_id, cop_id
    )


# --------------------------------------------------------------------------- #
# Federation ratings (D47, Phase D.2)
# --------------------------------------------------------------------------- #

@router.post(
    "/{jumper_id}/ratings",
    response_model=Jumper,
    status_code=status.HTTP_201_CREATED,
    summary="Add a federation-issued rating to a jumper (D47)",
    description=(
        "JSON body matching ``FederationRatingCreate``. ``code`` "
        "must match the per-org closed enum: CSPARatingCode for "
        "``CSPA``, USPARatingCode for ``USPA``, free text for "
        "``OTHER``. Tandem ratings live in a separate collection — "
        "they are manufacturer-issued, not federation-issued (D47). "
        "Returns the full updated ``Jumper``."
    ),
)
def add_rating_route(
    jumper_id: UUID,
    payload: FederationRatingCreate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jumper:
    return jumper_credential_service.add_rating_to_jumper(
        logbook_root, user_id, jumper_id, payload
    )


@router.put(
    "/{jumper_id}/ratings/{rating_id}",
    response_model=Jumper,
    summary="Replace one federation rating by id (D47)",
)
def update_rating_route(
    jumper_id: UUID,
    rating_id: UUID,
    payload: FederationRatingCreate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jumper:
    return jumper_credential_service.update_rating_on_jumper(
        logbook_root, user_id, jumper_id, rating_id, payload
    )


@router.delete(
    "/{jumper_id}/ratings/{rating_id}",
    response_model=Jumper,
    summary="Remove one federation rating by id (D47)",
)
def delete_rating_route(
    jumper_id: UUID,
    rating_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jumper:
    return jumper_credential_service.delete_rating_from_jumper(
        logbook_root, user_id, jumper_id, rating_id
    )


# --------------------------------------------------------------------------- #
# Tandem ratings — manufacturer-issued (D47, Phase D.2)
# --------------------------------------------------------------------------- #

@router.post(
    "/{jumper_id}/tandem-ratings",
    response_model=Jumper,
    status_code=status.HTTP_201_CREATED,
    summary="Add a manufacturer-issued tandem instructor rating (D47)",
    description=(
        "JSON body matching ``TandemRatingCreate``. ``system`` is "
        "the closed TandemSystem enum (UPT Vector / Sigma, Strong "
        "Dual Hawk, OTHER). When ``system=OTHER`` the "
        "``system_other`` field carries the free-text system name; "
        "otherwise it must be absent. Returns the full updated "
        "``Jumper``."
    ),
)
def add_tandem_rating_route(
    jumper_id: UUID,
    payload: TandemRatingCreate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jumper:
    return jumper_credential_service.add_tandem_rating_to_jumper(
        logbook_root, user_id, jumper_id, payload
    )


@router.put(
    "/{jumper_id}/tandem-ratings/{tandem_rating_id}",
    response_model=Jumper,
    summary="Replace one tandem rating by id (D47)",
)
def update_tandem_rating_route(
    jumper_id: UUID,
    tandem_rating_id: UUID,
    payload: TandemRatingCreate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jumper:
    return jumper_credential_service.update_tandem_rating_on_jumper(
        logbook_root, user_id, jumper_id, tandem_rating_id, payload
    )


@router.delete(
    "/{jumper_id}/tandem-ratings/{tandem_rating_id}",
    response_model=Jumper,
    summary="Remove one tandem rating by id (D47)",
)
def delete_tandem_rating_route(
    jumper_id: UUID,
    tandem_rating_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jumper:
    return jumper_credential_service.delete_tandem_rating_from_jumper(
        logbook_root, user_id, jumper_id, tandem_rating_id
    )


@router.patch(
    "/{jumper_id}/tandem-ratings/{tandem_rating_id}/currency-reset",
    response_model=Jumper,
    summary="Declare current after a supervised re-currency jump (D47)",
    description=(
        "Stamps ``currency_reset_at`` on the tandem rating to "
        "today's UTC date. No request body — the only effect is "
        "the timestamp.\n\n"
        "The Phase E currency calculator suppresses the not-current "
        "warning when ``currency_reset_at`` is within the system's "
        "currency window (UPT 90 d, Strong 12 mo). Use this after a "
        "supervised re-currency jump that the calculator can't see "
        "from the jump index alone (e.g. the supervised jump itself "
        "is not yet logged; or the jumper has been current outside "
        "the logbook). The reset only suppresses the warning — the "
        "underlying jump-derived counters remain visible in the "
        "detail view so the user can see what the calculator is "
        "reading.\n\n"
        "404 if no tandem rating with this id exists on the jumper."
    ),
)
def reset_tandem_rating_currency_route(
    jumper_id: UUID,
    tandem_rating_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jumper:
    return jumper_credential_service.reset_tandem_rating_currency(
        logbook_root, user_id, jumper_id, tandem_rating_id
    )


# --------------------------------------------------------------------------- #
# Medicals (D47, Phase D.2)
# --------------------------------------------------------------------------- #

@router.post(
    "/{jumper_id}/medicals",
    response_model=Jumper,
    status_code=status.HTTP_201_CREATED,
    summary="Add a government-issued aviation medical to a jumper (D47)",
    description=(
        "JSON body matching ``MedicalCreate``. ``kind`` is currently "
        "``class_iii`` only; higher classes can land additively. "
        "``issuing_authority`` is free text — type the agency name "
        "from the certificate (e.g. ``Transport Canada``, ``FAA``). "
        "Returns the full updated ``Jumper``."
    ),
)
def add_medical_route(
    jumper_id: UUID,
    payload: MedicalCreate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jumper:
    return jumper_credential_service.add_medical_to_jumper(
        logbook_root, user_id, jumper_id, payload
    )


@router.put(
    "/{jumper_id}/medicals/{medical_id}",
    response_model=Jumper,
    summary="Replace one medical by id (D47)",
)
def update_medical_route(
    jumper_id: UUID,
    medical_id: UUID,
    payload: MedicalCreate,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jumper:
    return jumper_credential_service.update_medical_on_jumper(
        logbook_root, user_id, jumper_id, medical_id, payload
    )


@router.delete(
    "/{jumper_id}/medicals/{medical_id}",
    response_model=Jumper,
    summary="Remove one medical by id (D47)",
)
def delete_medical_route(
    jumper_id: UUID,
    medical_id: UUID,
    logbook_root: Path = Depends(get_logbook_root),
    user_id: str = Depends(get_user_id),
) -> Jumper:
    return jumper_credential_service.delete_medical_from_jumper(
        logbook_root, user_id, jumper_id, medical_id
    )
