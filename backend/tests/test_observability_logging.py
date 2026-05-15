"""Unit tests for the D27 structured-logging contract.

These lock down the JSON Lines shape and the contextvar plumbing before
the middleware that drives them lands in slice C. If any of these fail,
the logging contract has drifted — either the change is intentional and
D27 wants a new entry superseding it, or there's a regression.
"""
from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from backend.observability.logging import (
    JsonFormatter,
    configure_logging,
    request_id_var,
)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _record(
    *,
    level: int = logging.INFO,
    name: str = "backend.test",
    msg: str = "hello",
    args: tuple = (),
    exc_info=None,
    **extra,
) -> logging.LogRecord:
    """Build a LogRecord the way ``Logger._log`` would.

    Using ``Logger.makeRecord`` rather than constructing directly means
    ``extra`` hits the same reserved-name guard real callers would see,
    and record.__dict__ looks exactly like it would in production.
    """
    logger = logging.getLogger(name)
    return logger.makeRecord(
        name=name,
        level=level,
        fn="/dev/null",
        lno=0,
        msg=msg,
        args=args,
        exc_info=exc_info,
        extra=extra or None,
    )


@pytest.fixture(autouse=True)
def _reset_contextvar():
    """Every test starts with ``request_id_var`` at its default (None)."""
    token = request_id_var.set(None)
    try:
        yield
    finally:
        request_id_var.reset(token)


# --------------------------------------------------------------------------- #
# JsonFormatter — reserved fields
# --------------------------------------------------------------------------- #

class TestReservedFields:
    def test_all_six_reserved_fields_present_when_no_exc(self):
        # Without exc_info, 'exception' is absent — the other five are
        # always emitted so downstream tooling can rely on their presence.
        out = JsonFormatter().format(_record(msg="hi"))
        body = json.loads(out)
        assert set(body) == {"timestamp", "level", "logger", "message", "request_id"}

    def test_timestamp_is_iso_utc_millis_with_z(self):
        # D27 pins this format so log and DB (D17) timestamps sort together.
        body = json.loads(JsonFormatter().format(_record()))
        ts = body["timestamp"]
        # 'YYYY-MM-DDTHH:MM:SS.sssZ' = 24 chars. Cheap structural assertion.
        assert len(ts) == 24
        assert ts.endswith("Z")
        assert ts[10] == "T"
        assert ts[-5] == "."  # ms delimiter

    def test_level_is_uppercase_name(self):
        body = json.loads(JsonFormatter().format(_record(level=logging.WARNING)))
        assert body["level"] == "WARNING"

    def test_logger_is_dotted_name(self):
        body = json.loads(JsonFormatter().format(_record(name="backend.api.rest")))
        assert body["logger"] == "backend.api.rest"

    def test_message_is_interpolated(self):
        # '%'-style interpolation is the stdlib default; the formatter must
        # call record.getMessage() to honour it rather than emit record.msg.
        body = json.loads(
            JsonFormatter().format(_record(msg="hello %s", args=("world",)))
        )
        assert body["message"] == "hello world"


# --------------------------------------------------------------------------- #
# JsonFormatter — request_id
# --------------------------------------------------------------------------- #

class TestRequestIdCorrelation:
    def test_null_when_no_context(self):
        # Emission outside a request (startup, CLI, test) surfaces as JSON
        # null so downstream queries can distinguish 'no context' from a
        # stringified 'None'.
        body = json.loads(JsonFormatter().format(_record()))
        assert body["request_id"] is None

    def test_string_when_contextvar_set(self):
        # UUID is serialized via str() rather than raw — both ends of the
        # correlation (X-Request-Id header, problem+json body) are strings,
        # so logs match.
        rid = uuid4()
        token = request_id_var.set(rid)
        try:
            body = json.loads(JsonFormatter().format(_record()))
        finally:
            request_id_var.reset(token)
        assert body["request_id"] == str(rid)
        # Round-trip through UUID to confirm it's a valid UUIDv4 shape.
        assert UUID(body["request_id"]).version == 4


# --------------------------------------------------------------------------- #
# JsonFormatter — exceptions
# --------------------------------------------------------------------------- #

class TestExceptionRendering:
    def test_exception_absent_when_exc_info_not_supplied(self):
        body = json.loads(JsonFormatter().format(_record()))
        assert "exception" not in body

    def test_exception_is_single_string_with_traceback(self):
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            exc_info = sys.exc_info()
        body = json.loads(JsonFormatter().format(_record(exc_info=exc_info)))
        assert isinstance(body["exception"], str)
        assert "ValueError: boom" in body["exception"]
        # Traceback header must be present — catches a regression where
        # someone swaps formatException() for str(exc).
        assert "Traceback" in body["exception"]

    def test_output_is_single_line_even_with_traceback(self):
        # D27: one JSON object per line. A multi-line traceback must be
        # escaped inside the JSON string value, not break the stream.
        try:
            raise RuntimeError("oops")
        except RuntimeError:
            import sys
            exc_info = sys.exc_info()
        out = JsonFormatter().format(_record(exc_info=exc_info))
        # Exactly one line: no newline inside the rendered JSON. The
        # StreamHandler later appends the terminating '\n'.
        assert "\n" not in out


# --------------------------------------------------------------------------- #
# JsonFormatter — extras
# --------------------------------------------------------------------------- #

class TestExtraHandling:
    def test_extras_pass_through_as_top_level(self):
        body = json.loads(
            JsonFormatter().format(_record(method="GET", status=200))
        )
        assert body["method"] == "GET"
        assert body["status"] == 200

    def test_collision_with_reserved_field_raises(self):
        # D27: collisions fail loudly. Attempting to pass 'timestamp' via
        # ``extra=`` is a server bug — a caller shadowing our output.
        # Note: we can't use ``extra={"logger": ...}`` or ``extra={"message": ...}``
        # because stdlib's ``makeRecord`` protects its own attribute names.
        # We use 'timestamp' — it's in our D27 reserved set but NOT in
        # stdlib's LogRecord attribute set, so a buggy filter could still
        # inject it. Simulate that here by setting it directly on the record.
        record = _record()
        record.timestamp = "fake-value"  # emulate a filter-injected collision
        with pytest.raises(ValueError, match="timestamp.*reserved D27 field"):
            JsonFormatter().format(record)

    def test_non_ascii_characters_preserved(self):
        # D4 normalizes on write; D27 doesn't touch text going out.
        # ``ensure_ascii=False`` keeps 'Sauté' readable instead of \u00e9.
        body_str = JsonFormatter().format(_record(jumper="Sauté"))
        assert "Sauté" in body_str
        body = json.loads(body_str)
        assert body["jumper"] == "Sauté"

    def test_non_json_types_fall_back_to_str(self):
        # A caller passing a Path, UUID, or Enum shouldn't crash the
        # formatter. ``default=str`` gives a deterministic fallback so log
        # emission never raises in production.
        # ``str(Path)`` produces backslash paths on Windows and forward
        # slashes on POSIX — assert against the OS-specific
        # serialization rather than baking ``/tmp/x`` into the test.
        from pathlib import Path
        path = Path("/tmp/x")
        out = JsonFormatter().format(_record(path=path))
        body = json.loads(out)
        assert body["path"] == str(path)


# --------------------------------------------------------------------------- #
# configure_logging
# --------------------------------------------------------------------------- #

class TestConfigureLogging:
    @pytest.fixture(autouse=True)
    def _restore_root(self):
        """Snapshot and restore root-logger state around each test."""
        root = logging.getLogger()
        saved_handlers = list(root.handlers)
        saved_level = root.level
        # Also snapshot uvicorn.access because configure_logging mutates it.
        access = logging.getLogger("uvicorn.access")
        saved_disabled = access.disabled
        try:
            yield
        finally:
            root.handlers = saved_handlers
            root.level = saved_level
            access.disabled = saved_disabled

    def test_installs_exactly_one_handler(self):
        # ``file_sink=False`` so the assertion is about the stderr handler
        # alone — the file-sink behaviour gets its own test below.
        configure_logging("INFO", file_sink=False)
        root = logging.getLogger()
        assert len(root.handlers) == 1

    def test_idempotent_across_repeat_calls(self):
        # Tests often exercise startup multiple times; duplicate handlers
        # would double every log line. ``configure_logging`` replaces
        # rather than stacks.
        configure_logging("INFO", file_sink=False)
        configure_logging("DEBUG", file_sink=False)
        root = logging.getLogger()
        assert len(root.handlers) == 1
        assert root.level == logging.DEBUG

    def test_handler_uses_json_formatter(self):
        configure_logging("INFO", file_sink=False)
        (handler,) = logging.getLogger().handlers
        assert isinstance(handler.formatter, JsonFormatter)


class TestFileSink:
    """The rotating file sink lands logs at
    ``user_config_dir() / 'logs' / 'skydive-logbook.log'`` so a
    Finder-launched .app has somewhere to write debug output. Tests
    monkeypatch ``user_config_dir`` to redirect at a tmp directory."""

    @pytest.fixture(autouse=True)
    def _restore_root(self):
        root = logging.getLogger()
        saved_handlers = list(root.handlers)
        saved_level = root.level
        access = logging.getLogger("uvicorn.access")
        saved_disabled = access.disabled
        try:
            yield
        finally:
            root.handlers = saved_handlers
            root.level = saved_level
            access.disabled = saved_disabled

    def test_file_sink_creates_log_file(self, monkeypatch, tmp_path):
        from backend.observability import logging as obs_logging

        monkeypatch.setattr(
            obs_logging, "user_config_dir", lambda: tmp_path / "config"
        )
        configure_logging("INFO", file_sink=True)
        logging.getLogger("test").info("hello")
        for h in logging.getLogger().handlers:
            h.flush()
        log_file = tmp_path / "config" / "logs" / "skydive-logbook.log"
        assert log_file.is_file()
        assert "hello" in log_file.read_text(encoding="utf-8")

    def test_file_sink_installs_a_second_handler(self, monkeypatch, tmp_path):
        from backend.observability import logging as obs_logging

        monkeypatch.setattr(
            obs_logging, "user_config_dir", lambda: tmp_path / "config"
        )
        configure_logging("INFO", file_sink=True)
        # stderr + rotating-file = 2 handlers.
        assert len(logging.getLogger().handlers) == 2

    def test_file_sink_disabled_returns_to_one_handler(self):
        configure_logging("INFO", file_sink=False)
        assert len(logging.getLogger().handlers) == 1

    def test_log_file_path_returns_active_path(self, monkeypatch, tmp_path):
        from backend.observability import logging as obs_logging

        monkeypatch.setattr(
            obs_logging, "user_config_dir", lambda: tmp_path / "config"
        )
        configure_logging("INFO", file_sink=True)
        from backend.observability.logging import log_file_path
        assert log_file_path() == tmp_path / "config" / "logs" / "skydive-logbook.log"

    def test_log_file_path_is_none_when_sink_off(self):
        configure_logging("INFO", file_sink=False)
        from backend.observability.logging import log_file_path
        assert log_file_path() is None

    def test_unwritable_log_dir_does_not_kill_startup(
        self, monkeypatch, tmp_path
    ):
        # Simulate a read-only / permission-denied logs directory: the
        # mkdir raises OSError. configure_logging must catch it,
        # surface a warning, and leave only the stderr handler.
        from backend.observability import logging as obs_logging

        bad_dir = tmp_path / "blocked"
        monkeypatch.setattr(obs_logging, "user_config_dir", lambda: bad_dir)
        original_mkdir = Path.mkdir

        def fake_mkdir(self, *a, **kw):
            if self == bad_dir / "logs":
                raise OSError("simulated read-only filesystem")
            return original_mkdir(self, *a, **kw)

        monkeypatch.setattr(Path, "mkdir", fake_mkdir)
        configure_logging("INFO", file_sink=True)
        # stderr handler still installed; file handler skipped.
        assert len(logging.getLogger().handlers) == 1
        from backend.observability.logging import log_file_path
        assert log_file_path() is None

    def test_emitted_records_are_valid_json_lines(self):
        # Swap the handler's stream for an in-memory buffer so we can
        # read back what configure_logging's formatter produced.
        # ``file_sink=False`` keeps this test focused on stderr handler
        # behaviour without writing a stray file under user_config_dir.
        configure_logging("INFO", file_sink=False)
        (handler,) = logging.getLogger().handlers
        buf = io.StringIO()
        handler.stream = buf

        logging.getLogger("backend.test").info("hi there")

        out = buf.getvalue().strip()
        # Exactly one line, parseable as JSON, carrying our contract fields.
        assert "\n" not in out
        body = json.loads(out)
        assert body["message"] == "hi there"
        assert body["logger"] == "backend.test"
        assert body["level"] == "INFO"
        assert body["request_id"] is None

    def test_silences_uvicorn_access(self):
        configure_logging("INFO", file_sink=False)
        assert logging.getLogger("uvicorn.access").disabled is True

    def test_leaves_uvicorn_error_propagating(self):
        # ``uvicorn.error`` is not silenced — it's the startup/shutdown
        # channel. Default stdlib state: no handlers, propagate=True. We
        # must not have flipped that.
        configure_logging("INFO", file_sink=False)
        err = logging.getLogger("uvicorn.error")
        assert err.disabled is False
        assert err.propagate is True
