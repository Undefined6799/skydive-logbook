# skydive-logbook PyInstaller spec — v0.1
#
# Audit 2026-04-29 INFRA-5 / INFRA-7. Builds a single binary per
# platform (macOS .app, Windows .exe, Linux ELF) that bundles:
#
#   - the FastAPI/uvicorn backend (entry point: backend.scripts.launch_desktop:main)
#   - the built React frontend (frontend/dist/)
#   - the app-shipped XSDs (backend/xml/schema/)
#   - the user-folder template (backend/storage/templates/LOGBOOK_README.md)
#
# Read by ``pyinstaller skydive-logbook.spec``. PyInstaller evaluates
# this file as Python — it is NOT a static config; the BLOCK_CIPHER,
# Analysis, PYZ, EXE, and platform-conditional COLLECT/BUNDLE objects
# are all real Python objects with documented APIs.
#
# Refs:
#   - PyInstaller spec files:
#     https://pyinstaller.org/en/stable/spec-files.html
#   - bundling data files:
#     https://pyinstaller.org/en/stable/spec-files.html#adding-data-files
#   - macOS .app BUNDLE:
#     https://pyinstaller.org/en/stable/spec-files.html#spec-file-options-for-a-mac-os-x-bundle
#
# Verification surface: the agent that wrote this can validate
# Python-syntax of the file and import-ability of the entry point;
# producing the actual binary requires running PyInstaller on each
# target OS. See docs/build.md for the per-platform
# build command and the verification gaps.

# ruff: noqa: F821, F405  # PyInstaller injects Analysis/PYZ/EXE/etc. at runtime
# pyright: ignore[reportUndefinedVariable]  # see ruff comment above

from pathlib import Path

# pyright: basic - PyInstaller injects globals; static analysis can't see them
# (Analysis, PYZ, EXE, COLLECT, BUNDLE are added by PyInstaller's spec eval).

import sys
import tomllib

# --- Project metadata ------------------------------------------------------

# Read version from pyproject.toml so the binary's version metadata
# matches what `pip install` would show. INFRA-7: pull version from
# the single source of truth.
PROJECT_ROOT = Path(SPECPATH).resolve()  # noqa: F821 - SPECPATH injected by PyInstaller
with open(PROJECT_ROOT / "pyproject.toml", "rb") as fh:
    _pyproject = tomllib.load(fh)
APP_VERSION = _pyproject["project"]["version"]
APP_NAME = "Skydive Logbook"
APP_BUNDLE_ID = "org.skydive_logbook.app"  # macOS bundle id

# Icon path (INFRA-7). PyInstaller wants a platform-native format:
#   - macOS:   .icns (use iconutil to convert from .iconset)
#   - Windows: .ico (Multi-size)
#   - Linux:   .png (used by AppImage tooling; PyInstaller itself
#              ignores the ``icon=`` arg on Linux)
# We ship a single SVG placeholder in build/icons/ and document the
# per-platform conversion in docs/build.md. If the platform-specific file
# is missing, fall back to None — the build will produce a plain
# default-icon binary rather than fail.
_icon_dir = PROJECT_ROOT / "build" / "icons"
if sys.platform == "darwin":
    _icon = _icon_dir / "skydive-logbook.icns"
elif sys.platform == "win32":
    _icon = _icon_dir / "skydive-logbook.ico"
else:
    _icon = _icon_dir / "skydive-logbook.png"
ICON_PATH = str(_icon) if _icon.is_file() else None

# --- Datas: XSDs, README template, frontend dist --------------------------

# D29 consequence: the app must ship its XSDs and README template
# inside the binary so first-run bootstrap can copy them into the
# user's logbook folder. Without these in ``datas``, the manifest
# writer hits OSError("SCHEMA.v1.xsd not found") on the very first
# write.
#
# datas tuples are (source_path, dest_dir_inside_bundle). The dest
# preserves the package layout so ``backend.xml.validator``'s
# ``SCHEMA_DIR = Path(__file__).parent / "schema"`` still resolves at
# runtime.
datas = [
    (
        str(PROJECT_ROOT / "backend" / "xml" / "schema"),
        "backend/xml/schema",
    ),
    (
        str(PROJECT_ROOT / "backend" / "storage" / "templates"),
        "backend/storage/templates",
    ),
    # The built React app — served by FastAPI from inside the bundle.
    # frontend/dist/ must exist before invoking pyinstaller; the
    # build command in docs/build.md runs `npm run build` first.
    (
        str(PROJECT_ROOT / "frontend" / "dist"),
        "frontend/dist",
    ),
]

# --- Hiddenimports: modules PyInstaller's static scan misses --------------

# uvicorn auto-loads its loop / http / lifespan implementations at
# runtime; PyInstaller's static analysis doesn't see them.
# pywebview's platform backend is similarly imported by string.
hiddenimports = [
    # uvicorn
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    # FastAPI / starlette / pydantic dynamic dispatch
    "email.mime.multipart",
    "email.mime.text",
    # Our XML pipeline ships only a single namespace today, but the
    # validator builds a path from the namespace at runtime.
    "backend.xml.serialize",
    "backend.xml.validator",
]

# --- Analysis -------------------------------------------------------------

a = Analysis(
    [str(PROJECT_ROOT / "backend" / "scripts" / "launch_desktop.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Test-only deps — never imported at runtime.
        "pytest",
        "_pytest",
        "ruff",
        "pyright",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="skydive-logbook",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX shrinks the binary but breaks macOS code signing.
    console=False,  # GUI app; set True briefly to debug startup.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,  # let PyInstaller pick host arch
    codesign_identity=None,  # see docs/build.md §Code Signing
    entitlements_file=None,
    icon=ICON_PATH,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="skydive-logbook",
)

# --- macOS .app BUNDLE -----------------------------------------------------

# PyInstaller produces a .app structure when BUNDLE is invoked. Only
# evaluate on Darwin so non-mac builds skip this block cleanly.
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=ICON_PATH,
        bundle_identifier=APP_BUNDLE_ID,
        version=APP_VERSION,
        info_plist={
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleIdentifier": APP_BUNDLE_ID,
            # Single-window GUI app; no document types.
            "LSApplicationCategoryType": "public.app-category.utilities",
            # The launcher writes config.toml in the user's
            # Application Support and reads/writes the user-chosen
            # logbook folder. macOS sandboxing entitlements are
            # documented in docs/build.md (we run unsandboxed for v0.1
            # because the user explicitly chooses the logbook root).
            "NSHighResolutionCapable": True,
            # Mojave+ requires this string for any app that calls
            # NSOpenPanel — the file dialog the launcher uses on
            # first-run.
            "NSDesktopFolderUsageDescription": (
                "Skydive Logbook needs access to choose your "
                "logbook folder."
            ),
            "NSDocumentsFolderUsageDescription": (
                "Skydive Logbook needs access to choose your "
                "logbook folder."
            ),
            "NSDownloadsFolderUsageDescription": (
                "Skydive Logbook needs access to choose your "
                "logbook folder."
            ),
        },
    )
