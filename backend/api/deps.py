"""Shared FastAPI dependency functions (D7).

Dependencies fetched by route handlers via ``Depends(...)``. Keeping them
in one module means tests can override via
``app.dependency_overrides[get_logbook_root] = lambda: tmp_path`` without
juggling imports from a handful of places.

Why not pass ``logbook_root`` into ``create_app`` and stash it on
``app.state``? That works too, but makes tests rely on global app state
instead of the dependency system FastAPI already provides. The
dependency-injection pattern keeps routes testable in isolation and
matches FastAPI's documented testing story.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import Depends

from ..config import Settings, load_settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the app's ``Settings`` instance, memoized per process.

    ``load_settings()`` reads from env vars and the D28 TOML file; it
    is cheap but not free, so we cache the result. Tests that need a
    different Settings instance override this dependency directly
    (``app.dependency_overrides[get_settings] = lambda: ...``), which
    bypasses the cache.
    """
    return load_settings()


def get_logbook_root(settings: Settings = Depends(get_settings)) -> Path:
    """Resolve the configured logbook root (D20) for the current request.

    Overridden in tests to point at a ``tmp_path`` without touching
    the user's real config.
    """
    return settings.logbook_root


def get_user_id() -> str:
    """The D8 user_id for the current request.

    v0.1 single-user: always ``"default"``. When auth lands, this
    dependency reads the authenticated principal from the request
    context; the route-handler signature stays the same.
    """
    return "default"
