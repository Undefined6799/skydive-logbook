"""Tests for ``backend.main.main`` — the startup sequence.

What these tests pin down:

  * Order of side-effects: bootstrap → open_index → reindex (if rebuilt)
    → uvicorn.run, under the D9 lock. If this order flips, the REST
    API could accept requests against a half-initialized folder or a
    stale/empty index.
  * Happy path: fresh run creates ``index.sqlite`` at the canonical
    path with ``PRAGMA user_version = INDEX_SCHEMA_VERSION``, emits an
    ``index_opened`` INFO record, and releases the lock on exit.
  * Rebuild path (D26): stale ``user_version`` triggers the drop-and-
    rebuild inside ``open_index``; main logs an ``index_schema_rebuilt``
    WARNING, runs ``reindex_from_xml`` synchronously, logs
    ``reindex_completed`` INFO, and proceeds to ``uvicorn.run``.
  * Reindex failure paths: a raise from ``reindex_from_xml`` or an
    ``aborted`` report (e.g. duplicate jump_number per D25) refuses
    startup with exit 1 and never invokes uvicorn.
  * Error paths: bootstrap / open_index / lock acquisition each fail
    cleanly with exit 1, leaving no partial state.

Two things the fixtures do to keep tests fast and isolated:

  * ``uvicorn.run`` is mocked so no port is bound.
  * ``configure_logging`` is patched to a no-op inside main, because
    the real ``configure_logging`` wipes the root logger's handler
    list on every call — which would detach pytest's ``caplog``
    handler and hide the records we want to assert on.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

import pytest

from backend import main as main_module
from backend.storage.index import INDEX_FILENAME, INDEX_SCHEMA_VERSION

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def mock_uvicorn(monkeypatch):
    """Replace ``uvicorn.run`` with a recorder so no port gets bound.

    ``main`` imports ``uvicorn`` lazily inside the function. We
    monkeypatch the module's ``run`` attribute; any ``import uvicorn``
    inside ``main`` returns the same module object, so the patch takes
    effect regardless of import order.
    """
    import uvicorn

    calls: list[dict] = []

    def _recorded_run(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(uvicorn, "run", _recorded_run)
    return calls


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch):
    """Point the logbook root at a throw-away folder; isolate config.

    Clears ``SKYDIVE_*`` env vars that may be set on the developer
    host and points ``user_config_dir`` at an empty temp path so the
    D28 TOML source does not read the host's real config.
    """
    from backend import config as config_module

    monkeypatch.setattr(
        config_module, "user_config_dir", lambda: tmp_path / "_config"
    )
    for key in list(os.environ):
        if key.startswith("SKYDIVE_"):
            monkeypatch.delenv(key, raising=False)

    root = tmp_path / "logbook"
    monkeypatch.setenv("SKYDIVE_LOGBOOK_ROOT", str(root))
    return root


@pytest.fixture
def no_configure_logging(monkeypatch):
    """Stub ``configure_logging`` inside ``main`` so caplog survives.

    The real ``configure_logging`` clears every handler from the root
    logger (D27) before installing its own JSON formatter. Pytest's
    ``caplog`` attaches a handler to the root logger; if we let
    ``configure_logging`` run, caplog gets wiped and records never
    reach the assertions. Stubbing it inside ``main`` leaves caplog
    untouched; the tests that exercise log output use caplog, and the
    tests that exercise configure_logging itself live in
    ``test_observability_logging``.
    """
    monkeypatch.setattr(main_module, "configure_logging", lambda level: None)


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #

class TestFreshStartup:
    def test_exits_zero(
        self, isolated_env, mock_uvicorn, no_configure_logging, caplog
    ):
        assert main_module.main() == 0

    def test_uvicorn_is_invoked_with_expected_wiring(
        self, isolated_env, mock_uvicorn, no_configure_logging, caplog
    ):
        main_module.main()
        assert len(mock_uvicorn) == 1
        call = mock_uvicorn[0]
        assert call["args"][0] == "backend.api.rest:app"
        # D27 knobs that keep our JSON formatter in place.
        assert call["kwargs"]["log_config"] is None
        assert call["kwargs"]["access_log"] is False

    def test_index_file_created_with_current_schema_version(
        self, isolated_env, mock_uvicorn, no_configure_logging, caplog
    ):
        main_module.main()
        index_path = isolated_env / INDEX_FILENAME
        assert index_path.is_file()
        # Peek at user_version via a fresh connection — main already
        # closed the one it opened.
        conn = sqlite3.connect(str(index_path))
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            assert version == INDEX_SCHEMA_VERSION
        finally:
            conn.close()

    def test_emits_index_opened_at_info(
        self, isolated_env, mock_uvicorn, no_configure_logging, caplog
    ):
        caplog.set_level(logging.INFO, logger="backend.main")
        main_module.main()
        records = [r for r in caplog.records if r.message == "index_opened"]
        assert len(records) == 1
        record = records[0]
        assert record.levelname == "INFO"
        assert record.name == "backend.main"
        assert record.previous_version == 0  # fresh DB
        assert record.current_version == INDEX_SCHEMA_VERSION

    def test_bootstrap_artifacts_present(
        self, isolated_env, mock_uvicorn, no_configure_logging, caplog
    ):
        # Regression: if the call order ever flips so open_index runs
        # before bootstrap, the XSDs would be missing. Pin the
        # post-startup shape.
        main_module.main()
        assert (isolated_env / "SCHEMA.v1.xsd").is_file()
        assert (isolated_env / "README.md").is_file()
        assert (isolated_env / "jumps").is_dir()


class TestReopenAtCurrentVersion:
    def test_second_run_opens_at_current_version_no_rebuild(
        self, isolated_env, mock_uvicorn, no_configure_logging, caplog
    ):
        caplog.set_level(logging.INFO, logger="backend.main")
        main_module.main()
        caplog.clear()
        assert main_module.main() == 0

        opened = [r for r in caplog.records if r.message == "index_opened"]
        assert len(opened) == 1
        assert opened[0].previous_version == INDEX_SCHEMA_VERSION

        rebuilt = [
            r for r in caplog.records if r.message == "index_schema_rebuilt"
        ]
        assert rebuilt == []


# --------------------------------------------------------------------------- #
# Rebuild path (D26)
# --------------------------------------------------------------------------- #

class TestSchemaRebuild:
    def test_stale_user_version_triggers_warning(
        self, isolated_env, mock_uvicorn, no_configure_logging, caplog
    ):
        # First run creates a clean index. Then forge a stale
        # user_version and run main again. Per D26, the rebuild branch
        # synchronously reindexes from XML and proceeds to uvicorn —
        # an empty rebuilt logbook reindexes to zero rows successfully.
        main_module.main()
        index_path = isolated_env / INDEX_FILENAME
        conn = sqlite3.connect(str(index_path))
        try:
            conn.execute("PRAGMA user_version = 99")
        finally:
            conn.close()

        caplog.set_level(logging.INFO, logger="backend.main")
        caplog.clear()
        # D26 successful rebuild + reindex returns 0 (the API is now
        # safe to serve — index matches XML, even if both are empty).
        assert main_module.main() == 0

        rebuilt = [
            r for r in caplog.records if r.message == "index_schema_rebuilt"
        ]
        assert len(rebuilt) == 1
        record = rebuilt[0]
        assert record.levelname == "WARNING"
        assert record.name == "backend.main"
        assert record.previous_version == 99
        assert record.current_version == INDEX_SCHEMA_VERSION

    def test_rebuild_runs_reindex_synchronously(
        self, isolated_env, mock_uvicorn, no_configure_logging, caplog
    ):
        # D26 §Mechanics: schema rebuild → reindex_from_xml is invoked
        # before uvicorn.run. The fresh-run-then-forge fixture has no
        # jumps on disk, so the reindex emits a clean report with
        # zero folders scanned. Pin both the call and the report log.
        main_module.main()
        conn = sqlite3.connect(str(isolated_env / INDEX_FILENAME))
        try:
            conn.execute("PRAGMA user_version = 99")
        finally:
            conn.close()

        caplog.set_level(logging.INFO, logger="backend.main")
        caplog.clear()
        # The setup main() call above already recorded one uvicorn
        # invocation; clear so the post-rebuild assertion below counts
        # only the rebuild run's invocation.
        mock_uvicorn.clear()
        assert main_module.main() == 0

        completed = [
            r for r in caplog.records if r.message == "reindex_completed"
        ]
        assert len(completed) == 1
        record = completed[0]
        assert record.levelname == "INFO"
        assert record.folders_scanned == 0
        assert record.jumps_indexed == 0
        assert record.skipped == 0

        # And uvicorn was reached afterwards (proves reindex did not
        # short-circuit startup on the empty-logbook happy path).
        assert len(mock_uvicorn) == 1

    def test_rebuild_does_not_emit_index_opened(
        self, isolated_env, mock_uvicorn, no_configure_logging, caplog
    ):
        # The WARNING and the INFO are mutually exclusive — a single
        # startup emits one or the other, never both.
        main_module.main()
        conn = sqlite3.connect(str(isolated_env / INDEX_FILENAME))
        try:
            conn.execute("PRAGMA user_version = 99")
        finally:
            conn.close()

        caplog.set_level(logging.INFO, logger="backend.main")
        caplog.clear()
        main_module.main()

        opened = [r for r in caplog.records if r.message == "index_opened"]
        assert opened == []

    def test_reindex_raise_refuses_startup(
        self,
        isolated_env,
        mock_uvicorn,
        no_configure_logging,
        capsys,
        monkeypatch,
    ):
        # Per D26: a raised exception from reindex_from_xml refuses
        # startup. uvicorn must not be invoked — serving the API
        # against an empty just-rebuilt index would silently hide
        # every existing jump.
        main_module.main()  # establish a clean index
        conn = sqlite3.connect(str(isolated_env / INDEX_FILENAME))
        try:
            conn.execute("PRAGMA user_version = 99")
        finally:
            conn.close()

        def _raises(root):
            raise RuntimeError("simulated: reindex disk error")

        monkeypatch.setattr(main_module, "reindex_from_xml", _raises)

        mock_uvicorn.clear()
        assert main_module.main() == 1
        assert mock_uvicorn == []
        captured = capsys.readouterr()
        assert "reindex failed" in captured.err
        assert "simulated" in captured.err

    def test_reindex_aborted_refuses_startup(
        self,
        isolated_env,
        mock_uvicorn,
        no_configure_logging,
        capsys,
        monkeypatch,
    ):
        # Per D25: a duplicate jump_number across two folders aborts
        # the reindex. ReindexReport.aborted carries the message;
        # main surfaces it on stderr and refuses to start.
        from backend.services.reindex_service import ReindexReport

        main_module.main()  # establish a clean index
        conn = sqlite3.connect(str(isolated_env / INDEX_FILENAME))
        try:
            conn.execute("PRAGMA user_version = 99")
        finally:
            conn.close()

        abort_message = "duplicate jump_number 42 for user 'default' across 2 folders: jumps/[42] a, jumps/[42] b"

        def _aborted(root):
            return ReindexReport(
                folders_scanned=2,
                jumps_indexed=0,
                aborted=abort_message,
            )

        monkeypatch.setattr(main_module, "reindex_from_xml", _aborted)

        mock_uvicorn.clear()
        assert main_module.main() == 1
        assert mock_uvicorn == []
        captured = capsys.readouterr()
        assert "reindex aborted" in captured.err
        # The full abort message — including the offending folders —
        # must reach the operator's stderr verbatim.
        assert "duplicate jump_number 42" in captured.err
        assert "jumps/[42] a" in captured.err
        assert "jumps/[42] b" in captured.err


# --------------------------------------------------------------------------- #
# Error paths
# --------------------------------------------------------------------------- #

class TestErrorPaths:
    def test_lock_failure_returns_one_and_does_not_run_uvicorn(
        self,
        isolated_env,
        mock_uvicorn,
        no_configure_logging,
        capsys,
    ):
        # Acquire the lock from this test, then call main() — it must
        # fail to acquire and exit 1. uvicorn must not be called.
        from backend.storage.lockfile import acquire

        isolated_env.mkdir(parents=True, exist_ok=True)
        held = acquire(isolated_env)
        try:
            assert main_module.main() == 1
            assert mock_uvicorn == []
            captured = capsys.readouterr()
            # The existing LockError branch prints via ``print(...,
            # file=sys.stderr)``. Regression-pin that a message lands.
            assert "error" in captured.err.lower()
        finally:
            held.release()

    def test_bootstrap_oserror_returns_one(
        self,
        isolated_env,
        mock_uvicorn,
        no_configure_logging,
        capsys,
        monkeypatch,
    ):
        # Simulate a disk failure during bootstrap. main catches
        # OSError and exits 1 cleanly.
        def _raises(root):
            raise PermissionError("simulated: /mnt is read-only")

        monkeypatch.setattr(main_module, "bootstrap_logbook", _raises)
        assert main_module.main() == 1
        assert mock_uvicorn == []
        captured = capsys.readouterr()
        assert "cannot set up logbook" in captured.err

    def test_open_index_oserror_returns_one(
        self,
        isolated_env,
        mock_uvicorn,
        no_configure_logging,
        capsys,
        monkeypatch,
    ):
        def _raises(root):
            raise PermissionError("simulated: index.sqlite not writable")

        monkeypatch.setattr(main_module, "open_index", _raises)
        assert main_module.main() == 1
        assert mock_uvicorn == []
        captured = capsys.readouterr()
        assert "cannot open index" in captured.err

    def test_lock_is_released_after_open_index_failure(
        self,
        isolated_env,
        mock_uvicorn,
        no_configure_logging,
        monkeypatch,
    ):
        # Regression: if open_index blows up, the finally block must
        # release the lock so a retry can proceed. We prove this by
        # running main twice in the same test — the second call would
        # exit 1 on LockError if the lock had leaked.
        from backend.storage.index import open_index as real_open_index

        call_count = {"n": 0}

        def _conditional_raise(root):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise PermissionError("simulated first-call failure")
            return real_open_index(root)

        monkeypatch.setattr(main_module, "open_index", _conditional_raise)

        # First call: synthetic failure.
        assert main_module.main() == 1
        # Second call: open_index succeeds, full startup completes.
        assert main_module.main() == 0
        assert len(mock_uvicorn) == 1
