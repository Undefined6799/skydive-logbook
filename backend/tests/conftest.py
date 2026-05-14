"""Shared fixtures."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

from backend.models.jump import Attachment, Jump


@pytest.fixture
def logbook_root(tmp_path: Path) -> Iterator[Path]:
    """A freshly empty logbook root. Each test gets its own tmp dir."""
    root = tmp_path / "logbook"
    root.mkdir()
    yield root


@pytest.fixture
def sample_jump() -> Jump:
    """A fully-populated Jump that exercises every optional field."""
    return Jump(
        id=uuid4(),
        jump_number=851,
        date=date(2026, 4, 22),
        time=None,
        timezone=None,
        dropzone="Skydive Elsinore",
        aircraft="Twin Otter",
        discipline="FS-4",
        exit_altitude_m=4000,
        deployment_altitude_m=900,
        freefall_time_s=55,
        notes="First 4-way of the season. Funnel on exit, recovered.",
        attachments=[
            Attachment(
                filename="flysight.csv",
                sha256="a" * 64,
                size=12345,
                content_type="text/csv",
            )
        ],
    )
