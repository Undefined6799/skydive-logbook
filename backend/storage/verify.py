"""On-demand byte-level integrity check (D25 §verify).

``verify_logbook`` is the single operation in v0.1 that re-hashes
attachment bytes on disk. Every read path (service layer, reindex,
reconcile) trusts ``jump.xml``'s claimed hashes; verify is where the
trust gets audited.

Checks, per jump folder:

  * The XML under ``jump.xml`` parses through the hardened parser
    (D2) and validates against its declared XSD namespace (D18).
  * Every ``<attachment>`` file is rehashed from disk and compared
    to the ``<sha256>`` claim. Mismatches are reported.
  * ``SHA256SUMS`` is parsed and structurally compared to what
    ``manifest.from_jump_xml`` would produce. Mismatches indicate a
    stale manifest (regenerable via ``folder_reconcile``).
  * Files in the folder not referenced by ``jump.xml`` and not in the
    always-excluded set (``SHA256SUMS``, ``summary.md``, etc.) are
    reported as orphans — surplus to the jump's declared state.

Across-folder check:

  * Two folders claiming the same ``(user_id, jump_number)`` pair are
    reported as duplicates (D23). The app refuses to auto-resolve;
    the user edits one or moves the other.

Scope:

  * **Read-only.** verify never writes. Fixing a stale manifest is
    ``folder_reconcile``'s job; fixing an invalid jump.xml is the
    user's job.
  * Walks both ``jumps/`` (active) and ``.trash/`` (soft-deleted per
    D19). Per-folder checks apply to both, but with **different
    strictness** (D62): live jumps are XSD-strict; trashed jumps are
    parse-only because in-place schema renames (D57, D61) leave
    older trashed files structurally fine but XSD-noncompliant.
    The duplicate jump_number check applies to ``jumps/`` only —
    trashed folders are not part of the active uniqueness namespace.
  * ``.trash/`` also hosts namespace subdirs for non-jump entities
    (``.trash/rigs/``, ``.trash/dropzones/``, ``.trash/inventory/``,
    ``.trash/people/``, ``.trash/jumpers/``). These are recognised
    and skipped in v0.1 — entity-aware trash validation is a
    deferred follow-up to D62. Unknown direct children of
    ``.trash/`` are reported as ``unknown_trash_entry`` so genuine
    surprises don't pass silently.
  * Runs without the D9 lock. verify is a reader; D9 only blocks
    concurrent writers. A racing write during verify may produce a
    transient false-positive that clears on the next run.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..xml.validator import (
    XMLError,
    namespace_of,
    parse,
    schema_for_namespace,
    validate,
)
from . import manifest

# Top-level subfolders verify walks. ``jumps/`` holds active data;
# ``.trash/`` holds soft-deleted folders (D19). Per-folder integrity
# checks run in both; the duplicate-jump_number check runs only in
# the active namespace.
_ACTIVE_JUMPS_DIR = "jumps"
_TRASH_DIR = ".trash"

# Trashed-jump folders are named ``<basic-iso-ts>_<original-name>``
# by ``storage.trash._now_utc_basic_iso`` (see ``storage/trash.py``).
# The millisecond suffix is optional — older trash predates the
# millisecond-precision change and uses ``YYYYMMDDTHHMMSSZ_``.
# The ``Z`` is also tolerated as optional because some existing
# fixtures predate the ``Z``-always rule; verify is permissive on
# read.
_TRASH_JUMP_NAME_RE = re.compile(r"^\d{8}T\d{6}(?:\.\d{3})?Z?_")

# Direct children of ``.trash/`` that are namespace subdirs for
# non-jump entities (D33 rigs, D44 dropzones, D54 people, plus the
# pre-existing jumpers and inventory shelves). Verify skips these
# in v0.1; entity-aware trash validation lands separately.
_TRASH_NAMESPACE_SUBDIRS: frozenset[str] = frozenset({
    "rigs",
    "dropzones",
    "inventory",
    "people",
    "jumpers",
})

# Files that live in a jump folder legitimately without being
# referenced by ``jump.xml``. Keep in sync with ``manifest._EXCLUDED_ALWAYS``
# but include ``jump.xml`` itself (it's the anchor, not an orphan).
_FOLDER_EXCLUDES: frozenset[str] = frozenset({
    manifest.JUMP_XML_NAME,
    manifest.MANIFEST_NAME,
    f"{manifest.MANIFEST_NAME}.tmp",
    "summary.md",
    "summary.md.tmp",
    # OS-generated noise that shows up after a user browses the
    # logbook in Finder / Explorer / nautilus. Reporting these as
    # ``orphan_file`` trains users to ignore real verify findings;
    # better to filter them. Audit CODE-5 (2026-04-29).
    ".DS_Store",       # macOS Finder metadata
    "Thumbs.db",       # Windows Explorer thumbnail cache
    "desktop.ini",     # Windows folder customization
    ".AppleDouble",    # older macOS resource-fork directory marker
})

# AppleDouble pair files (``._<original>``) accompany every regular
# file on filesystems that don't natively store macOS resource forks.
# We can't enumerate them by name — every attachment can spawn its
# own ``._<filename>`` — so the check below uses a prefix test.
_OS_NOISE_PREFIXES: tuple[str, ...] = ("._",)


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class VerifyIssue:
    """One problem found during ``verify_logbook``.

    ``folder`` is always POSIX-style relative to ``logbook_root``.
    ``kind`` is a stable machine-readable identifier; callers (CLI,
    future REST endpoint) branch on it. ``detail`` is human-readable.
    """

    folder: str
    kind: str
    detail: str


@dataclass(frozen=True)
class VerifyReport:
    """Result of ``verify_logbook``.

    ``clean`` is True iff no issues were found. CLI callers map to
    exit 0 (clean) / exit 1 (issues present).
    """

    folders_scanned: int
    # ``list[VerifyIssue]`` (the generic alias) is callable and returns
    # an empty list; passing it as the factory preserves the element
    # type so pyright sees ``list[VerifyIssue]`` rather than
    # ``list[Unknown]`` from a bare ``list``.
    issues: list[VerifyIssue] = field(default_factory=list[VerifyIssue])

    @property
    def clean(self) -> bool:
        return not self.issues


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def verify_logbook(logbook_root: Path) -> VerifyReport:
    """Run every D25 integrity check against ``logbook_root``.

    Returns a structured report; does not raise on finding issues.
    Only unrecoverable filesystem errors (e.g. ``logbook_root`` is
    not a directory) propagate as ``OSError``.
    """
    logbook_root = Path(logbook_root)

    issues: list[VerifyIssue] = []
    folders_scanned = 0

    # Active jumps: run per-folder checks AND participate in the
    # duplicate-number check.
    active_jumps: list[tuple[Path, int | None, str]] = []
    # Each entry: (folder_path, jump_number or None if unparseable, user_id)

    active_dir = logbook_root / _ACTIVE_JUMPS_DIR
    if active_dir.is_dir():
        for folder in sorted(active_dir.iterdir()):
            if not folder.is_dir():
                continue
            folders_scanned += 1
            jump_number, user_id, folder_issues = _verify_folder(
                folder, logbook_root
            )
            issues.extend(folder_issues)
            active_jumps.append((folder, jump_number, user_id))

    # Trashed entries: per-folder checks run with parse-only
    # validation (D62) because in-place schema renames (D57, D61)
    # leave older trashed jump files XSD-noncompliant though
    # structurally fine. Direct children of ``.trash/`` are
    # classified into:
    #   * trashed-jump folders (``<ts>_<name>``) — verified with
    #     ``is_trash=True`` (parse-only); not part of duplicate
    #     detection per D19 + D23.
    #   * namespace subdirs for non-jump entities (``rigs/``,
    #     ``dropzones/``, ``inventory/``, ``people/``, ``jumpers/``)
    #     — skipped in v0.1; entity-aware trash validation lands
    #     separately. Tracked but not validated.
    #   * anything else — reported as ``unknown_trash_entry`` so a
    #     surprising entry doesn't pass silently.
    trash_dir = logbook_root / _TRASH_DIR
    if trash_dir.is_dir():
        for entry in sorted(trash_dir.iterdir()):
            if not entry.is_dir():
                continue
            name = entry.name
            if _TRASH_JUMP_NAME_RE.match(name):
                folders_scanned += 1
                _, _, folder_issues = _verify_folder(
                    entry, logbook_root, is_trash=True
                )
                issues.extend(folder_issues)
            elif name in _TRASH_NAMESPACE_SUBDIRS:
                # Skip — entity-aware verify is a follow-up to D62.
                # We deliberately don't recurse: false-positiving a
                # trashed rig as an "invalid jump folder" is worse
                # than not checking it at all in v0.1.
                continue
            else:
                issues.append(
                    VerifyIssue(
                        folder=_rel(entry, logbook_root),
                        kind="unknown_trash_entry",
                        detail=(
                            "unexpected direct child of .trash/ — "
                            "not a trashed-jump folder and not a "
                            "recognised entity namespace"
                        ),
                    )
                )

    # Cross-folder duplicate check (D23). Groups by (user_id,
    # jump_number); every group with more than one member is a
    # duplicate. We report on the folder whose name is alphabetically
    # second so there is one issue per *extra* claimant, not one per
    # folder involved — ``verify`` exit-status semantics care about
    # issue count.
    by_key: dict[tuple[str, int], list[Path]] = {}
    for folder, num, user in active_jumps:
        if num is None:
            # Jump had no parseable jump_number; already reported as an
            # invalid_xml issue by _verify_folder.
            continue
        by_key.setdefault((user, num), []).append(folder)
    for (user, num), folders in by_key.items():
        if len(folders) > 1:
            folders_sorted = sorted(folders)
            first = folders_sorted[0]
            for dup in folders_sorted[1:]:
                issues.append(
                    VerifyIssue(
                        folder=_rel(dup, logbook_root),
                        kind="duplicate_jump_number",
                        detail=(
                            f"jump_number {num} (user_id={user!r}) is also "
                            f"claimed by {_rel(first, logbook_root)!r}"
                        ),
                    )
                )

    # Cross-entity reference check (D54). Every jump may reference a
    # rig, a dropzone, a packer (Person), and a list of group members
    # (Persons). The service layer resolves missing references softly
    # — the UI shows "Unknown <uuid-prefix>" rather than erroring — but
    # the silent degradation can hide accidental deletions from the
    # user. Verify surfaces them here as ``dangling_reference`` issues
    # so a hand-edit, a partial restore, or a sync race becomes
    # visible.
    valid_ids = _collect_entity_ids(logbook_root)
    for folder, _, _ in active_jumps:
        issues.extend(
            _check_dangling_references(folder, logbook_root, valid_ids)
        )

    return VerifyReport(folders_scanned=folders_scanned, issues=issues)


# --------------------------------------------------------------------------- #
# Per-folder checks
# --------------------------------------------------------------------------- #

def _verify_folder(
    folder: Path, logbook_root: Path, *, is_trash: bool = False
) -> tuple[int | None, str, list[VerifyIssue]]:
    """Run every per-folder check. Returns (jump_number, user_id, issues).

    ``jump_number`` is None when ``jump.xml`` failed to parse or validate
    — in that case the folder cannot participate in the duplicate-number
    check, and other checks may be skipped where they would depend on a
    parsed tree.

    ``is_trash`` flips the XSD-validation step off (D62). Trashed
    folders are historical: in-place schema renames (D57 removed
    ``landing_direction`` / ``group_size``; D61 renamed ``fun_jump``)
    leave older trashed jumps structurally fine but XSD-noncompliant.
    Verify still catches truly corrupt files (parse failure) and
    every other check (attachment hashes, manifest, orphans) runs
    unchanged.
    """
    issues: list[VerifyIssue] = []
    rel = _rel(folder, logbook_root)

    jump_xml = folder / manifest.JUMP_XML_NAME
    if not jump_xml.is_file():
        issues.append(
            VerifyIssue(
                folder=rel,
                kind="invalid_folder",
                detail="missing jump.xml",
            )
        )
        # Cannot continue — the folder has no anchor.
        return None, "default", issues

    # Check 1: parse (always) + XSD validate (live jumps only).
    # ``is_trash`` skips the XSD step per D62 so D57/D61-style
    # schema-evolution drift in old trashed files doesn't generate
    # false positives. Parse failure (genuinely corrupt XML) is
    # still reported in both modes.
    try:
        jump_bytes = jump_xml.read_bytes()
        root = parse(jump_bytes)
        ns = namespace_of(root)
        if not is_trash:
            schema = schema_for_namespace(ns, schema_dir=logbook_root)
            validate(root, schema=schema)
    except (XMLError, OSError) as exc:
        issues.append(
            VerifyIssue(
                folder=rel,
                kind="invalid_xml",
                detail=str(exc),
            )
        )
        return None, "default", issues

    ns_prefix = f"{{{ns}}}" if ns else ""

    # Extract jump_number (required by XSD, so validation above
    # guarantees presence; still tolerate a bad int cast defensively).
    jump_number: int | None = None
    num_el = root.find(f"{ns_prefix}jump_number")
    if num_el is not None and num_el.text is not None:
        try:
            jump_number = int(num_el.text)
        except ValueError:
            issues.append(
                VerifyIssue(
                    folder=rel,
                    kind="invalid_xml",
                    detail=f"jump_number not an integer: {num_el.text!r}",
                )
            )

    # D8: user_id is a service-layer parameter, not in jump.xml today.
    # Every jump is effectively user_id='default' until multi-user lands.
    user_id = "default"

    # Collect attachment (filename, sha256) pairs from the tree.
    referenced: dict[str, str] = {}
    atts_el = root.find(f"{ns_prefix}attachments")
    if atts_el is not None:
        for a in atts_el.findall(f"{ns_prefix}attachment"):
            fn_el = a.find(f"{ns_prefix}filename")
            sha_el = a.find(f"{ns_prefix}sha256")
            filename = fn_el.text if fn_el is not None else None
            claimed = sha_el.text if sha_el is not None else None
            if filename and claimed:
                referenced[filename] = claimed

    # Check 2: rehash each attachment, compare to claim.
    for filename, claimed in referenced.items():
        att_path = folder / filename
        if not att_path.is_file():
            issues.append(
                VerifyIssue(
                    folder=rel,
                    kind="missing_attachment",
                    detail=(
                        f"{filename!r} referenced by jump.xml but not "
                        f"present on disk"
                    ),
                )
            )
            continue
        actual = manifest.sha256_file(att_path)
        if actual != claimed:
            issues.append(
                VerifyIssue(
                    folder=rel,
                    kind="attachment_mismatch",
                    detail=(
                        f"{filename!r} hash on disk ({actual}) does not "
                        f"match jump.xml claim ({claimed})"
                    ),
                )
            )

    # Check 3: SHA256SUMS structurally matches what from_jump_xml would
    # produce. Done via manifest.parse of both sides so ordering and
    # whitespace tolerances match ``folder_reconcile``'s comparison.
    sums_path = folder / manifest.MANIFEST_NAME
    if sums_path.is_file():
        try:
            on_disk = set(manifest.parse(sums_path.read_bytes()))
        except ValueError as exc:
            issues.append(
                VerifyIssue(
                    folder=rel,
                    kind="stale_manifest",
                    detail=f"SHA256SUMS is malformed: {exc}",
                )
            )
            on_disk = None  # type: ignore[assignment]
        else:
            # D62: trash skips XSD validation in the manifest helper
            # too — otherwise from_jump_xml would re-trip the same
            # schema-drift error that ``is_trash`` is meant to bypass.
            expected_bytes = manifest.from_jump_xml(
                folder,
                logbook_root=logbook_root,
                validate_xsd=not is_trash,
            )
            expected = set(manifest.parse(expected_bytes))
            if on_disk != expected:
                issues.append(
                    VerifyIssue(
                        folder=rel,
                        kind="stale_manifest",
                        detail=(
                            "SHA256SUMS does not match jump.xml's claims "
                            "(run reindex or folder_reconcile to repair)"
                        ),
                    )
                )
    else:
        issues.append(
            VerifyIssue(
                folder=rel,
                kind="stale_manifest",
                detail="SHA256SUMS is missing",
            )
        )

    # Check 4: orphan files — anything in the folder that is not the
    # anchor, not in the always-excluded set, and not referenced by
    # ``jump.xml``. We walk recursively: attachment filenames are
    # single-segment per D4, so any file in a subdirectory is
    # inherently unreferenced and therefore an orphan.
    for entry in sorted(folder.rglob("*")):
        if not entry.is_file():
            continue
        rel_path = entry.relative_to(folder).as_posix()
        name = entry.name
        if name in _FOLDER_EXCLUDES:
            continue
        # AppleDouble pair files (``._<filename>``) are filesystem
        # noise, not user attachments. Pre-fix-match rather than
        # listed by name because every attachment spawns its own.
        if any(name.startswith(p) for p in _OS_NOISE_PREFIXES):
            continue
        # A referenced attachment is at the root of the folder only
        # (D4 forbids '/' in filenames), so compare by rel_path.
        if rel_path in referenced:
            continue
        issues.append(
            VerifyIssue(
                folder=rel,
                kind="orphan_file",
                detail=(
                    f"{rel_path!r} is not referenced by jump.xml and is "
                    f"not a derived file"
                ),
            )
        )

    return jump_number, user_id, issues


# --------------------------------------------------------------------------- #
# Cross-entity reference check (D54)
# --------------------------------------------------------------------------- #

# Subdirectories holding entity records that jumps can reference. Each
# entry maps the entity kind to the kind's storage layout — either
# ``flat`` (filename stem is the UUID) or ``folder`` (folder name is
# the UUID; the canonical XML sits inside). Rigs are special: the
# folder name is the sanitized nickname, not the UUID, so the UUID
# must be parsed from rig.xml's ``<id>`` element.
_ENTITY_DIRS_FLAT: tuple[tuple[str, str], ...] = (
    ("dropzones", "dropzones"),
    ("people", "people"),
)

_ENTITY_DIRS_FOLDER_NAMED_BY_UUID: tuple[tuple[str, str], ...] = (
    ("jumpers", "jumpers"),
)


def _collect_entity_ids(logbook_root: Path) -> dict[str, set[str]]:
    """Walk every entity directory and return the active UUIDs per kind.

    ``Active`` means present under the live path (``rigs/``,
    ``dropzones/``, ``people/``, ``jumpers/``). Trashed entities under
    ``.trash/`` are intentionally NOT collected: per D19, soft-deleted
    records are still on disk but no longer part of the active
    namespace. A jump referencing a trashed entity IS dangling — that's
    the case this check catches.
    """
    ids: dict[str, set[str]] = {
        "rigs": set(),
        "dropzones": set(),
        "people": set(),
        "jumpers": set(),
    }

    # Flat-file entities — filename stem is the UUID. Cheap walk; no XML
    # parsing needed.
    for kind, subdir in _ENTITY_DIRS_FLAT:
        d = logbook_root / subdir
        if not d.is_dir():
            continue
        for entry in d.iterdir():
            if entry.is_file() and entry.suffix == ".xml":
                ids[kind].add(entry.stem)

    # Folder-shaped entities where the folder name itself is the UUID
    # (D47 migrated jumpers to ``jumpers/<uuid>/jumper.xml``).
    for kind, subdir in _ENTITY_DIRS_FOLDER_NAMED_BY_UUID:
        d = logbook_root / subdir
        if not d.is_dir():
            continue
        for entry in d.iterdir():
            if entry.is_dir():
                ids[kind].add(entry.name)

    # Rigs — folder name is the sanitized nickname, not the UUID. The
    # UUID lives inside ``rig.xml`` as ``<id>``. Parse + extract.
    rigs_dir = logbook_root / "rigs"
    if rigs_dir.is_dir():
        for entry in rigs_dir.iterdir():
            if not entry.is_dir():
                continue
            rig_xml = entry / "rig.xml"
            if not rig_xml.is_file():
                continue
            try:
                root = parse(rig_xml.read_bytes())
            except XMLError:
                # A broken rig.xml is reported elsewhere; skip here so
                # the dangling-ref check doesn't double-report.
                continue
            ns_prefix = f"{{{namespace_of(root)}}}" if namespace_of(root) else ""
            id_el = root.find(f"{ns_prefix}id")
            if id_el is not None and id_el.text:
                ids["rigs"].add(id_el.text.strip())

    return ids


def _check_dangling_references(
    folder: Path, logbook_root: Path, valid_ids: dict[str, set[str]]
) -> list[VerifyIssue]:
    """Report references in ``folder/jump.xml`` that don't resolve.

    Each unresolved reference is one ``dangling_reference`` issue
    carrying the field name (``rig_id`` / ``dropzone_id`` /
    ``packed_by`` / ``group_members``) and the orphaned UUID. Service
    layer resolution is soft per D54 — these don't break the app —
    but the user typically wants to know.
    """
    issues: list[VerifyIssue] = []
    jump_xml = folder / manifest.JUMP_XML_NAME
    if not jump_xml.is_file():
        return issues  # _verify_folder already reported invalid_folder.

    try:
        root = parse(jump_xml.read_bytes())
    except XMLError:
        return issues  # _verify_folder already reported invalid_xml.

    ns_prefix = f"{{{namespace_of(root)}}}" if namespace_of(root) else ""
    rel = _rel(folder, logbook_root)

    # Single-UUID references. Each pair binds the XML element name to
    # the entity kind whose ID set we check against.
    for tag, kind in (
        ("rig_id", "rigs"),
        ("dropzone_id", "dropzones"),
        ("packed_by", "people"),
    ):
        el = root.find(f"{ns_prefix}{tag}")
        if el is not None and el.text:
            ref = el.text.strip()
            if ref and ref not in valid_ids[kind]:
                issues.append(
                    VerifyIssue(
                        folder=rel,
                        kind="dangling_reference",
                        detail=(
                            f"<{tag}>{ref}</{tag}> does not resolve to an "
                            f"active {kind[:-1]} (entity may have been "
                            f"deleted or never existed)"
                        ),
                    )
                )

    # Multi-UUID reference: group_members is a wrapper containing zero
    # or more <member> children, each a Person UUID.
    members_el = root.find(f"{ns_prefix}group_members")
    if members_el is not None:
        for member_el in members_el.findall(f"{ns_prefix}member"):
            if member_el.text:
                ref = member_el.text.strip()
                if ref and ref not in valid_ids["people"]:
                    issues.append(
                        VerifyIssue(
                            folder=rel,
                            kind="dangling_reference",
                            detail=(
                                f"<group_members><member>{ref}</member> does "
                                f"not resolve to an active person (entity may "
                                f"have been deleted or never existed)"
                            ),
                        )
                    )

    return issues


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _rel(path: Path, root: Path) -> str:
    """POSIX-style relative path for reporting."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        # Shouldn't happen in normal use; fall back to absolute.
        return str(path)
