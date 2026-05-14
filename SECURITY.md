# Security policy

Thank you for taking the time to look at this. The project handles
personal data (jump logs, FlySight CSVs, video, equipment records,
medical certificates), so reports are taken seriously even though the
app is single-user and runs on localhost.

## How to report a vulnerability

**Please do not open a public issue.**

The preferred path is GitHub's private vulnerability reporting:

1. Go to the **Security** tab of this repository.
2. Click **Report a vulnerability**.
3. Fill in what you found, how to reproduce it, and the impact you
   see.

If that isn't available to you, email the address in the maintainer's
GitHub profile with `[skydive-logbook security]` as the subject prefix.

## What to expect

- **Triage target:** 7–14 days (single maintainer, best-effort).
- **Acknowledgement:** within 7 days of a private report you should
  hear back at least to confirm receipt.
- **Disclosure:** coordinated. Once a fix is shipped, the report is
  written up as a GitHub Security Advisory and credited to the
  reporter unless they prefer anonymity.

If a reported issue is reproduced and accepted, expect a fix on the
next minor release; critical issues ship faster.

## Threat model in plain language

This shapes what is and isn't in scope.

- **The app is single-user.** No multi-tenant isolation to defend.
- **The HTTP server binds to loopback only** (`127.0.0.1`). Per
  `DECISIONS.md` D48, v0.1 has no authentication surface. If you bind
  the server to a non-loopback interface (say, to share with a LAN
  device), that is a configuration you took on yourself and is
  explicitly out of scope.
- **The logbook folder is the trust boundary.** Files inside the
  user's logbook folder are trusted (they came from the user). Files
  *arriving* — multipart uploads, XML the user pastes in, attachments
  in arbitrary user-named formats — are not, and pass through the
  defences listed below.

### Defences worth knowing about

- **XML parsing (`backend/xml/validator.py`):** hardened lxml parser
  with entity resolution off, DTD loading off, network off, and a
  byte-level scan rejecting any `<!DOCTYPE` before the parser sees
  the bytes. XXE and billion-laughs attacks are structurally
  impossible. Input is capped at 10 MB. Tests in
  `backend/tests/test_validator.py` pin every variant.
- **Path safety (`backend/storage/filesystem.py`):** every path
  derived from user input goes through `safe_join` plus
  `sanitize_folder_name` / `sanitize_filename`. Forbidden characters,
  Windows reserved device names, trailing dot/space, names that
  decode to `.` / `..`, and 255-UTF-8-byte length cap are all
  enforced. Resolved paths must remain under the logbook root.
- **Atomic writes (`backend/storage/filesystem.py`):** every
  persisted file is written via `atomic_write` (or
  `atomic_write_stream` for attachments) — temp file + `F_FULLFSYNC`
  on Darwin / `fsync` elsewhere + `os.replace` + parent-directory
  `fsync`. Crash mid-write never leaves a torn file.
- **Integrity (`backend/storage/manifest.py`):** every jump folder
  carries a `SHA256SUMS` manifest verified by
  `python -m backend.scripts.verify`. Silent corruption (bad cloud
  sync, bit rot) surfaces as a verify failure, not a silently-served
  bad file.
- **CVE-pinned dependency floors (`pyproject.toml`):** `starlette`
  and `python-multipart` are floor-pinned to versions that resolve
  every multipart-DoS and Range-header CVE we are aware of. The
  exact CVEs are cited inline.

### Realistic concerns to look at

- Crafted XML that gets past the parser hardening
- Path-traversal in attachment filenames or jump titles
- Zip-slip in future imports (D14 defers imports — when they land,
  this is the place to look)
- Dependency CVEs (Dependabot is enabled; please report anything
  Dependabot misses)
- A logbook folder synced from a hostile peer through Dropbox /
  iCloud / Syncthing

### Out of scope

- Attacks that assume local code execution. The threat model already
  assumes the user owns the machine. A Trojan in the user's home
  directory is not a Skydive Logbook bug.
- Attacks that assume the user has manually bound the server to a
  public interface against the documented loopback-only posture.
- Theoretical timing attacks on the in-process writer lock.
- Privacy concerns about Dropbox / iCloud / Syncthing themselves —
  those are the user's choice of sync provider.

## Supported versions

Pre-alpha; only the latest commit on `main` is supported. Once v0.1
is tagged, the supported-versions table will be:

| Version | Supported |
| ------- | --------- |
| latest minor on `main` | ✅ |
| anything older | ❌ |

## Coordinated disclosure

If you'd rather coordinate publicly with a fix-then-disclose window,
say so in your report. A 90-day disclosure window is the default; we
will hold longer if the fix is non-trivial and you're willing to wait.

## Hall of fame

There isn't one yet. The first person to report something accepted will
seed it.
