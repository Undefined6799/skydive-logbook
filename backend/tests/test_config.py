"""Tests for D20 config loading.

The contract under test:

  * ``user_config_dir()/config.toml`` is an additional source for
    ``Settings``.
  * Precedence, highest to lowest: init kwargs > env > TOML > defaults.
  * A missing file is normal (defaults + env win).
  * A malformed file fails loudly — silent fallback to defaults would
    let a typo point the app at the wrong logbook.
  * Unknown keys are ignored (``extra="ignore"``) so an older binary
    reading a newer file does not crash.
"""
from __future__ import annotations

import tomllib

import pytest

from backend import config as config_module
from backend.config import Settings, load_settings

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def isolated_config_dir(tmp_path, monkeypatch):
    """Point ``user_config_dir()`` at a throw-away directory.

    Monkeypatching the module-level function lets every ``Settings()``
    constructed during the test resolve ``config_file_path()`` to the
    temp dir. Nothing is created up front — tests that want a file
    write it explicitly.
    """
    monkeypatch.setattr(config_module, "user_config_dir", lambda: tmp_path)
    # Also clear any SKYDIVE_ env vars the host might have set, so the
    # env source doesn't shadow what each test is trying to prove.
    for key in list(os_environ_keys()):
        if key.startswith("SKYDIVE_"):
            monkeypatch.delenv(key, raising=False)
    return tmp_path


def os_environ_keys():
    """Imported lazily so pytest can stub ``os.environ`` in a fixture."""
    import os
    return os.environ.keys()


def write_toml(dir_path, body: str):
    """Write ``body`` to ``config.toml`` inside ``dir_path``."""
    (dir_path / "config.toml").write_text(body, encoding="utf-8")


# --------------------------------------------------------------------------- #
# user_config_dir + config_file_path
# --------------------------------------------------------------------------- #

class TestConfigFilePath:
    def test_config_file_path_is_inside_user_config_dir(self, isolated_config_dir):
        # The function is an indirection point tests rely on; changing
        # its behaviour is a breaking change for the fixture pattern.
        assert config_module.config_file_path() == isolated_config_dir / "config.toml"


# --------------------------------------------------------------------------- #
# Defaults + missing file
# --------------------------------------------------------------------------- #

class TestMissingFile:
    def test_settings_use_defaults_when_file_absent(self, isolated_config_dir):
        # File is deliberately not created.
        s = Settings()
        assert s.bind_host == "127.0.0.1"
        assert s.bind_port == 8765
        assert s.log_level == "INFO"

    def test_empty_file_behaves_like_missing_file(self, isolated_config_dir):
        write_toml(isolated_config_dir, "")
        s = Settings()
        assert s.bind_port == 8765


# --------------------------------------------------------------------------- #
# TOML-sourced values
# --------------------------------------------------------------------------- #

class TestTomlOverridesDefaults:
    def test_single_value_from_toml(self, isolated_config_dir):
        write_toml(isolated_config_dir, 'bind_port = 9000\n')
        s = Settings()
        assert s.bind_port == 9000
        # Unrelated fields stay at default.
        assert s.bind_host == "127.0.0.1"

    def test_multiple_values(self, isolated_config_dir):
        write_toml(
            isolated_config_dir,
            'bind_host = "0.0.0.0"\n'
            'bind_port = 9001\n'
            'log_level = "DEBUG"\n',
        )
        s = Settings()
        assert s.bind_host == "0.0.0.0"
        assert s.bind_port == 9001
        assert s.log_level == "DEBUG"

    def test_logbook_root_as_string_coerces_to_path(self, isolated_config_dir, tmp_path):
        target = tmp_path / "my-logbook"
        # ``as_posix()`` so the path written into TOML uses forward
        # slashes — Windows backslashes in a TOML string trigger
        # invalid-escape errors (``\U`` looks like a Unicode escape
        # to TOML's lexer). Pydantic + pathlib still parse the
        # POSIX-style path correctly on Windows.
        write_toml(isolated_config_dir, f'logbook_root = "{target.as_posix()}"\n')
        s = Settings()
        # Pydantic coerces the TOML string into a Path via the field annotation.
        assert s.logbook_root == target

    def test_unknown_toml_field_is_ignored(self, isolated_config_dir):
        # Per D48: a pre-D48 config.toml carrying ``api_key`` (or any
        # other unknown field) must not cause a startup error — the
        # Settings model is configured ``extra="ignore"`` so the value
        # is silently dropped. Pinning this prevents a future
        # accidental ``extra="forbid"`` flip from breaking existing
        # users on upgrade.
        write_toml(
            isolated_config_dir,
            'bind_port = 9000\napi_key = "left-over-from-pre-d48"\n',
        )
        s = Settings()
        assert s.bind_port == 9000
        assert not hasattr(s, "api_key")


# --------------------------------------------------------------------------- #
# Precedence
# --------------------------------------------------------------------------- #

class TestPrecedence:
    def test_env_overrides_toml(self, isolated_config_dir, monkeypatch):
        # env > file is the load-bearing precedence rule: it lets the
        # operator override a stale value in config.toml for a single run
        # without editing the file, and keeps secrets out of plaintext
        # disk storage if desired.
        write_toml(isolated_config_dir, 'bind_port = 9000\nlog_level = "DEBUG"\n')
        monkeypatch.setenv("SKYDIVE_BIND_PORT", "9999")
        s = Settings()
        assert s.bind_port == 9999
        # Field not overridden in env still comes from TOML.
        assert s.log_level == "DEBUG"

    def test_init_kwargs_override_env_and_toml(self, isolated_config_dir, monkeypatch):
        # Construction-time kwargs beat everything else — useful for
        # tests and for the future CLI-arg slice (--port 9000).
        write_toml(isolated_config_dir, 'bind_port = 9000\n')
        monkeypatch.setenv("SKYDIVE_BIND_PORT", "9001")
        s = Settings(bind_port=9002)
        assert s.bind_port == 9002


# --------------------------------------------------------------------------- #
# Error paths
# --------------------------------------------------------------------------- #

class TestMalformedFile:
    def test_syntax_error_raises(self, isolated_config_dir):
        # A typo in the file must not silently fall back to defaults —
        # the operator who edited it needs to see the failure.
        write_toml(isolated_config_dir, 'bind_port = [invalid\n')
        with pytest.raises(tomllib.TOMLDecodeError):
            Settings()

    def test_wrong_type_for_field_raises_validation_error(self, isolated_config_dir):
        # Not specifically D20-driven, but documents the consequence: if
        # a file says bind_port = "nine thousand", the user gets a
        # pydantic ValidationError at startup, not at first request.
        import pydantic
        write_toml(isolated_config_dir, 'bind_port = "nine-thousand"\n')
        with pytest.raises(pydantic.ValidationError):
            Settings()

    def test_port_out_of_range_raises(self, isolated_config_dir):
        # The Field(ge=1, le=65535) constraint still applies.
        import pydantic
        write_toml(isolated_config_dir, 'bind_port = 0\n')
        with pytest.raises(pydantic.ValidationError):
            Settings()


# --------------------------------------------------------------------------- #
# Forward-compatibility: unknown keys
# --------------------------------------------------------------------------- #

class TestUnknownKeys:
    def test_unknown_toml_key_is_ignored(self, isolated_config_dir):
        # An older binary reading a newer config file (or a user's typo)
        # should not crash. ``extra="ignore"`` handles this. If this
        # test flips to "fails", we've tightened the contract; requires
        # a D-entry revision (D20 or a superseder).
        write_toml(
            isolated_config_dir,
            'bind_port = 9000\nfuture_knob = "some-value-from-a-later-version"\n',
        )
        s = Settings()
        assert s.bind_port == 9000
        assert not hasattr(s, "future_knob")


# --------------------------------------------------------------------------- #
# load_settings helper
# --------------------------------------------------------------------------- #

class TestLoadSettings:
    def test_expands_tilde_in_logbook_root(self, isolated_config_dir, monkeypatch, tmp_path):
        # ``~`` in the file should be expanded to an absolute path
        # *after* construction, so the rest of the app never has to
        # worry about it. Point HOME at tmp_path so the expansion is
        # deterministic across test hosts. On Windows ``Path.expanduser``
        # consults ``USERPROFILE`` instead of ``HOME``; setting both
        # keeps the test platform-portable.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        write_toml(isolated_config_dir, 'logbook_root = "~/MyLogbook"\n')
        s = load_settings()
        assert s.logbook_root == tmp_path / "MyLogbook"
        # The path string no longer contains '~'.
        assert "~" not in str(s.logbook_root)

    def test_overrides_kwarg_wins(self, isolated_config_dir, monkeypatch, tmp_path):
        # load_settings(**overrides) passes through to Settings(**overrides),
        # so the init-kwarg precedence rule still holds.
        write_toml(isolated_config_dir, 'bind_port = 9000\n')
        monkeypatch.setenv("SKYDIVE_BIND_PORT", "9001")
        s = load_settings(bind_port=9002)
        assert s.bind_port == 9002
