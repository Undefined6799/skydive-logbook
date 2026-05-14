"""Make a logbook folder ready to hold jumps (D29).

``bootstrap_logbook(root)`` is an idempotent filesystem primitive. It
can be called on a fresh folder, a folder that already has content, or
an app upgrade where the shipped XSD has new additive fields, and the
result is the same: the folder contains every app-shipped schema file
(D18), a human-readable README (D5), and the three subdirectories the
rest of the storage layer writes into.

Per D29 the contract is deliberately narrow:

  * **Creates / overwrites.** Every ``SCHEMA.v*.xsd`` shipped by the
    app — always overwritten. Within a schema version, changes are
    strictly additive (D18), so refreshing the file cannot invalidate
    older jumps written against the prior bytes.
  * **Creates only if missing.** ``README.md``. Preserving
    user-authored edits across app upgrades is worth the small cost of
    not picking up template improvements automatically.
  * **Creates (mkdir).** ``jumps/``, ``dropzones/``,
    ``inventory/mains/``, ``inventory/reserves/``, ``inventory/aads/``,
    ``inventory/containers/``, ``.trash/``. Idempotent by
    ``exist_ok=True``; nested paths use ``parents=True`` so
    ``inventory/`` is auto-created. Rig-side subdirs (``rigs/``,
    ``jumpers/``) come in a later phase alongside those entities.

What this module deliberately does **not** do:

  * **No ``settings.xml``.** That file's schema is not yet fixed by a
    D-entry; writing an empty stub here would commit to an implicit
    shape we would then need to supersede.
  * **No ``index.sqlite``.** Owned by ``storage/index.py`` and D26.
  * **No ``.logbook.lock``.** Owned by ``storage/lockfile.py`` (D9).
    The caller (``main.py``) acquires the lock *before* calling this
    function so bootstrap runs under mutual exclusion; tests call this
    module directly without a lock, which stays simple because every
    step is idempotent.

Errors propagate unmodified: a permission error, a read-only mount, or
``root`` pointing at a file all raise the underlying ``OSError``. The
caller decides how to present the failure (``main.py`` prints a
friendly one-liner and exits non-zero).
"""
from __future__ import annotations

from importlib import resources
from pathlib import Path

from ..xml.validator import XSDValidationError
from ..xml.validator import validate_schema_file as xml_validate_schema_file
from .filesystem import atomic_write

# Subdirectories this module creates. Kept as a tuple so the set is
# explicit and stable; if a later decision adds one (e.g. ``imports/``
# when the deferred-import feature lands), it goes here and in D29.
#
# ``dropzones/`` lands additively per D44 (R.D.0/R.D.1) — flat
# single-file-per-DZ structure, sibling to ``jumps/``.
#
# ``people/`` lands additively per D54 (Phase 2b) — same flat
# single-file-per-record shape as ``dropzones/``. People are jumpers
# the owner has flown with (referenced from <jump>/<group_members>)
# and packers (referenced from <jump>/<packed_by>).
#
# R.0.1 (D33) dropped the legacy ``equipment/`` subdir. Rig-manager
# inventory subdirs land in R.0.3a (one per kind under ``inventory/``)
# alongside the create/get services that write into them. Rig-side
# subdirs (``rigs/`` for the folder-with-manifest rig assemblies,
# ``jumpers/`` for the flat single-file jumper records) land in R.2.0b
# alongside their create/get services.
#
# Names with a slash (e.g. ``inventory/mains``) are passed through
# ``Path.mkdir(parents=True, exist_ok=True)`` so the parent
# ``inventory/`` directory is created automatically. A flat name like
# ``jumps`` likewise tolerates the parents flag at the root level.
_SUBDIRS: tuple[str, ...] = (
    "jumps",
    "dropzones",
    "people",
    "inventory/mains",
    "inventory/reserves",
    "inventory/aads",
    "inventory/containers",
    "rigs",
    "jumpers",
    ".trash",
)

# Resource package references. Looked up once at import time so the
# failure mode of "package not installed correctly" surfaces early
# rather than on the first bootstrap call.
_SCHEMA_PACKAGE = "backend.xml.schema"
_TEMPLATE_PACKAGE = "backend.storage.templates"

# The template filename shipped under ``backend/storage/templates/``.
# Named ``LOGBOOK_README.md`` in source to disambiguate from the
# *project* README (which is about the app itself, not about a logbook
# folder); written to disk as ``README.md`` per D5.
_README_TEMPLATE_NAME = "LOGBOOK_README.md"
_README_OUTPUT_NAME = "README.md"


def bootstrap_logbook(root: Path) -> None:
    """Ensure ``root`` is a ready-to-use logbook folder (D29).

    Idempotent. Safe to call on every app start, on first run after the
    user picks a folder, and on app upgrades that ship a refreshed XSD.

    Steps, in order:

    1. Create ``root`` (and any missing parents).
    2. Install every ``SCHEMA.v*.xsd`` from the ``backend.xml.schema``
       resource package into ``root``. Uses ``atomic_write`` (D10) so
       a crash mid-write does not leave a torn XSD in the logbook.
    3. Install ``README.md`` from the template package, only if the
       target does not already exist.
    4. ``mkdir(exist_ok=True)`` the subdirectories listed in
       ``_SUBDIRS``.

    Arguments:
      root: Absolute path to the logbook folder. ``~`` should already
        be expanded by the caller (``load_settings`` handles this).

    Raises:
      OSError (and subclasses: ``PermissionError``, ``FileExistsError``,
        ``NotADirectoryError``, …): propagated unmodified from the
        underlying filesystem call.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    # Step 2: schema files. Copy every SCHEMA.v*.xsd; the current app
    # may ship one (v1 today) or several (after a v2 schema lands).
    #
    # After copying each file, parse the result as an XMLSchema to
    # confirm it's intact. This catches a class of failure where the
    # file lands on disk corrupted (truncated, wrong encoding, partial
    # write surviving a crash) before it causes cryptic 500s on every
    # read — D2's invariant is "every read goes through the XSD", so a
    # broken XSD is fatal to the whole logbook. Better to fail loudly at
    # boot than at the first GET.
    schema_dir = resources.files(_SCHEMA_PACKAGE)
    for entry in schema_dir.iterdir():
        name = entry.name
        if name.startswith("SCHEMA.v") and name.endswith(".xsd"):
            # ``Traversable.read_bytes()`` is the stdlib-supported way
            # to read a resource regardless of whether the package is
            # installed from source, a wheel, or a zipfile. See
            # https://docs.python.org/3/library/importlib.resources.html
            xsd_bytes = entry.read_bytes()
            xsd_path = root / name
            atomic_write(xsd_path, xsd_bytes)
            # Confirm the just-written XSD compiles as a schema.
            # ``validate_schema_file`` lives in ``backend/xml/`` so the
            # direct lxml use stays inside the typed boundary; here we
            # just wrap its ``XSDValidationError`` into an ``OSError``
            # to keep ``main.py``'s existing OSError → exit-1 branch
            # unchanged.
            try:
                xml_validate_schema_file(xsd_path)
            except XSDValidationError as exc:
                # The file just landed on disk and lxml can't make a
                # schema from it. Either the bundled XSD is corrupt
                # (a build problem) or the write got mangled (a
                # filesystem problem). Either way, blocking startup
                # with a clear message is the right move.
                raise OSError(
                    f"installed schema {name} at {xsd_path} is not a valid "
                    f"XML Schema: {exc}. Delete the file and re-launch — "
                    "bootstrap will copy a fresh one. If the error persists, "
                    f"the bundled schema in backend/xml/schema/{name} may be "
                    "corrupt; verify it parses with `python -c \"from lxml "
                    "import etree; etree.XMLSchema(etree.parse('backend/"
                    f"xml/schema/{name}'))\"`."
                ) from exc

    # Step 3: README.md, preserving any user edits on re-bootstrap.
    readme_path = root / _README_OUTPUT_NAME
    if not readme_path.exists():
        template = resources.files(_TEMPLATE_PACKAGE).joinpath(_README_TEMPLATE_NAME)
        atomic_write(readme_path, template.read_bytes())

    # Step 4: subdirectories the storage layer writes into.
    # ``parents=True`` lets nested entries like ``inventory/mains``
    # auto-create their parent (``inventory/``) without us having to
    # list it separately.
    for name in _SUBDIRS:
        (root / name).mkdir(parents=True, exist_ok=True)

    # Step 5: D47 / Phase C.1 — migrate any legacy flat
    # ``jumpers/<uuid>.xml`` files to the folder-with-manifest shape
    # ``jumpers/<uuid>/jumper.xml`` + ``SHA256SUMS`` + ``attachments/``.
    # Idempotent and crash-resistant; a fresh logbook with no jumpers
    # is a no-op. See ``backend/storage/jumper_migration.py`` for the
    # exact crash semantics.
    from .jumper_migration import migrate_all_jumpers
    migrate_all_jumpers(root)
