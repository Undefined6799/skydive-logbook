"""Shared model constants and types used across every domain model."""

SCHEMA_NAMESPACE_V1 = "https://skydive-logbook.org/schema/v1"
"""XML namespace for v1. Every XML file the logbook produces (jump.xml,
dropzone records, rig-manager components per D33+D34) declares this as
its default namespace. See D18 for how future versions coexist."""

APP_NAME = "skydive-logbook"
APP_VERSION = "0.1.0"
"""Human-readable app version. Emitted as ``<generator>`` into every
XML file the app writes (Q4) so files carry their provenance for
bug reports and forensics. Bump in lockstep with pyproject.toml."""

GENERATOR_STRING = f"{APP_NAME}/{APP_VERSION}"


SHA256_HEX_PATTERN = r"^[0-9a-f]{64}$"

# IANA tz name pattern. Intentionally permissive: a name only has to
# *look* like a tz identifier at this layer (start with a letter, be
# composed of IANA-legal characters). The authoritative check happens
# in the service layer via ``zoneinfo.ZoneInfo(name)``, which knows
# the actual tz database.
#
# Accepts real names like:
#   America/Los_Angeles, Europe/London, Etc/GMT+10,
#   America/Argentina/Buenos_Aires, UTC
# Rejects obvious garbage (empty, shell metacharacters, leading digit,
# path traversal) so pattern-based tools have something to lean on.
#
# Ref: tz database naming rules — https://data.iana.org/time-zones/theory.html
IANA_TZ_PATTERN = r"^[A-Za-z][A-Za-z0-9+\-_/]*$"
