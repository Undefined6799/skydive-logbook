# Skydive Logbook

This folder is a skydiving logbook. Every jump is one subdirectory under
`jumps/` containing a machine-readable XML file plus any attachments
(FlySight tracks, video, photos). The XML is the source of truth; you
can read and verify the logbook with a text editor and an XSD validator
even if this app is gone.

## Layout

```
README.md            this file
SCHEMA.v1.xsd        schema that every jump.xml is validated against
SCHEMA.v2.xsd        future schemas live alongside v1 (older jumps keep
                     validating against their declared namespace)
settings.xml         per-logbook settings (units, jumper name); optional
.logbook.lock        advisory file lock, created while the app is running
.trash/              soft-deleted jumps; recoverable by moving folders
                     back into jumps/
dropzones/           one XML file per dropzone record
  <uid>.xml
inventory/           rig-manager components, one XML file per record
  mains/<uid>.xml          main canopies
  reserves/<uid>.xml       reserve canopies
  aads/<uid>.xml           automatic activation devices
  containers/<uid>.xml     containers (rigs hold these by reference)
rigs/                rig assemblies; folder-with-manifest per rig
  <nickname>/              folder named after the rig nickname
    rig.xml                jurisdiction + four current_*_id refs +
                           repack history
    SHA256SUMS             shasum -c compatible checksums
jumpers/             jumper records; one flat XML file per jumper
  <uid>.xml                exit weight + staleness timestamp
jumps/
  [851] 2026-04-22/  human-readable name; internal id is in jump.xml
    jump.xml         structured source of truth
    SHA256SUMS       shasum -c compatible checksums for every file
    summary.md       one-paragraph plain-language preview (regenerable)
    flysight.csv     original upload; referenced with its SHA-256 hash
    video_01.mp4     attachment
    photos/          attachments may live in subdirectories
```

`index.sqlite` may also be present in the logbook root. It is a
rebuildable index — every field in it also lives in XML — so deleting
it does not lose any user data. The app will rebuild it on next open.

## Verifying without the app

```bash
# Validate a jump.xml against its declared schema version.
xmllint --schema SCHEMA.v1.xsd "jumps/[851] 2026-04-22/jump.xml" --noout

# Verify integrity of a single jump folder.
cd "jumps/[851] 2026-04-22/"
shasum -c SHA256SUMS
```

`jump.xml` carries the stable `<id>` (a UUID). The folder name is for
humans browsing in Finder or Explorer — you can safely rename a folder
to correct a jump number or date; the UUID inside the XML is what
internal references use.

## Editing by hand

Plain-text edits to any XML file will be accepted on next read as long
as the file still validates against its schema. If you edit a
`jump.xml`, you will need to regenerate `SHA256SUMS` (or delete it —
the app rebuilds it from the XML on next open) and optionally delete
`summary.md` so the plain-language preview refreshes.

## What not to touch

- `.logbook.lock` — advisory lock. Delete only if you know no app
  instance is running.
- `index.sqlite` — fine to delete; will be rebuilt. Do not edit.
- Files under `.trash/` — move back to `jumps/` to restore, or delete
  to permanently remove.
