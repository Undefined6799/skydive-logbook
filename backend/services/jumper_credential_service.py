"""Jumper credential CRUD service (D47, Phase D).

Each of the five credential collections — memberships, cops, ratings,
tandem_ratings, medicals — gets the same CRUD shape:

  * ``add_<kind>_to_jumper`` — append one record. Server mints the id.
  * ``update_<kind>_on_jumper`` — full-replace one record by id (the
    URL path id, not a body field).
  * ``delete_<kind>_from_jumper`` — remove one record by id.

Phase D.1 ships the membership variant; D.2 replicates the pattern
across the other four collections.

Invariants (every write):
  * Pydantic re-validates the merged Jumper before serialization
    (defensive — :class:`MembershipCreate` already validated the
    payload, but the merged shape is what hits the XSD).
  * Cross-reference: when a record's ``card_attachment_id`` is set,
    it must match an attachment id already on the same jumper. The
    service rejects (422) before any disk write — D47 specifies that
    the "every card_attachment_id resolves to an attachment with
    that id" invariant lives at the service layer (the XSD has no
    keyref support).
  * The merged Jumper is XSD-validated before
    :func:`backend.services.jumper_service._write_jumper` lands the
    bytes. ``_write_jumper`` regenerates the SHA256SUMS manifest and
    creates / preserves the ``attachments/`` subfolder per C.1.

All public functions take a ``user_id`` per D8 and ignore it (single-
jumper convention, same posture as the rest of jumper_service).
"""
from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError

from ..api.errors import (
    FieldError,
    NotFoundError,
    ValidationFailedError,
    validation_failed_from_pydantic,
)
from ..models.jumper import (
    Cop,
    CopCreate,
    FederationRating,
    FederationRatingCreate,
    Jumper,
    Medical,
    MedicalCreate,
    Membership,
    MembershipCreate,
    TandemRating,
    TandemRatingCreate,
)
from ..xml.validator import XMLError
from . import jumper_service
from ._timestamps import now_utc_iso
from ._write_lock import with_writer_lock

_logger = logging.getLogger("backend.services.jumper_credential")


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def _assert_attachment_exists(
    jumper: Jumper, card_attachment_id: UUID | None
) -> None:
    """Raise :class:`ValidationFailedError` if ``card_attachment_id``
    references an attachment that isn't on the jumper.

    The check is structural: a credential can carry a None
    ``card_attachment_id`` (no card uploaded yet), or a UUID that
    matches one of ``jumper.attachments[*].id``. Anything else is a
    422 with a ``#/card_attachment_id`` pointer so the caller can
    fix the request without parsing a generic message.

    The inverse direction (refusing to delete an attachment that any
    credential references) is enforced in
    :func:`backend.services.jumper_service.delete_attachment_from_jumper`.
    Together the two halves keep the cross-reference consistent
    without an XSD keyref (XSD 1.0 has none).
    """
    if card_attachment_id is None:
        return
    known = {a.id for a in jumper.attachments}
    if card_attachment_id not in known:
        raise ValidationFailedError(
            "card_attachment_id does not reference an attachment on this jumper",
            errors=[
                FieldError(
                    pointer="#/card_attachment_id",
                    detail=(
                        f"attachment {card_attachment_id} is not in the "
                        "jumper's attachments list — upload the file first, "
                        "then reference its id here"
                    ),
                ),
            ],
        )




def _persist_with_credentials_update(
    logbook_root: Path,
    jumper: Jumper,
    **updates: object,
) -> Jumper:
    """Write ``jumper`` (with the supplied collection updates merged)
    to disk via :func:`jumper_service._write_jumper`.

    Bumps ``updated_at``. Preserves every other field including the
    untouched credential collections and the attachments list.
    Raises :class:`ValidationFailedError` if the merged Jumper fails
    XSD validation (defensive — the inputs are already model-
    validated, but a future contributor mistake would surface here).
    """
    # ``_write_jumper`` is a deliberate package-private helper shared
    # with this credential service; the underscore signals "service-
    # internal" while the cross-module call is intentional. Per the
    # pyright policy D-entry: narrow ``# pyright: ignore`` rather than
    # dropping the underscore (which would broaden visibility).
    merged = jumper.model_copy(
        update={
            **updates,
            "updated_at": now_utc_iso(),
        },
    )
    try:
        jumper_service._write_jumper(logbook_root, merged)  # pyright: ignore[reportPrivateUsage]
    except XMLError as exc:
        raise ValidationFailedError(
            f"updated jumper XML failed XSD validation: {exc}",
        ) from exc
    return merged


def _load_jumper(logbook_root: Path, jumper_id: UUID) -> Jumper:
    """Resolve the jumper folder + parse the XML, or raise 404.

    Wraps the private helpers in :mod:`jumper_service` so this module
    doesn't have to know the folder layout — keeping the credential
    service one level above the storage shape.
    """
    folder = jumper_service._jumper_folder(logbook_root, jumper_id)  # pyright: ignore[reportPrivateUsage]
    if not folder.is_dir():
        raise NotFoundError(f"jumper {jumper_id} not found")
    return jumper_service._read_jumper(folder)  # pyright: ignore[reportPrivateUsage]


# --------------------------------------------------------------------------- #
# Memberships (D.1)
# --------------------------------------------------------------------------- #

@with_writer_lock
def add_membership_to_jumper(
    logbook_root: Path,
    user_id: str,
    jumper_id: UUID,
    payload: MembershipCreate,
) -> Jumper:
    """Append one membership to the jumper's record.

    Server mints the membership UUID. The merged jumper is XSD-validated
    before the write per D2; on success, ``jumper.xml`` and the manifest
    are atomically updated.

    Raises:
      :class:`NotFoundError` (404): jumper not found.
      :class:`ValidationFailedError` (422): payload's
        ``card_attachment_id`` doesn't match any attachment on the
        jumper, OR the merged Jumper failed Pydantic / XSD validation.
    """
    del user_id  # v0.1: see jumper_service.create_jumper

    jumper = _load_jumper(logbook_root, jumper_id)
    _assert_attachment_exists(jumper, payload.card_attachment_id)

    try:
        membership = Membership(id=uuid4(), **payload.model_dump())
    except ValidationError as exc:
        raise validation_failed_from_pydantic(
            exc, "membership validation failed"
        ) from exc

    updated = _persist_with_credentials_update(
        logbook_root,
        jumper,
        memberships=[*jumper.memberships, membership],
    )

    _logger.info(
        "jumper_membership_added",
        extra={
            "jumper_id": str(jumper_id),
            "membership_id": str(membership.id),
            "org": membership.org.value,
        },
    )
    return updated


@with_writer_lock
def update_membership_on_jumper(
    logbook_root: Path,
    user_id: str,
    jumper_id: UUID,
    membership_id: UUID,
    payload: MembershipCreate,
) -> Jumper:
    """Full-replace one membership by its id.

    The id comes from the URL path. The body's payload supplies every
    editable field; the server reuses the existing record's id (so a
    re-PUT after a list reorder doesn't reshuffle the underlying
    UUIDs).

    Raises:
      :class:`NotFoundError` (404): jumper not found, or no membership
        with this id on the jumper.
      :class:`ValidationFailedError` (422): same as add.
    """
    del user_id

    jumper = _load_jumper(logbook_root, jumper_id)

    if not any(m.id == membership_id for m in jumper.memberships):
        raise NotFoundError(
            f"membership {membership_id} not found on jumper {jumper_id}"
        )

    _assert_attachment_exists(jumper, payload.card_attachment_id)

    try:
        replaced = Membership(id=membership_id, **payload.model_dump())
    except ValidationError as exc:
        raise validation_failed_from_pydantic(
            exc, "membership validation failed"
        ) from exc

    new_memberships = [
        replaced if m.id == membership_id else m for m in jumper.memberships
    ]
    updated = _persist_with_credentials_update(
        logbook_root, jumper, memberships=new_memberships
    )

    _logger.info(
        "jumper_membership_updated",
        extra={
            "jumper_id": str(jumper_id),
            "membership_id": str(membership_id),
            "org": replaced.org.value,
        },
    )
    return updated


@with_writer_lock
def delete_membership_from_jumper(
    logbook_root: Path,
    user_id: str,
    jumper_id: UUID,
    membership_id: UUID,
) -> Jumper:
    """Remove one membership by id.

    No cross-reference protection on this side: a membership doesn't
    own anything that other credentials point at. The user might want
    to keep the corresponding attachment around (the card is still
    valid even if they're no longer a member); the attachment stays
    on disk and is independently deletable later.

    Raises:
      :class:`NotFoundError` (404): jumper or membership not found.
    """
    del user_id

    jumper = _load_jumper(logbook_root, jumper_id)

    if not any(m.id == membership_id for m in jumper.memberships):
        raise NotFoundError(
            f"membership {membership_id} not found on jumper {jumper_id}"
        )

    new_memberships = [
        m for m in jumper.memberships if m.id != membership_id
    ]
    updated = _persist_with_credentials_update(
        logbook_root, jumper, memberships=new_memberships
    )

    _logger.info(
        "jumper_membership_deleted",
        extra={
            "jumper_id": str(jumper_id),
            "membership_id": str(membership_id),
        },
    )
    return updated


# --------------------------------------------------------------------------- #
# CoPs (D.2)
# --------------------------------------------------------------------------- #

@with_writer_lock
def add_cop_to_jumper(
    logbook_root: Path,
    user_id: str,
    jumper_id: UUID,
    payload: CopCreate,
) -> Jumper:
    """Append one Certificate of Proficiency / license to the jumper.

    Mirrors :func:`add_membership_to_jumper`. Per-org level enum
    rules (CSPACopLevel for CSPA, USPACopLevel for USPA, free text
    for OTHER) are enforced at the Pydantic ``CopCreate`` layer.
    """
    del user_id

    jumper = _load_jumper(logbook_root, jumper_id)
    _assert_attachment_exists(jumper, payload.card_attachment_id)

    try:
        cop = Cop(id=uuid4(), **payload.model_dump())
    except ValidationError as exc:
        raise validation_failed_from_pydantic(
            exc, "cop validation failed"
        ) from exc

    updated = _persist_with_credentials_update(
        logbook_root, jumper, cops=[*jumper.cops, cop]
    )

    # ``level`` collides with ``LogRecord.level`` and is rejected by
    # D27's reserved-field guard. Use ``cop_level`` instead — same
    # data, namespaced to the credential so the operator-visible
    # log entry still distinguishes "USPA A" from "CSPA B".
    _logger.info(
        "jumper_cop_added",
        extra={
            "jumper_id": str(jumper_id),
            "cop_id": str(cop.id),
            "org": cop.org.value,
            "cop_level": cop.level,
        },
    )
    return updated


@with_writer_lock
def update_cop_on_jumper(
    logbook_root: Path,
    user_id: str,
    jumper_id: UUID,
    cop_id: UUID,
    payload: CopCreate,
) -> Jumper:
    del user_id

    jumper = _load_jumper(logbook_root, jumper_id)

    if not any(c.id == cop_id for c in jumper.cops):
        raise NotFoundError(
            f"cop {cop_id} not found on jumper {jumper_id}"
        )

    _assert_attachment_exists(jumper, payload.card_attachment_id)

    try:
        replaced = Cop(id=cop_id, **payload.model_dump())
    except ValidationError as exc:
        raise validation_failed_from_pydantic(
            exc, "cop validation failed"
        ) from exc

    new_cops = [replaced if c.id == cop_id else c for c in jumper.cops]
    updated = _persist_with_credentials_update(
        logbook_root, jumper, cops=new_cops
    )

    # See ``add_cop_to_jumper`` — ``level`` collides with
    # ``LogRecord.level``; namespace as ``cop_level``.
    _logger.info(
        "jumper_cop_updated",
        extra={
            "jumper_id": str(jumper_id),
            "cop_id": str(cop_id),
            "org": replaced.org.value,
            "cop_level": replaced.level,
        },
    )
    return updated


@with_writer_lock
def delete_cop_from_jumper(
    logbook_root: Path,
    user_id: str,
    jumper_id: UUID,
    cop_id: UUID,
) -> Jumper:
    del user_id

    jumper = _load_jumper(logbook_root, jumper_id)

    if not any(c.id == cop_id for c in jumper.cops):
        raise NotFoundError(
            f"cop {cop_id} not found on jumper {jumper_id}"
        )

    new_cops = [c for c in jumper.cops if c.id != cop_id]
    updated = _persist_with_credentials_update(
        logbook_root, jumper, cops=new_cops
    )

    _logger.info(
        "jumper_cop_deleted",
        extra={"jumper_id": str(jumper_id), "cop_id": str(cop_id)},
    )
    return updated


# --------------------------------------------------------------------------- #
# Federation ratings (D.2)
# --------------------------------------------------------------------------- #

@with_writer_lock
def add_rating_to_jumper(
    logbook_root: Path,
    user_id: str,
    jumper_id: UUID,
    payload: FederationRatingCreate,
) -> Jumper:
    """Append one federation-issued rating to the jumper.

    Per-org code enum rules (CSPARatingCode for CSPA, USPARatingCode
    for USPA, free text for OTHER) are enforced at the Pydantic
    ``FederationRatingCreate`` layer. Tandem ratings are a separate
    collection (manufacturer-issued, not federation-issued); see
    :func:`add_tandem_rating_to_jumper`.
    """
    del user_id

    jumper = _load_jumper(logbook_root, jumper_id)
    _assert_attachment_exists(jumper, payload.card_attachment_id)

    try:
        rating = FederationRating(id=uuid4(), **payload.model_dump())
    except ValidationError as exc:
        raise validation_failed_from_pydantic(
            exc, "rating validation failed"
        ) from exc

    updated = _persist_with_credentials_update(
        logbook_root, jumper, ratings=[*jumper.ratings, rating]
    )

    _logger.info(
        "jumper_rating_added",
        extra={
            "jumper_id": str(jumper_id),
            "rating_id": str(rating.id),
            "org": rating.org.value,
            "code": rating.code,
        },
    )
    return updated


@with_writer_lock
def update_rating_on_jumper(
    logbook_root: Path,
    user_id: str,
    jumper_id: UUID,
    rating_id: UUID,
    payload: FederationRatingCreate,
) -> Jumper:
    del user_id

    jumper = _load_jumper(logbook_root, jumper_id)

    if not any(r.id == rating_id for r in jumper.ratings):
        raise NotFoundError(
            f"rating {rating_id} not found on jumper {jumper_id}"
        )

    _assert_attachment_exists(jumper, payload.card_attachment_id)

    try:
        replaced = FederationRating(id=rating_id, **payload.model_dump())
    except ValidationError as exc:
        raise validation_failed_from_pydantic(
            exc, "rating validation failed"
        ) from exc

    new_ratings = [
        replaced if r.id == rating_id else r for r in jumper.ratings
    ]
    updated = _persist_with_credentials_update(
        logbook_root, jumper, ratings=new_ratings
    )

    _logger.info(
        "jumper_rating_updated",
        extra={
            "jumper_id": str(jumper_id),
            "rating_id": str(rating_id),
            "org": replaced.org.value,
            "code": replaced.code,
        },
    )
    return updated


@with_writer_lock
def delete_rating_from_jumper(
    logbook_root: Path,
    user_id: str,
    jumper_id: UUID,
    rating_id: UUID,
) -> Jumper:
    del user_id

    jumper = _load_jumper(logbook_root, jumper_id)

    if not any(r.id == rating_id for r in jumper.ratings):
        raise NotFoundError(
            f"rating {rating_id} not found on jumper {jumper_id}"
        )

    new_ratings = [r for r in jumper.ratings if r.id != rating_id]
    updated = _persist_with_credentials_update(
        logbook_root, jumper, ratings=new_ratings
    )

    _logger.info(
        "jumper_rating_deleted",
        extra={"jumper_id": str(jumper_id), "rating_id": str(rating_id)},
    )
    return updated


# --------------------------------------------------------------------------- #
# Tandem ratings (D.2)
# --------------------------------------------------------------------------- #

@with_writer_lock
def add_tandem_rating_to_jumper(
    logbook_root: Path,
    user_id: str,
    jumper_id: UUID,
    payload: TandemRatingCreate,
) -> Jumper:
    """Append one manufacturer-issued tandem instructor rating.

    Mirrors :func:`add_rating_to_jumper` but the credential is
    scoped to a tandem rig system (UPT Vector / Sigma, Strong Dual
    Hawk, OTHER) per D47's architectural correction.
    """
    del user_id

    jumper = _load_jumper(logbook_root, jumper_id)
    _assert_attachment_exists(jumper, payload.card_attachment_id)

    try:
        tandem = TandemRating(id=uuid4(), **payload.model_dump())
    except ValidationError as exc:
        raise validation_failed_from_pydantic(
            exc, "tandem rating validation failed"
        ) from exc

    updated = _persist_with_credentials_update(
        logbook_root,
        jumper,
        tandem_ratings=[*jumper.tandem_ratings, tandem],
    )

    _logger.info(
        "jumper_tandem_rating_added",
        extra={
            "jumper_id": str(jumper_id),
            "tandem_rating_id": str(tandem.id),
            "system": tandem.system.value,
        },
    )
    return updated


@with_writer_lock
def update_tandem_rating_on_jumper(
    logbook_root: Path,
    user_id: str,
    jumper_id: UUID,
    tandem_rating_id: UUID,
    payload: TandemRatingCreate,
) -> Jumper:
    del user_id

    jumper = _load_jumper(logbook_root, jumper_id)

    if not any(t.id == tandem_rating_id for t in jumper.tandem_ratings):
        raise NotFoundError(
            f"tandem_rating {tandem_rating_id} not found on jumper {jumper_id}"
        )

    _assert_attachment_exists(jumper, payload.card_attachment_id)

    try:
        replaced = TandemRating(id=tandem_rating_id, **payload.model_dump())
    except ValidationError as exc:
        raise validation_failed_from_pydantic(
            exc, "tandem rating validation failed"
        ) from exc

    new_list = [
        replaced if t.id == tandem_rating_id else t
        for t in jumper.tandem_ratings
    ]
    updated = _persist_with_credentials_update(
        logbook_root, jumper, tandem_ratings=new_list
    )

    _logger.info(
        "jumper_tandem_rating_updated",
        extra={
            "jumper_id": str(jumper_id),
            "tandem_rating_id": str(tandem_rating_id),
            "system": replaced.system.value,
        },
    )
    return updated


@with_writer_lock
def reset_tandem_rating_currency(
    logbook_root: Path,
    user_id: str,
    jumper_id: UUID,
    tandem_rating_id: UUID,
) -> Jumper:
    """Stamp ``currency_reset_at`` on a tandem rating to today's UTC date.

    The manual "I declare I am current after a supervised re-currency
    jump" override (D47, Phase D.3). When the calculator (Phase E)
    sees a ``currency_reset_at`` within the system's currency window
    (UPT 90 d, Strong 12 mo) it treats the jumper as current
    regardless of recent jump activity.

    No request body — the only effect is setting ``currency_reset_at``
    to today. Subsequent calls re-stamp; the rating's other fields
    (system, expiry_date, card_attachment_id, notes) are untouched.
    Use the full PUT (``update_tandem_rating_on_jumper``) when those
    need to change.

    Raises:
      :class:`NotFoundError` (404): jumper not found, or no tandem
        rating with this id on the jumper.
    """
    del user_id

    jumper = _load_jumper(logbook_root, jumper_id)

    target = next(
        (t for t in jumper.tandem_ratings if t.id == tandem_rating_id),
        None,
    )
    if target is None:
        raise NotFoundError(
            f"tandem_rating {tandem_rating_id} not found on jumper {jumper_id}"
        )

    today = jumper_service._today_utc()  # pyright: ignore[reportPrivateUsage]
    new_list = [
        t.model_copy(update={"currency_reset_at": today})
        if t.id == tandem_rating_id
        else t
        for t in jumper.tandem_ratings
    ]
    updated = _persist_with_credentials_update(
        logbook_root, jumper, tandem_ratings=new_list
    )

    _logger.info(
        "jumper_tandem_rating_currency_reset",
        extra={
            "jumper_id": str(jumper_id),
            "tandem_rating_id": str(tandem_rating_id),
            "system": target.system.value,
            "currency_reset_at": today.isoformat(),
        },
    )
    return updated


@with_writer_lock
def delete_tandem_rating_from_jumper(
    logbook_root: Path,
    user_id: str,
    jumper_id: UUID,
    tandem_rating_id: UUID,
) -> Jumper:
    del user_id

    jumper = _load_jumper(logbook_root, jumper_id)

    if not any(t.id == tandem_rating_id for t in jumper.tandem_ratings):
        raise NotFoundError(
            f"tandem_rating {tandem_rating_id} not found on jumper {jumper_id}"
        )

    new_list = [
        t for t in jumper.tandem_ratings if t.id != tandem_rating_id
    ]
    updated = _persist_with_credentials_update(
        logbook_root, jumper, tandem_ratings=new_list
    )

    _logger.info(
        "jumper_tandem_rating_deleted",
        extra={
            "jumper_id": str(jumper_id),
            "tandem_rating_id": str(tandem_rating_id),
        },
    )
    return updated


# --------------------------------------------------------------------------- #
# Medicals (D.2)
# --------------------------------------------------------------------------- #

@with_writer_lock
def add_medical_to_jumper(
    logbook_root: Path,
    user_id: str,
    jumper_id: UUID,
    payload: MedicalCreate,
) -> Jumper:
    """Append one government-issued aviation medical to the jumper.

    Mirrors :func:`add_membership_to_jumper`. v0.1's MedicalKind has
    only ``class_iii`` so the per-kind validation surface is small;
    higher classes can land additively.
    """
    del user_id

    jumper = _load_jumper(logbook_root, jumper_id)
    _assert_attachment_exists(jumper, payload.card_attachment_id)

    try:
        medical = Medical(id=uuid4(), **payload.model_dump())
    except ValidationError as exc:
        raise validation_failed_from_pydantic(
            exc, "medical validation failed"
        ) from exc

    updated = _persist_with_credentials_update(
        logbook_root, jumper, medicals=[*jumper.medicals, medical]
    )

    _logger.info(
        "jumper_medical_added",
        extra={
            "jumper_id": str(jumper_id),
            "medical_id": str(medical.id),
            "kind": medical.kind.value,
        },
    )
    return updated


@with_writer_lock
def update_medical_on_jumper(
    logbook_root: Path,
    user_id: str,
    jumper_id: UUID,
    medical_id: UUID,
    payload: MedicalCreate,
) -> Jumper:
    del user_id

    jumper = _load_jumper(logbook_root, jumper_id)

    if not any(m.id == medical_id for m in jumper.medicals):
        raise NotFoundError(
            f"medical {medical_id} not found on jumper {jumper_id}"
        )

    _assert_attachment_exists(jumper, payload.card_attachment_id)

    try:
        replaced = Medical(id=medical_id, **payload.model_dump())
    except ValidationError as exc:
        raise validation_failed_from_pydantic(
            exc, "medical validation failed"
        ) from exc

    new_list = [
        replaced if m.id == medical_id else m for m in jumper.medicals
    ]
    updated = _persist_with_credentials_update(
        logbook_root, jumper, medicals=new_list
    )

    _logger.info(
        "jumper_medical_updated",
        extra={
            "jumper_id": str(jumper_id),
            "medical_id": str(medical_id),
            "kind": replaced.kind.value,
        },
    )
    return updated


@with_writer_lock
def delete_medical_from_jumper(
    logbook_root: Path,
    user_id: str,
    jumper_id: UUID,
    medical_id: UUID,
) -> Jumper:
    del user_id

    jumper = _load_jumper(logbook_root, jumper_id)

    if not any(m.id == medical_id for m in jumper.medicals):
        raise NotFoundError(
            f"medical {medical_id} not found on jumper {jumper_id}"
        )

    new_list = [m for m in jumper.medicals if m.id != medical_id]
    updated = _persist_with_credentials_update(
        logbook_root, jumper, medicals=new_list
    )

    _logger.info(
        "jumper_medical_deleted",
        extra={
            "jumper_id": str(jumper_id),
            "medical_id": str(medical_id),
        },
    )
    return updated
