# pyright: basic
"""Smoke tests for ``backend.scripts.launch_desktop`` (TEST-3, audit 2026-04-29).

The desktop launcher is the riskiest cross-platform surface in the
project — 605 lines, pywebview integration, frontend build detection,
process orchestration. Full coverage isn't the goal; the goal is that
**broken imports / typos surface in CI** and **the happy-path config
write works**. The audit explicitly framed it that way:

  > Goal isn't full coverage — it's "broken imports / typos surface
  > in CI" + "happy-path config write works."

The pywebview window is never opened in these tests. Functions that
call into ``webview`` are exercised only by import (they're lazily
imported inside ``JsApi`` methods, so importing the module does not
require pywebview to be installed in the test env).

The launcher carries a ``# pyright: basic`` pragma — same posture
applied here, since the test file pokes at internals (``JsApi``, the
helper builders) that the strict allow-list intentionally exempts
upstream.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest import mock

# Importing the module is itself part of the smoke test — broken
# imports / module-level typos fail at collection time.
from backend.scripts import launch_desktop


class TestImportSurface:
    """Failsafe: module imports cleanly without pywebview installed.

    pywebview is the optional ``[desktop]`` extra; the launcher imports
    it lazily inside ``JsApi.change_logbook_folder`` (and at the
    bottom of the file inside ``main``). Importing the module at the
    top of this test file is the load-bearing assertion: if a future
    refactor moves a ``webview`` import to module top, this test
    starts failing in test envs that don't install ``[desktop]``.
    """

    def test_module_loaded(self):
        # Trivially true — the import at the top would have raised.
        # The point is that the import happened; this test is the
        # checkbox that lives next to it.
        assert launch_desktop is not None

    def test_public_helpers_exist(self):
        # Sanity that the names the tests below depend on are still
        # there. A rename catches us before runtime.
        for name in (
            "_project_root",
            "_looks_like_vite_build",
            "_sources_newer_than_dist",
            "_ensure_frontend_built",
            "_write_logbook_config",
            "_run_uvicorn",
            "_wait_for_backend",
            "JsApi",
            "main",
        ):
            assert hasattr(launch_desktop, name), name


class TestProjectRoot:
    def test_resolves_to_project_root(self):
        # The launcher lives at backend/scripts/launch_desktop.py;
        # the project root is two parents up from that file. Pin
        # the relationship so a future relocation surfaces here.
        root = launch_desktop._project_root()
        assert (root / "backend").is_dir()
        assert (root / "pyproject.toml").is_file()


class TestLooksLikeViteBuild:
    """Pin the heuristic that distinguishes a real Vite build from a
    leftover skeleton. The launcher uses this to decide whether to
    rebuild — a false positive would serve stale UI; a false negative
    would rebuild needlessly.
    """

    def test_recognises_vite_marker(self, tmp_path: Path):
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text(
            '<!doctype html><html><head>'
            '<script type="module" src="/assets/index-abc123.js"></script>'
            '</head></html>',
            encoding="utf-8",
        )
        assert launch_desktop._looks_like_vite_build(dist) is True

    def test_rejects_missing_index(self, tmp_path: Path):
        dist = tmp_path / "dist"
        dist.mkdir()
        assert launch_desktop._looks_like_vite_build(dist) is False

    def test_rejects_non_vite_index(self, tmp_path: Path):
        dist = tmp_path / "dist"
        dist.mkdir()
        # An index.html that isn't a Vite build (e.g. a stray
        # placeholder) — the marker strings are missing.
        (dist / "index.html").write_text(
            "<!doctype html><html><body>Hello</body></html>",
            encoding="utf-8",
        )
        assert launch_desktop._looks_like_vite_build(dist) is False


class TestSourcesNewerThanDist:
    """The launcher uses mtimes to decide whether a rebuild is needed.
    Two regressions to guard against:
      1. Returning False when sources are obviously newer (would serve
         stale UI on every boot after a frontend edit).
      2. Returning True spuriously (would rebuild every launch).
    """

    def _stub_frontend(self, tmp_path: Path) -> Path:
        """Build a minimal frontend tree with a pre-existing dist."""
        frontend = tmp_path / "frontend"
        (frontend / "src").mkdir(parents=True)
        (frontend / "dist").mkdir()
        (frontend / "dist" / "index.html").write_text("dist", encoding="utf-8")
        (frontend / "src" / "App.jsx").write_text("// app", encoding="utf-8")
        # Ensure source mtime is older than dist by setting both
        # explicitly — relying on natural ordering is flaky on fast
        # filesystems.
        old = (frontend / "dist" / "index.html").stat().st_mtime - 60
        import os
        os.utime(frontend / "src" / "App.jsx", (old, old))
        return frontend

    def test_returns_false_when_dist_is_fresh(self, tmp_path: Path):
        frontend = self._stub_frontend(tmp_path)
        assert launch_desktop._sources_newer_than_dist(frontend) is False

    def test_returns_true_when_dist_missing(self, tmp_path: Path):
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        # No dist/ at all → must rebuild.
        assert launch_desktop._sources_newer_than_dist(frontend) is True

    def test_returns_true_when_src_newer_than_dist(self, tmp_path: Path):
        frontend = self._stub_frontend(tmp_path)
        # Touch src to be after dist's mtime.
        future = (frontend / "dist" / "index.html").stat().st_mtime + 60
        import os
        os.utime(frontend / "src" / "App.jsx", (future, future))
        assert launch_desktop._sources_newer_than_dist(frontend) is True


class TestWriteLogbookConfig:
    """Happy-path: the launcher's only persistent side effect is writing
    config.toml. The folder picker on first-run depends on this; the
    Settings → Change Folder action depends on this.
    """

    def test_writes_logbook_root(self, tmp_path: Path, monkeypatch):
        cfg = tmp_path / "config.toml"
        # Redirect the launcher's idea of where config.toml lives.
        monkeypatch.setattr(
            launch_desktop, "config_file_path", lambda: cfg
        )
        target = tmp_path / "MyLogbook"
        launch_desktop._write_logbook_config(target)
        assert cfg.is_file()
        text = cfg.read_text(encoding="utf-8")
        # POSIX form on disk per the docstring — no escaping issues
        # across OSes.
        assert f'logbook_root = "{target.as_posix()}"' in text

    def test_creates_parent_dir(self, tmp_path: Path, monkeypatch):
        # config.toml's parent (the user_config_dir) may not exist
        # yet on a fresh machine — _write_logbook_config mkdirs it.
        cfg = tmp_path / "deep" / "nested" / "config.toml"
        monkeypatch.setattr(
            launch_desktop, "config_file_path", lambda: cfg
        )
        launch_desktop._write_logbook_config(tmp_path / "MyLogbook")
        assert cfg.parent.is_dir()
        assert cfg.is_file()

    def test_overwrites_existing(self, tmp_path: Path, monkeypatch):
        # Pre-existing config.toml gets fully replaced — this is the
        # Settings → Change Folder behaviour.
        cfg = tmp_path / "config.toml"
        cfg.write_text('logbook_root = "/old"\n', encoding="utf-8")
        monkeypatch.setattr(
            launch_desktop, "config_file_path", lambda: cfg
        )
        launch_desktop._write_logbook_config(tmp_path / "NewRoot")
        text = cfg.read_text(encoding="utf-8")
        assert "/old" not in text
        assert "NewRoot" in text


class TestEnsureFrontendBuiltShortCircuit:
    """The full build path requires npm + a real frontend tree; testing
    the subprocess flow end-to-end is outside this smoke's scope. What
    we DO pin: the early-return branches (already-built, npm missing).
    """

    def test_short_circuits_when_dist_is_fresh(self, tmp_path: Path):
        frontend = tmp_path / "frontend"
        (frontend / "dist").mkdir(parents=True)
        # Vite-marker index so _looks_like_vite_build returns True.
        (frontend / "dist" / "index.html").write_text(
            '<!doctype html><html><head>'
            '<script type="module" src="/assets/index-abc.js"></script>'
            '</head></html>',
            encoding="utf-8",
        )
        # No src/ → _sources_newer_than_dist returns False (no
        # candidates to compare). Already-built path returns True
        # without invoking npm.
        result = launch_desktop._ensure_frontend_built(frontend)
        assert result is True

    def test_returns_false_when_npm_missing(
        self, tmp_path: Path, monkeypatch
    ):
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        # No dist/ → must rebuild → reaches npm check.
        # shutil.which("npm") == None when npm is not on PATH.
        monkeypatch.setattr("shutil.which", lambda _: None)
        # Capture stderr so the test's failure message is silent.
        monkeypatch.setattr(sys, "stderr", io.StringIO())
        result = launch_desktop._ensure_frontend_built(frontend)
        assert result is False


class TestJsApi:
    """JsApi methods callable from React. The webview-using methods
    (change_logbook_folder, reveal_logbook_root) are exercised only
    indirectly — what we pin is the non-webview path:
    current_logbook_folder.
    """

    def test_current_logbook_folder_returns_string(
        self, tmp_path: Path, monkeypatch
    ):
        # Steer load_settings at a known dir.
        monkeypatch.setenv("SKYDIVE_LOGBOOK_ROOT", str(tmp_path / "lb"))
        api = launch_desktop.JsApi()
        result = api.current_logbook_folder()
        assert isinstance(result, str)
        # The launcher resolves through load_settings, which expands
        # ``~`` and applies the env override.
        assert "lb" in result

    def test_restart_app_invokes_execv(self, monkeypatch):
        # Don't actually exec — capture the call. The body of
        # ``restart_app`` shells back into the launcher; the real
        # invocation kills this Python process.
        seen: dict[str, object] = {}

        def fake_execv(path, argv):
            seen["path"] = path
            seen["argv"] = list(argv)
            # Return without raising — pretend exec happened. In
            # production execv replaces the process and never
            # returns.

        monkeypatch.setattr("os.execv", fake_execv)
        api = launch_desktop.JsApi()
        api.restart_app()
        assert seen["path"] == sys.executable
        assert seen["argv"] == [
            sys.executable, "-m", "backend.scripts.launch_desktop"
        ]


class TestWaitForBackend:
    """The launcher polls the health endpoint to know when uvicorn is
    ready to receive the window's redirect. The timeout is the only
    failure mode worth pinning here — the success path requires a
    real uvicorn, which is out of smoke scope.
    """

    def test_returns_false_when_backend_never_starts(self, monkeypatch):
        # Force urlopen to always fail — _wait_for_backend should
        # spin until timeout and return False.
        from urllib.error import URLError

        def always_fails(*_a, **_kw):
            raise URLError("nope")

        monkeypatch.setattr(
            "urllib.request.urlopen", always_fails
        )
        # Tight timeout so the test runs in well under a second.
        assert launch_desktop._wait_for_backend(timeout_s=0.3) is False

    def test_returns_true_when_backend_is_up(self, monkeypatch):
        # Stub urlopen to return a 200 immediately.
        class FakeResp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        monkeypatch.setattr(
            "urllib.request.urlopen", lambda *_a, **_kw: FakeResp()
        )
        assert launch_desktop._wait_for_backend(timeout_s=2.0) is True


class TestMainShortCircuit:
    """``main()`` drives the whole launcher. We test the short-circuit
    where ``_ensure_frontend_built`` returns False — main() returns 1
    without ever opening a window. The full path opens a pywebview
    window, which we cannot exercise headless in CI.
    """

    def test_main_returns_1_when_frontend_build_fails(self, monkeypatch):
        # Stub _ensure_frontend_built to fail. The next operation
        # in main is webview window creation — which we shouldn't
        # reach.
        monkeypatch.setattr(
            launch_desktop, "_ensure_frontend_built", lambda _frontend: False
        )

        # Also stub configure_logging — it has the side effect of
        # installing a JSON formatter on the root logger that other
        # tests don't expect.
        monkeypatch.setattr(
            "backend.observability.logging.configure_logging",
            lambda _level: None,
        )

        # Sentinel: if main reaches webview.start, blow up loudly.
        def _should_not_be_called(*_a, **_kw):
            raise AssertionError(
                "main() reached webview after frontend build failed"
            )

        # webview is imported inside main; pre-install a fake module
        # so any accidental call into it raises immediately.
        fake_webview = mock.MagicMock()
        fake_webview.start = _should_not_be_called
        fake_webview.create_window = _should_not_be_called
        monkeypatch.setitem(sys.modules, "webview", fake_webview)

        rc = launch_desktop.main()
        assert rc == 1


# --------------------------------------------------------------------------- #
# INFRA-6: first-run folder picker (audit 2026-04-29)
# --------------------------------------------------------------------------- #

class TestFirstRunFolderPicker:
    """``_setup_first_run`` is the seam between "no config.toml exists"
    and "logbook ready to bootstrap". The flow:

      - if config.toml exists → return its logbook_root unchanged.
      - else → pop a folder picker. On accept, write config.toml
        with the chosen path and return it. On cancel, fall back to
        the default and STILL write config.toml (so the next launch
        is silent).

    Both branches end with ``_write_logbook_config`` called once. The
    next step in ``_on_window_ready`` is ``bootstrap_logbook(root)``
    which is exercised end-to-end by the storage tests; this class
    only pins the picker dispatch + config-write contract.
    """

    def _make_window_stub(
        self, picker_returns: list[str] | None
    ):
        """A minimal pywebview Window stub.

        ``create_file_dialog`` returns whatever the test specified;
        ``evaluate_js`` is a no-op (the launcher uses it only to
        update the welcome status string).
        """
        window = mock.MagicMock()
        window.create_file_dialog.return_value = picker_returns
        return window

    def test_existing_config_skips_picker(
        self, tmp_path: Path, monkeypatch
    ):
        # When config.toml exists, _setup_first_run returns the
        # configured logbook_root WITHOUT touching the picker. Pin
        # this so a future regression that re-pops the picker is
        # caught (a UX disaster — the user would see the picker on
        # every launch).
        cfg = tmp_path / "config.toml"
        existing_root = tmp_path / "MyExistingLogbook"
        existing_root.mkdir()
        cfg.write_text(
            f'logbook_root = "{existing_root.as_posix()}"\n',
            encoding="utf-8",
        )
        # _setup_first_run resolves config_file_path twice — once
        # via its own ``cfg.exists()`` check (the launcher's import
        # of the symbol) and once inside ``load_settings`` via the
        # backend.config import. Patch both so the test hits the
        # same TOML.
        monkeypatch.setattr(
            launch_desktop, "config_file_path", lambda: cfg
        )
        import backend.config as _cfg_module
        monkeypatch.setattr(
            _cfg_module, "config_file_path", lambda: cfg
        )

        # Build a window stub that BLOWS UP if its picker is ever
        # called — that's the regression we're guarding against.
        window = mock.MagicMock()
        window.create_file_dialog.side_effect = AssertionError(
            "picker must not be invoked when config.toml already exists"
        )

        result = launch_desktop._setup_first_run(window)
        assert result == existing_root.resolve()
        # config.toml unchanged.
        assert cfg.read_text(encoding="utf-8").strip() == (
            f'logbook_root = "{existing_root.as_posix()}"'
        )

    def test_picker_accept_writes_config_with_chosen_path(
        self, tmp_path: Path, monkeypatch
    ):
        cfg = tmp_path / "config.toml"
        chosen = tmp_path / "ChosenLogbook"
        chosen.mkdir()
        monkeypatch.setattr(
            launch_desktop, "config_file_path", lambda: cfg
        )
        # Pre-install a fake webview module so the late
        # ``import webview`` inside _setup_first_run sees something.
        fake_webview = mock.MagicMock()
        fake_webview.FOLDER_DIALOG = 1  # the runtime int
        monkeypatch.setitem(sys.modules, "webview", fake_webview)

        window = self._make_window_stub(picker_returns=[str(chosen)])

        result = launch_desktop._setup_first_run(window)
        # Picker fired exactly once.
        window.create_file_dialog.assert_called_once()
        # config.toml written with the chosen path.
        text = cfg.read_text(encoding="utf-8")
        assert f'logbook_root = "{chosen.as_posix()}"' in text
        assert result == chosen.resolve()

    def test_picker_cancel_falls_back_to_default_and_persists(
        self, tmp_path: Path, monkeypatch
    ):
        # Cancel ≡ create_file_dialog returns None / empty. The
        # launcher writes config.toml ANYWAY with the default — so
        # the next launch is silent.
        cfg = tmp_path / "config.toml"
        monkeypatch.setattr(
            launch_desktop, "config_file_path", lambda: cfg
        )
        # Pretend home is a controlled tmp dir so the default
        # SkydiveLogbook lands somewhere we can predict.
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: fake_home))

        fake_webview = mock.MagicMock()
        fake_webview.FOLDER_DIALOG = 1
        monkeypatch.setitem(sys.modules, "webview", fake_webview)

        window = self._make_window_stub(picker_returns=None)

        result = launch_desktop._setup_first_run(window)
        expected = (fake_home / "SkydiveLogbook").resolve()
        assert result == expected
        # config.toml persisted with the default.
        assert cfg.is_file()
        text = cfg.read_text(encoding="utf-8")
        assert "SkydiveLogbook" in text

    def test_picker_called_with_folder_dialog_marker(
        self, tmp_path: Path, monkeypatch
    ):
        # Pin: the picker must be opened with
        # ``allow_multiple=False`` and the FOLDER_DIALOG marker.
        # A regression that flips to FILE_DIALOG would let users
        # pick a *file* as the logbook root — broken on the very
        # next launch.
        cfg = tmp_path / "config.toml"
        chosen = tmp_path / "Logbook"
        chosen.mkdir()
        monkeypatch.setattr(
            launch_desktop, "config_file_path", lambda: cfg
        )
        fake_webview = mock.MagicMock()
        fake_webview.FOLDER_DIALOG = "FOLDER"  # sentinel
        monkeypatch.setitem(sys.modules, "webview", fake_webview)

        window = self._make_window_stub(picker_returns=[str(chosen)])

        launch_desktop._setup_first_run(window)
        call = window.create_file_dialog.call_args
        # First positional arg is the dialog-type marker.
        assert call.args[0] == "FOLDER"
        # Multi-select must be off — choosing the logbook is a
        # single-select action.
        assert call.kwargs.get("allow_multiple") is False
