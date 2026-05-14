"""Pydantic model for a person — group members and packers (D54).

A Person is a first-class XML record under
``logbook_root/people/<uuid>.xml``. Jumps reference People by
UUID via ``<group_members>`` and ``<packed_by>`` (both D53). The
logbook owner is **not** a Person record — ``packed_by`` absent on a
jump is the canonical signal for "self-packed".

The shape is deliberately minimal: id, name, optional notes, audit
timestamps. The single registry serves both group-member and packer
contexts; no role tag distinguishes them. A friend who occasionally
packs for the user gets exactly one Person record.

Resolution is **soft** at the service layer: jump-side references
that don't resolve to an existing Person render as
``Unknown person <short-uuid>`` rather than as a validation error.
This keeps a hand-edited or half-imported logbook loadable; integrity
is a UI concern, not a data invariant. See D54 §Decision.

Field naming convention matches the rest of the schema (D2). Names
are NFC-normalized at the storage layer (D4) on every write.
"""
from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class Person(BaseModel):
    """Canonical person shape — serialized to ``people/<uuid>.xml``."""

    model_config = ConfigDict(extra="forbid")

    # Stable identifier. Never changes; the folder is the UUID so even
    # renaming via the UI never moves the file. Mirrors the Jumper
    # (D33) and Dropzone (D44) folder-naming conventions.
    id: UUID = Field(default_factory=uuid4)

    # Display name. Required, 1..120 chars, NFC-normalized at the
    # storage layer (D4). Mirrors JumpTitle / Dropzone.name caps so
    # listing UIs lay out predictably.
    name: str = Field(min_length=1, max_length=120)

    # Free-text notes. Optional. Used for short reminders ("packs at
    # Skydive City weekends", "rigger / friend / loft owner").
    notes: str | None = None

    # D32: audit timestamps in canonical UTC ms form, same posture
    # as Jump and Dropzone. Authored by the service layer; optional
    # on the model so a hand-crafted file still validates.
    created_at: str | None = None
    updated_at: str | None = None


class PersonCreate(BaseModel):
    """Request body for ``POST /api/v1/people`` (Phase 2c).

    Same field set as :class:`Person` minus the server-assigned
    ``id`` and the audit timestamps.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    notes: str | None = None


class PersonUpdate(BaseModel):
    """Request body for ``PUT /api/v1/people/{id}`` (Phase 2c).

    Full replace — every field must be supplied. ID is taken from
    the URL; audit timestamps are managed server-side.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    notes: str | None = None


class PersonSummary(BaseModel):
    """Compact projection for the people list / picker.

    Mirrors the columns cached on the SQLite ``people`` index table
    (Phase 2b) so list endpoints read at SQLite speed rather than
    walking ``people/*/person.xml`` per request (D3).
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
