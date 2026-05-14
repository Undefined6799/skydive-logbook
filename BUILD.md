# Building skydive-logbook for distribution

This document covers turning the source tree into a per-platform
binary using PyInstaller. v0.1 ships three artefacts:

- macOS: `Skydive Logbook.app` (Apple Silicon and Intel)
- Windows: `skydive-logbook.exe` + supporting files
- Linux: a directory tree under `dist/skydive-logbook/` (AppImage
  packaging is documented but the AppImage `.AppImage` itself is
  produced by `appimagetool` after PyInstaller — see §Linux below).

The PyInstaller spec (`skydive-logbook.spec` at the project root)
encodes everything that is the same across platforms — datas,
hidden imports, the entry point, and the macOS Info.plist. The
per-platform divergence lives in this document.

---

## Prerequisites (every platform)

1. Python 3.11 or newer (the project's runtime floor — D15).
2. `uv` for dependency installation.
3. Node.js LTS + `npm` for the React build.
4. PyInstaller 6.x (added to the project's `[desktop]` extras when
   the build pipeline lands).

```bash
uv sync --all-extras
uv pip install pyinstaller
(cd frontend && npm install && npm run build)
```

The frontend must be built BEFORE invoking PyInstaller — the spec
copies `frontend/dist/` into the bundle. A stale `dist/` produces a
binary that serves the wrong React app.

---

## Build commands

### macOS

```bash
# Apple Silicon native:
uv run pyinstaller skydive-logbook.spec --target-arch arm64 --clean

# Intel native:
uv run pyinstaller skydive-logbook.spec --target-arch x86_64 --clean

# Universal2 (combined):
uv run pyinstaller skydive-logbook.spec --target-arch universal2 --clean
```

Output: `dist/Skydive Logbook.app`. Drag to `/Applications`.

### Windows

```bash
uv run pyinstaller skydive-logbook.spec --clean
```

Output: `dist\skydive-logbook\skydive-logbook.exe` plus runtime
support files. Bundle into an installer with Inno Setup or NSIS
post-build (out of scope for v0.1).

### Linux

```bash
uv run pyinstaller skydive-logbook.spec --clean
```

Output: `dist/skydive-logbook/`. To produce an AppImage:

```bash
# Stage the binary with the AppDir layout AppImageTool expects:
mkdir -p AppDir/usr/bin
cp -r dist/skydive-logbook/* AppDir/usr/bin/
cp build/icons/skydive-logbook.png AppDir/skydive-logbook.png
cat > AppDir/skydive-logbook.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Skydive Logbook
Exec=skydive-logbook
Icon=skydive-logbook
Categories=Utility;
EOF
cat > AppDir/AppRun <<'EOF'
#!/bin/sh
HERE=$(dirname "$(readlink -f "$0")")
exec "$HERE/usr/bin/skydive-logbook" "$@"
EOF
chmod +x AppDir/AppRun
appimagetool AppDir Skydive_Logbook-x86_64.AppImage
```

`appimagetool` is from the AppImageKit project:
<https://github.com/AppImage/AppImageKit/releases>.

---

## Icon conversion

The spec looks for `build/icons/skydive-logbook.{icns,ico,png}`.
Convert from the SVG master before building.

### macOS — `.icns`

```bash
mkdir -p build/icons/skydive-logbook.iconset
for size in 16 32 64 128 256 512 1024; do
  rsvg-convert -w $size -h $size build/icons/skydive-logbook.svg \
    -o "build/icons/skydive-logbook.iconset/icon_${size}x${size}.png"
done
iconutil -c icns build/icons/skydive-logbook.iconset \
  -o build/icons/skydive-logbook.icns
```

`iconutil` ships with macOS. `rsvg-convert` is from `librsvg`
(`brew install librsvg`).

### Windows — `.ico`

```bash
magick convert -background none build/icons/skydive-logbook.svg \
  -define icon:auto-resize=16,32,48,64,128,256 \
  build/icons/skydive-logbook.ico
```

`magick` is ImageMagick 7. ImageMagick 6's `convert` works with the
same flags.

### Linux — `.png`

```bash
rsvg-convert -w 512 -h 512 build/icons/skydive-logbook.svg \
  -o build/icons/skydive-logbook.png
```

Linux users see the icon in the AppImage menu entry; PyInstaller
itself ignores `icon=` on Linux.

---

## Code signing — gatekeeping by platform

Code signing requires per-platform certificates that are NOT in
this repo. The PyInstaller spec exposes the hooks; the certs are
configured at build time via environment variables. See draft D52
in `DECISIONS.md` for the policy choice (ad-hoc signed for v0.1
self-distribution; full notarization deferred until commercial
signing certs are budgeted).

### macOS — Apple Developer ID

Required to avoid "skydive-logbook is damaged and can't be opened"
on Gatekeeper-protected machines.

```bash
# 1. Build unsigned per the section above.
# 2. Sign the .app:
codesign --deep --force --options runtime \
  --sign "Developer ID Application: <your name> (<team id>)" \
  "dist/Skydive Logbook.app"

# 3. Verify:
codesign --verify --deep --strict --verbose=2 "dist/Skydive Logbook.app"
spctl --assess --type execute --verbose "dist/Skydive Logbook.app"

# 4. Notarize (Apple's cloud-side signing for distribution outside
#    the App Store). Requires an app-specific password from
#    appleid.apple.com.
xcrun notarytool submit "dist/Skydive Logbook.app.zip" \
  --apple-id "your@apple.id" \
  --team-id "<TEAM_ID>" \
  --password "<APP_SPECIFIC_PASSWORD>" \
  --wait

# 5. Staple the notarization ticket so the binary works offline:
xcrun stapler staple "dist/Skydive Logbook.app"
```

References:
- Apple's Developer ID workflow:
  <https://developer.apple.com/documentation/security/signing-and-notarizing-macos-software>
- `notarytool` reference:
  <https://developer.apple.com/documentation/security/customizing-the-notarization-workflow>

To embed signing into the spec, set `codesign_identity=` on the
`EXE()` call to the Developer ID string above. We leave this `None`
in-tree because the identity is per-developer; pass it via env var
if you script the build.

### Windows — Authenticode

```powershell
signtool sign /tr http://timestamp.digicert.com /td sha256 \
  /fd sha256 /a "dist\skydive-logbook\skydive-logbook.exe"
```

Requires a code-signing certificate from DigiCert / Sectigo / etc.
EV certs ship as a USB-HSM token; OV/IV certs as a `.pfx` file.

References:
- `signtool` reference:
  <https://learn.microsoft.com/en-us/dotnet/framework/tools/signtool-exe>

### Linux — none

Linux distros sign packages, not binaries. AppImages can be
optionally signed with `appimagetool --sign` (gpg) but this is
informational, not gating.

---

## Per-platform verification gaps (audit 2026-04-29)

The agent that wrote this spec validates the Python syntax and the
data-file paths it bundles. **It cannot produce the binary
artefacts** — that requires real macOS / Windows / Linux build
machines. Concretely:

- macOS `.app` build: untested; first build pass on a real macOS
  machine will surface any missing `hiddenimports` or `binaries`
  the static analysis missed.
- Windows `.exe` build: untested; same caveat. Watch for
  `pywebview` requesting a runtime via WebView2 (Edge Chromium) —
  WebView2 is bundled into Windows 11 by default but not Windows
  10 LTSC; the installer must check.
- Linux ELF build: untested; the AppImage staging step is
  documented above but not run.

When the first real build per platform happens, append a "verified
on \<commit\>, \<machine\>" line to this section.

---

## CI

GitHub Actions Linux runners can build a Linux PyInstaller bundle
as a smoke test (see `.github/workflows/ci.yml`). macOS runners
exist on GitHub but run the build on every push is too slow for
v0.1 — CI builds the Linux artefact only; release builds are
manual until the schedule justifies a release-build job.
