# pyright: basic
"""Single-command desktop launcher with first-run folder picker.

Boots one process that does everything:

  1. Ensures the React frontend is built (``frontend/dist/``). Runs
     ``npm install`` + ``npm run build`` automatically when needed.
  2. Opens a welcome pywebview window.
  3. On first run (no config.toml), pops a native folder picker for
     the logbook root, writes config.toml, and bootstraps the folder.
  4. Starts uvicorn on a daemon thread, serving the FastAPI backend
     AND the built static frontend at http://localhost:8000.
  5. Redirects the welcome window to the running backend.

The same pywebview window persists across the welcome → main app
transition. Closing the window stops everything.

Exposes a small JS API (``window.pywebview.api``) so Settings inside
the React app can pop the same native folder picker, write a new
config.toml, and request a process restart without leaving the app.

This module imports ``webview`` (pywebview) — an optional ``[desktop]``
extra that ships no type stubs. The file-level ``# pyright: basic``
pragma above downgrades this single file to basic-mode type-checking
so the unknown* family stops cascading from every ``webview.X``
attribute access. Real type bugs (argument-type, optional access,
operator) still fire under basic.
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import uvicorn

# Late path setup so this module can also be imported from anywhere
# (tests, REPL) without first-class concerns about cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.config import config_file_path, load_settings  # noqa: E402

logger = logging.getLogger(__name__)


_BACKEND_HOST = "127.0.0.1"
_BACKEND_PORT = 8000
_BACKEND_HEALTH_URL = f"http://{_BACKEND_HOST}:{_BACKEND_PORT}/api/v1/health"
# Browsers see 127.0.0.1 and localhost as different origins. Open the
# pywebview window with `localhost` so the page origin matches the
# api.js fetch target — same-origin, no CORS preflight.
_APP_URL = f"http://localhost:{_BACKEND_PORT}/"


# Per D65 the launcher's job narrows to "pick a folder, run bootstrap,
# hand off to the SPA". The personal greeting that used to live here
# ("Good morning, Alex") leaked from a dev session and is wrong for
# every other installer; the SPA now owns the first-run welcome
# experience via the onboarding wizard, which can be themed alongside
# the rest of the app and reuse its component library.
_WELCOME_HTML = """\
<!doctype html>
<html lang=en>
<head>
<meta charset=utf-8>
<title>Skydive Logbook</title>
<style>
  html, body { margin: 0; padding: 0; height: 100%;
    background: #0b0d0f; color: #e7e7e8;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    -webkit-font-smoothing: antialiased; }
  .wrap { display: flex; align-items: center; justify-content: center;
    height: 100%; flex-direction: column; gap: 14px; }
  .label { font-size: 10px; letter-spacing: 0.3em; color: #737373;
    text-transform: uppercase; font-weight: 500; }
  .title { font-size: 26px; font-weight: 500; letter-spacing: -0.02em; }
  .status { font-size: 12px; color: #737373; font-family: ui-monospace, monospace; }
  .dot { display: inline-block; width: 7px; height: 7px;
    border-radius: 50%; background: #34d399;
    box-shadow: 0 0 12px rgba(52,211,153,0.55);
    animation: pulse 1.4s ease-in-out infinite; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
</style>
</head>
<body>
  <div class=wrap>
    <div class=label>Skydive Logbook</div>
    <div class=title>Starting up</div>
    <div class=status><span class=dot></span> &nbsp; <span id=msg>Loading\u2026</span></div>
  </div>
</body>
</html>
"""


def _is_frozen() -> bool:
    """Running inside a PyInstaller bundle (.app on macOS, .exe on
    Windows, AppImage on Linux). PyInstaller sets ``sys.frozen`` and
    ``sys._MEIPASS`` (the directory the bundle's data files were
    extracted to / are mapped from)."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def _project_root() -> Path:
    """Return the directory holding ``frontend/``, ``backend/``, etc.

    In dev mode that's the repo root (``Path(__file__) / .. / .. / ..``).
    In a packaged build it's PyInstaller's ``sys._MEIPASS`` — the
    extracted-resources directory that holds whatever the spec's
    ``datas`` block put there. The spec maps ``frontend/dist`` →
    ``frontend/dist`` inside the bundle, so resolving relative to
    ``_MEIPASS`` finds the same paths the dev-mode layout uses."""
    if _is_frozen():
        # ``sys._MEIPASS`` exists only in PyInstaller bundles; the
        # ``_is_frozen`` guard above gates the access. The pragma
        # documents both that fact and the reason pyright would
        # otherwise flag it.
        return Path(sys._MEIPASS)  # pyright: ignore[reportAttributeAccessIssue]  # PyInstaller-only attr
    return Path(__file__).resolve().parent.parent.parent


def _looks_like_vite_build(dist: Path) -> bool:
    index = dist / "index.html"
    if not index.is_file():
        return False
    try:
        head = index.read_text(encoding="utf-8", errors="ignore")[:2048]
    except OSError:
        return False
    return "/assets/index-" in head and 'type="module"' in head


def _sources_newer_than_dist(frontend_dir: Path) -> bool:
    """Return True if any file under ``src/`` (or top-level configs) is
    newer than ``dist/index.html``. This is a coarse "did the developer
    edit code since the last build" check — small enough to walk in
    well under a second on a typical project tree, accurate enough to
    avoid serving stale UI after a frontend edit.
    """
    dist_index = frontend_dir / "dist" / "index.html"
    if not dist_index.is_file():
        return True
    dist_mtime = dist_index.stat().st_mtime

    candidates = [
        frontend_dir / "package.json",
        frontend_dir / "vite.config.js",
        frontend_dir / "tailwind.config.js",
        frontend_dir / "postcss.config.js",
        frontend_dir / "index.html",
    ]
    for c in candidates:
        if c.is_file() and c.stat().st_mtime > dist_mtime:
            return True

    src = frontend_dir / "src"
    if src.is_dir():
        for path in src.rglob("*"):
            if path.is_file() and path.stat().st_mtime > dist_mtime:
                return True

    return False


def _ensure_frontend_built(frontend_dir: Path) -> bool:
    dist = frontend_dir / "dist"
    # In a packaged build the frontend was built at package time and
    # bundled into the .app / .exe / AppImage. There is no source
    # tree to rebuild from \u2014 and the bundle directory is read-only on
    # most install paths (e.g. ``/Applications`` on macOS), so an
    # ``npm install`` would fail anyway. Trust the bundled dist and
    # bail with a clear message if it's missing (which would mean a
    # broken build artifact).
    if _is_frozen():
        if _looks_like_vite_build(dist):
            return True
        sys.stderr.write(
            f"Packaged build is missing the bundled frontend at {dist}. "
            "This is a build-time bug \u2014 rebuild the .app with the "
            "frontend already built (frontend/dist/) and re-bundle.\n"
        )
        return False
    if _looks_like_vite_build(dist) and not _sources_newer_than_dist(frontend_dir):
        return True
    if dist.is_dir() and any(dist.iterdir()):
        if _looks_like_vite_build(dist):
            print("Source files newer than last build; rebuilding\u2026", flush=True)
        else:
            print("dist/ contains stale content; clearing and rebuilding\u2026", flush=True)
        shutil.rmtree(dist)

    npm = shutil.which("npm")
    if not npm:
        sys.stderr.write(
            "npm not found on PATH. Install Node.js from https://nodejs.org "
            "(LTS is fine) and re-run.\n"
        )
        return False

    node_modules = frontend_dir / "node_modules"
    if not node_modules.exists():
        print("Installing frontend dependencies (one-time, ~80 MB)\u2026", flush=True)
        if subprocess.run([npm, "install"], cwd=frontend_dir).returncode != 0:
            sys.stderr.write("`npm install` failed.\n")
            return False

    print("Building frontend (one-time, ~10\u201320 seconds)\u2026", flush=True)
    if subprocess.run([npm, "run", "build"], cwd=frontend_dir).returncode != 0:
        sys.stderr.write("`npm run build` failed.\n")
        return False

    if not (dist.is_dir() and any(dist.iterdir())):
        sys.stderr.write("Build completed but dist/ is empty.\n")
        return False
    return True


def _wait_for_backend(timeout_s: float = 15.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(_BACKEND_HEALTH_URL, timeout=0.5) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(0.1)
    return False


def _run_uvicorn() -> None:
    # Pass the app object directly rather than the ``"backend.api.rest:app"``
    # string. uvicorn's string-form import goes through
    # ``importlib.import_module`` which resolves names through ``sys.path``
    # — fine in a normal install but unreliable inside a PyInstaller
    # bundle, where the module table is structured differently and the
    # bundled ``backend`` package isn't always discoverable by the same
    # import path. Importing here means PyInstaller catches the
    # dependency at build time and the runtime lookup is a no-op.
    from backend.api.rest import app as _app
    config = uvicorn.Config(
        _app,
        host=_BACKEND_HOST,
        port=_BACKEND_PORT,
        log_level="info",
        reload=False,
    )
    uvicorn.Server(config).run()


def _open_path(path: Path) -> None:
    """Hand a path to the OS default handler.

    Cross-platform pattern, matches what file managers use internally:

    - macOS: ``open <path>`` — finder hands off via LaunchServices.
      Same command works for files (opens in default app) and folders
      (opens in Finder).
    - Windows: ``os.startfile`` — shell association lookup, opens the
      file in its registered handler.
    - Linux/BSD: ``xdg-open`` — desktop-environment-agnostic dispatcher.

    Best-effort: failures are swallowed (logged) rather than raised
    because UI buttons that "do nothing" are better than UI buttons
    that crash the modal. The user can always fall back to revealing
    the folder and opening manually.
    """
    p = str(path)
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", p])
        elif system == "Windows":
            os.startfile(p)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", p])
    except Exception as exc:  # pragma: no cover - OS-specific failure modes
        logger.warning("open_path failed for %s: %s", p, exc)


def _write_logbook_config(logbook_root: Path) -> None:
    """Persist the chosen logbook folder to user_config_dir/config.toml.

    The TOML is intentionally minimal — only logbook_root is set here.
    Future settings (units, theme, etc.) can be appended by the user
    directly or by a future settings UI. The launcher only owns the
    one field it needs to honor.
    """
    cfg = config_file_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    # POSIX form on disk so the same config works on macOS and Linux.
    # On Windows, Path.as_posix produces forward slashes which TOML
    # also accepts unambiguously (no escaping required).
    cfg.write_text(f'logbook_root = "{logbook_root.as_posix()}"\n', encoding="utf-8")


class JsApi:
    """Methods callable from React via ``window.pywebview.api.*``.

    pywebview exposes every method on this object as an async function
    in JS. Methods receive any args from JS and return a value back
    (Promise resolves to it). Exceptions become rejected Promises with
    the original message — let exceptions raise; don't swallow them.
    """

    def __init__(self) -> None:
        self._pending_restart_root: Path | None = None

    def current_logbook_folder(self) -> str:
        """Return the currently configured logbook folder as a string."""
        return str(load_settings().logbook_root)

    def change_logbook_folder(self) -> dict:
        """Pop a native folder picker and stage the choice.

        Writes config.toml immediately so the next launch picks up the
        new folder. Returns ``{path, requires_restart}`` so the React
        side can show a restart prompt.
        """
        import webview  # pyright: ignore[reportMissingImports]  # late import — pywebview is the optional [desktop] extra
        if not webview.windows:
            return {"path": None, "requires_restart": False}
        # pywebview 6.2 deprecated the module-level ``FOLDER_DIALOG``
        # constant in favour of the ``FileDialog`` enum. The runtime
        # value is unchanged (int 20); the new symbol just survives
        # the eventual removal.
        result = webview.windows[0].create_file_dialog(
            webview.FileDialog.FOLDER,
            allow_multiple=False,
        )
        if not result:
            return {"path": None, "requires_restart": False}
        new_root = Path(result[0]).expanduser().resolve()
        _write_logbook_config(new_root)
        self._pending_restart_root = new_root
        return {"path": str(new_root), "requires_restart": True}

    def restart_app(self) -> None:
        """Re-exec the desktop launcher in place.

        ``os.execv`` replaces the current process image — the running
        uvicorn thread dies with the parent, the new process re-reads
        config.toml and boots into the freshly-chosen logbook root.
        """
        argv = [sys.executable, "-m", "backend.scripts.launch_desktop"]
        os.execv(sys.executable, argv)

    def reveal_logbook_root(self) -> dict:
        """Open the configured logbook folder in the OS file manager."""
        root = load_settings().logbook_root
        _open_path(root)
        return {"ok": True, "path": str(root)}

    def reveal_logs_folder(self) -> dict:
        """Open the app's logs directory in the OS file manager.

        Lives at ``user_config_dir() / 'logs'`` per D27 and D20 — the
        app-config dir, NOT inside the logbook folder (logs are
        operational artifacts, not user data; D2 says the logbook
        folder stays self-describing).

        Returns ``{ok: False, error: ...}`` if the directory doesn't
        exist (no logs were ever written, e.g. ``configure_logging``
        was called with ``file_sink=False``). The Settings UI surfaces
        the error inline rather than opening a missing folder.
        """
        from backend.observability.logging import log_dir
        target = log_dir()
        if not target.is_dir():
            return {
                "ok": False,
                "error": (
                    f"logs folder does not exist at {target} — "
                    "no log file has been written this session"
                ),
            }
        _open_path(target)
        return {"ok": True, "path": str(target)}

    def reveal_config_file(self) -> dict:
        """Open the app's ``config.toml`` (D20) in the OS file manager.

        Reveals the *folder* containing the file (so the file is
        visible / selectable) rather than opening it directly — the
        contents may be edited but most users just want to see where
        it lives. Falls back to revealing the parent config dir if
        the file hasn't been written yet."""
        from backend.config import config_file_path, user_config_dir
        target = config_file_path()
        if target.is_file():
            _open_path(target.parent)
            return {"ok": True, "path": str(target)}
        config_dir = user_config_dir()
        if not config_dir.is_dir():
            return {
                "ok": False,
                "error": (
                    f"no config file at {target}; "
                    "the app writes one on first save"
                ),
            }
        _open_path(config_dir)
        return {"ok": True, "path": str(config_dir)}

    def reveal_jump_folder(self, jump_id: str) -> dict:
        """Open the jump's folder in the OS file manager.

        Looks the folder up by jump id in the SQLite index. The
        launcher process already has access to the same index uvicorn
        serves — there's no race here because the index is opened,
        read, and closed in this single function call.
        """
        folder = self._resolve_jump_folder(jump_id)
        if folder is None:
            return {"ok": False, "error": "jump not found"}
        _open_path(folder)
        return {"ok": True, "path": str(folder)}

    def open_jump_attachment(self, jump_id: str, filename: str) -> dict:
        """Open a file inside the jump's folder with the OS default app.

        Both tracked attachments and untracked files (dropped in via
        the file manager) are valid — the launcher only checks that
        the resolved path is a real file inside the jump folder.
        """
        folder = self._resolve_jump_folder(jump_id)
        if folder is None:
            return {"ok": False, "error": "jump not found"}

        # Sanitize the filename so a malformed JS argument cannot
        # escape the folder (e.g. ``../etc/passwd``). The
        # ``sanitize_filename`` primitive enforces D4's character set
        # and rejects any path separator.
        from backend.storage.filesystem import sanitize_filename
        try:
            safe = sanitize_filename(filename)
        except ValueError as exc:
            return {"ok": False, "error": f"invalid filename: {exc}"}

        target = folder / safe
        # ``Path.is_file`` returns False for symlinks pointing outside
        # the folder; combined with sanitize_filename rejecting
        # separators, the open is firmly scoped to the jump folder.
        if not target.is_file():
            return {"ok": False, "error": "file not in folder"}

        _open_path(target)
        return {"ok": True, "path": str(target)}

    def _resolve_jump_folder(self, jump_id: str) -> Path | None:
        """Look a jump up by id in the SQLite index, return its folder.

        Returns ``None`` for an unknown id (wrong user, deleted, never
        existed) — callers surface that as a structured error to JS.
        """
        from backend.storage.index import open_index
        settings = load_settings()
        result = open_index(settings.logbook_root)
        try:
            row = result.conn.execute(
                "SELECT folder FROM jumps WHERE id = ? AND user_id = ?",
                (jump_id, "default"),
            ).fetchone()
        finally:
            result.conn.close()
        if row is None:
            return None
        return settings.logbook_root / row["folder"]


def _setup_first_run(window) -> Path:
    """Resolve the logbook folder on launch.

    If config.toml exists, return its logbook_root unchanged.
    Otherwise pop a folder picker; on accept, write config and return
    the chosen path; on cancel, fall back to the default and write
    config so the next launch is silent.
    """
    cfg = config_file_path()
    if cfg.exists():
        return load_settings().logbook_root.expanduser().resolve()

    # Suggest a default location next to the user's home folder. The
    # picker opens at home so the user can either accept the default
    # or navigate elsewhere.
    default = Path.home() / "SkydiveLogbook"
    window.evaluate_js(
        "document.getElementById('msg').textContent = 'Choose your logbook folder\u2026';"
    )
    import webview  # pyright: ignore[reportMissingImports]  # pywebview is the optional [desktop] extra
    # pywebview 6.2 deprecated ``FOLDER_DIALOG``; ``FileDialog.FOLDER``
    # is the surviving symbol — same runtime int 20.
    chosen = window.create_file_dialog(
        webview.FileDialog.FOLDER,
        directory=str(Path.home()),
        allow_multiple=False,
    )
    root = Path(chosen[0]) if chosen else default
    root = root.expanduser().resolve()
    _write_logbook_config(root)
    return root


def _set_status(window, msg: str) -> None:
    """Update the welcome window's status line, escaping for safe JS."""
    safe = msg.replace("\\", "\\\\").replace("'", "\u2019").replace("\n", " ")
    window.evaluate_js(
        f"document.getElementById('msg').textContent = '{safe}';"
    )


def _on_window_ready(*_args) -> None:
    """Runs once the welcome window is shown.

    pywebview ``webview.start(func)`` calls ``func`` with zero args by
    default; older versions / examples sometimes pass the window. Accept
    varargs to be robust either way and pull the window from the live
    ``webview.windows`` list, which is the documented public surface for
    grabbing the active window from inside a start callback.

    Owns the full first-run flow plus the handoff to the running
    backend. Mirrors ``backend.main.main``'s startup ordering so the
    logbook is fully provisioned before uvicorn accepts requests:
    folder picker → mkdir + bootstrap (copies SCHEMA.v1.xsd, README,
    subdirectories) → open_index (D26 schema check) → uvicorn. Skipping
    bootstrap leaves the folder without the XSD that the manifest
    writer needs, which surfaces as a 500 on the first POST.

    All exceptions are caught and surfaced into the welcome window's
    status line so a misconfigured launch doesn't leave a stuck blank
    window.
    """
    import webview  # pyright: ignore[reportMissingImports]  # pywebview is the optional [desktop] extra
    if not webview.windows:
        return
    window = webview.windows[0]
    try:
        # Step 1: resolve / pick the logbook folder. config.toml gets
        # written here so subsequent restarts skip the picker.
        root = _setup_first_run(window)
        root.mkdir(parents=True, exist_ok=True)

        # Step 2: D29 bootstrap. Copies the app-shipped XSDs (SCHEMA.v1.xsd
        # and any future versions), writes LOGBOOK_README if missing, and
        # creates jumps/, dropzones/, .trash/. Idempotent — safe on every
        # launch. This step is what was missing before; without it,
        # manifest writes fail with `OSError: SCHEMA.v1.xsd not found`.
        _set_status(window, "Setting up logbook\u2026")
        from backend.storage.bootstrap import bootstrap_logbook
        try:
            bootstrap_logbook(root)
        except OSError as exc:
            _set_status(
                window,
                f"Cannot set up logbook at {root}: {exc}",
            )
            return

        # Step 3: D9 single-instance lock. backend.main.main() acquires
        # this same lock to prevent two app processes from operating on
        # the same logbook (which would race on jump.xml writes, the
        # SHA256SUMS regen, and SQLite). Hold the lock for the lifetime
        # of this Python process — pywebview's window close exits the
        # process, which releases the lock via filelock's atexit hook.
        from backend.storage.lockfile import LockError, acquire
        try:
            acquire(root)
        except LockError as exc:
            _set_status(
                window,
                f"Another logbook instance is already running: {exc}",
            )
            return

        # Step 4: D26 index check. open_index applies the drop-and-
        # reindex flow if PRAGMA user_version disagrees with the current
        # INDEX_SCHEMA_VERSION. The connection is closed immediately —
        # services manage their own connections per-request.
        from backend.storage.index import open_index
        schema_was_rebuilt = False
        try:
            result = open_index(root)
            try:
                schema_was_rebuilt = result.schema_was_rebuilt
            finally:
                result.conn.close()
        except OSError as exc:
            _set_status(window, f"Cannot open index: {exc}")
            return

        # Step 4b: D26 prescribes "if schema_was_rebuilt, run reindex
        # synchronously." Without this, every existing jump folder
        # vanishes from the list endpoint until the user manually
        # clicks Reindex — bad first impression after an app upgrade.
        # We log the result and continue; a duplicate-jump-number
        # abort is recoverable (operator can rename a folder and
        # rerun reindex from Settings) so we don't block startup.
        if schema_was_rebuilt:
            _set_status(window, "Rebuilding index from XML\u2026")
            from backend.services.reindex_service import reindex_from_xml
            try:
                report = reindex_from_xml(root)
                if report.aborted:
                    logger.error(
                        "auto_reindex_aborted: %s", report.aborted
                    )
                else:
                    logger.info(
                        "auto_reindex done: indexed=%d skipped=%d",
                        report.jumps_indexed, len(report.skipped),
                    )
            except Exception as exc:
                logger.exception("auto_reindex failed: %s", exc)

        # Step 5: start uvicorn now that the folder is fully provisioned.
        _set_status(window, "Starting backend\u2026")
        threading.Thread(
            target=_run_uvicorn, daemon=True, name="uvicorn"
        ).start()

        if not _wait_for_backend():
            _set_status(
                window,
                "Backend did not start within 15s. Check the terminal log.",
            )
            return

        # Hand off to the running React app.
        window.load_url(_APP_URL)
    except Exception as exc:  # pragma: no cover - top-level safety net
        logging.exception("launch failed")
        _set_status(window, f"Error: {exc}")


def main() -> int:
    try:
        import webview  # type: ignore[import-not-found]
    except ImportError:
        sys.stderr.write(
            "pywebview is not installed. Install with `uv pip install -e \".[desktop]\"` "
            "or `pip install pywebview`.\n"
        )
        return 1

    # Wire the D27 JSON formatter + rotating file sink. The desktop
    # launcher owns startup in packaged mode (backend.main.main is
    # NOT called — uvicorn boots from a thread inside this process),
    # so without this call the .app would have no log file at all,
    # only stderr that's invisible to a Finder-launched binary.
    # ``file_sink=True`` writes to ``user_config_dir() / 'logs' /
    # 'skydive-logbook.log'`` (D20 / D27).
    from backend.observability.logging import configure_logging
    configure_logging(level="INFO", file_sink=True)

    project_root = _project_root()
    frontend_dir = project_root / "frontend"

    if not frontend_dir.is_dir():
        sys.stderr.write(
            f"Expected frontend/ at {frontend_dir}, but it doesn't exist.\n"
        )
        return 1

    if not _ensure_frontend_built(frontend_dir):
        return 1

    api = JsApi()
    webview.create_window(
        title="Skydive Logbook",
        html=_WELCOME_HTML,
        width=1280,
        height=820,
        min_size=(960, 640),
        resizable=True,
        js_api=api,
    )
    # ``debug=True`` enables the WebView devtools (right-click → Inspect
    # on macOS WKWebView) — useful in dev for inspecting network calls,
    # console errors, and React rendering. In a packaged build (.app /
    # .exe / AppImage) the devtools are off-putting on every launch and
    # the file-sink log is the right debugging surface for users; the
    # ``_is_frozen`` gate flips them off there. To force devtools on in
    # a packaged build for a one-off debug session, set the env var
    # ``SKYDIVE_DEVTOOLS=1`` before launching.
    debug = (not _is_frozen()) or os.environ.get("SKYDIVE_DEVTOOLS") == "1"
    webview.start(_on_window_ready, debug=debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
