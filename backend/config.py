"""Application configuration (D20).

App-level config (which logbook folder to open, REST bind) lives in the OS
user config dir as ``config.toml``. Per-logbook preferences (units, jumper
name) live inside the logbook folder as settings.xml — that file is loaded
by the service layer, not here.

Source precedence, highest to lowest:

1. ``init`` kwargs — ``Settings(bind_port=9000)`` passed from code.
2. Environment variables — ``SKYDIVE_`` prefixed, e.g. ``SKYDIVE_API_KEY``.
   Env wins over the file so secrets can stay out of a plaintext config.
3. ``user_config_dir()/config.toml`` — the D20 TOML file.
4. Defaults declared on the model.

Example ``config.toml``::

    logbook_root = "~/SkydiveLogbook"
    bind_host = "127.0.0.1"
    bind_port = 8765
    log_level = "INFO"

Missing file: fine, defaults + env cover everything. Malformed file:
``tomllib.TOMLDecodeError`` propagates — silent fallback to defaults
would hide a typo that could point the app at the wrong logbook.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


def user_config_dir() -> Path:
    """OS-appropriate directory for app config (D20)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "skydive-logbook"


def config_file_path() -> Path:
    """Return the TOML config file location per D20.

    Declared as a free function (not a ``Settings`` attribute) so tests
    can ``monkeypatch.setattr("backend.config.user_config_dir", ...)``
    and have the new location take effect on the next ``Settings()`` call.
    """
    return user_config_dir() / "config.toml"


class Settings(BaseSettings):
    """App-level settings. Per-logbook prefs live in the logbook folder."""
    model_config = SettingsConfigDict(env_prefix="SKYDIVE_", extra="ignore")

    # Path to the logbook folder. `~` is expanded on load.
    logbook_root: Path = Field(default_factory=lambda: Path.home() / "SkydiveLogbook")

    # REST binding. Default loopback only — see D48 for the v0.1 posture
    # (single-user, loopback-only, no auth surface). When LAN exposure or
    # multi-user lands, the successor D-entry re-introduces an auth
    # configuration field together with the middleware that enforces it.
    bind_host: str = Field(default="127.0.0.1")
    bind_port: int = Field(default=8765, ge=1, le=65535)

    # Root log level (D27). Case-insensitive; ``configure_logging`` uppers
    # it. Valid values match stdlib logging: DEBUG, INFO, WARNING, ERROR,
    # CRITICAL. Env: SKYDIVE_LOG_LEVEL.
    log_level: str = Field(default="INFO")

    # Update-check repository, formatted as ``"owner/repo"`` against the
    # GitHub Releases API. D14 defers *automatic* updates (the app
    # silently replacing its own binary), but a user-initiated
    # "Check for updates" button is in scope — it surfaces version
    # information and opens the release page; no binary replacement.
    # ``None`` (the default) disables the feature: the endpoint returns
    # 503 ``update_check_disabled`` and the UI is expected to hide the
    # button. Env: ``SKYDIVE_UPDATE_CHECK_REPO``.
    update_check_repo: str | None = Field(default=None)

    # Per-request total body cap (bytes). The middleware rejects
    # requests whose ``Content-Length`` (or streamed body) exceeds
    # this with 413 problem+json + ``code=payload_too_large`` per
    # Slice 10. The total includes multipart boundaries and form
    # fields, not just attachment payloads — set high enough to
    # cover a plausible multi-video jump's full upload. 5 GiB is
    # the v0.1 default; a user with a Bigway-sized post-jump
    # turnaround can raise it via env / TOML.
    #
    # Env: ``SKYDIVE_MAX_REQUEST_BYTES``.
    max_request_bytes: int = Field(default=5 * 1024 * 1024 * 1024, ge=1)

    # Per-file cap (bytes). Enforced inside the upload chunk loop
    # in ``backend/api/jumps.py:_upload_chunks`` (and the matching
    # constant in ``backend/api/jumpers.py`` for credential cards).
    # A single attachment over this size raises
    # ``PayloadTooLargeError`` mid-stream; the partial tmp file is
    # cleaned up by ``atomic_write_stream``'s context-manager
    # rollback. 2 GiB matches the largest practical single skydive
    # video file in v0.1.
    #
    # Env: ``SKYDIVE_MAX_FILE_BYTES``.
    max_file_bytes: int = Field(default=2 * 1024 * 1024 * 1024, ge=1)

    # CORS allow-list. Defaults to the Vite dev origins (npm run
    # dev serves the SPA at :5173 against the FastAPI :8000
    # backend in dev mode); the packaged pywebview build serves
    # both from the same origin so CORS doesn't apply.
    #
    # A user who runs the frontend dev server on a non-standard
    # port, or who exposes the API to a known LAN host they trust,
    # overrides this via ``SKYDIVE_CORS_ALLOWED_ORIGINS`` (comma-
    # separated). Empty list disables CORS entirely (safe for
    # same-origin deployments).
    #
    # Env: ``SKYDIVE_CORS_ALLOWED_ORIGINS`` (comma-separated).
    cors_allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )

    # Whether unhandled-exception responses include the exception
    # type and message in the 500 problem+json body. The full
    # traceback always goes to the structured log via ``exc_info``
    # regardless; this flag controls only the wire-visible ``detail``
    # field.
    #
    # ``None`` (default) means "auto" — the model_validator below
    # resolves it to ``True`` when ``bind_host`` is loopback, ``False``
    # otherwise. A v0.1 desktop user on 127.0.0.1 sees useful error
    # detail in modals; any non-loopback bind (D48 LAN exposure, future
    # server mode) defaults safe: a generic message on the wire, full
    # detail only in the log. The user can override either way by
    # setting the flag explicitly.
    #
    # Env: ``SKYDIVE_EXPOSE_INTERNAL_ERRORS``.
    expose_internal_errors: bool | None = Field(default=None)

    # Hosts that are unambiguously "this machine, not the network."
    # Used by the auto-resolve for ``expose_internal_errors`` and by
    # ``main.py`` for the non-loopback boot warning. Kept as a class-
    # level constant so the two consumers share the same set.
    _LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

    @model_validator(mode="after")
    def _resolve_expose_internal_errors(self) -> Settings:
        """Auto-resolve ``expose_internal_errors`` from ``bind_host``.

        Auto rule: on loopback, default ``True`` (desktop UX); on any
        non-loopback bind, default ``False`` (no exception detail
        leaks onto the network). An explicit value via env / TOML /
        init overrides the auto rule.
        """
        if self.expose_internal_errors is None:
            self.expose_internal_errors = self.bind_host in self._LOOPBACK_HOSTS
        return self

    def is_loopback_bind(self) -> bool:
        """Whether ``bind_host`` is a loopback address."""
        return self.bind_host in self._LOOPBACK_HOSTS

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Layer the TOML source between env and defaults (D20).

        Order of the returned tuple is priority, first == highest. We
        drop ``dotenv_settings`` — a .env file in the app directory is
        not part of D20's contract and would add a fourth surface with
        unclear ordering rules. Keeping init/env/toml/secrets is both
        smaller and closer to what D20 describes.
        """
        return (
            init_settings,
            env_settings,
            TomlConfigSettingsSource(settings_cls, toml_file=config_file_path()),
            file_secret_settings,
        )


def load_settings(**overrides: Any) -> Settings:
    """Build ``Settings`` from defaults + TOML + env + any kwarg overrides.

    The ``overrides`` keyword arguments map directly to ``Settings`` fields
    and take highest precedence — useful for tests and for the CLI-arg
    slice that will land later (e.g. ``--port 9000``).

    ``logbook_root`` is expanded (``~``) after construction so the value
    the rest of the app sees is always an absolute path, regardless of
    whether it was declared in TOML, env, or a default.
    """
    s = Settings(**overrides)
    s.logbook_root = s.logbook_root.expanduser()
    return s
