# Platform Portability Research — atomic_write, filelock, case-insensitive FS, SQLite WAL

## Summary for the Reviewer

**Three critical findings:**

1. **`os.replace` is atomic on all platforms, but durability varies sharply.** `os.replace(tmp, path)` atomically replaces the target on POSIX (via `rename(2)`) and Windows (via `MoveFileExW` with `MOVEFILE_REPLACE_EXISTING`). However, **durability survives a crash only if the parent directory itself is fsync'd**. The code fsync's the file but not the parent dir — this is a gap. On modern Linux ext4 (`data=ordered`), the risk is low; on macOS with disk caches or on SMB/NFS, the risk is real.

2. **`os.fsync` on macOS does NOT use F_FULLFSYNC by default.** CPython's `os.fsync` maps directly to the BSD `fsync(2)`, which Apple's own documentation warns may not flush disk caches. For true durability on macOS (especially relevant for users with external drives or SSDs with aggressive caching), the code should use `fcntl(fd, F_FULLFSYNC)`. SQLite's `fullfsync` pragma exists for exactly this reason.

3. **`filelock` uses flock on POSIX (advisory, not mandatory) and is NFS/SMB-unsafe without careful configuration.** On NFS before Linux 2.6.12, flock didn't work at all. Modern NFS and CIFS (SMB 5.5+) emulate flock as byte-range locks, but on CIFS this changed the semantics to **mandatory** locking — a design mismatch. On cloud-sync folders (Dropbox, OneDrive, iCloud), the lock file and logbook XML can desync.

---

## 1. `os.replace` + `os.fsync` — Atomicity vs Durability

### POSIX Baseline

**Atomicity:** [POSIX.1-2017 rename(2)](https://pubs.opengroup.org/onlinepubs/9699919799/functions/rename.html) guarantees that if the call returns successfully, "a link named new shall remain visible to other threads throughout the renaming operation and refer either to the file referred to by new or old before the operation began." This is atomic on the same filesystem.

**Durability:** POSIX does NOT guarantee that a crash immediately after `rename()` returns will preserve the new file. The rename changes the directory entry (in-memory), but the directory inode must also reach persistent storage. **POSIX fsync(2) does not fsync the directory.** Per [Linux fsync(2) man page](https://man7.org/linux/man-pages/man2/fsync.2.html): "Calling fsync() does not necessarily ensure that the entry in the directory containing the file has also reached disk. For that an explicit fsync() on a file descriptor for the directory is also needed."

The codebase calls `os.fsync(f.fileno())` on the file, then `os.replace(tmp, path)`, but never fsync's the parent directory. **This is correct for atomicity but insufficient for durability.**

### Linux (ext4/btrfs/XFS)

**ext4 with `data=ordered` (default):** Metadata (including directory entries) is journaled before data. A crash after `rename()` returns will have already written the journal entry for the directory update. Unclean recovery will replay it. **In practice, the file survives.**

**ext4 with `data=writeback` or `noauto_da_alloc` (older kernels):** The "0-byte file after crash" bug (CVE-2008-1375) could occur — directory entry committed but file data still pending. Modern kernels (post-2009) default to safer modes. **Current risk: very low.**

**btrfs & XFS:** Both use COW or similar techniques. The rename operation and its metadata are typically atomic with respect to crash recovery at the filesystem level, without requiring explicit parent-dir fsync.

### macOS (APFS)

[Apple's fsync(2) documentation](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/fsync.2.html) states clearly:

> "Note that while fsync() will flush all data from the host to the drive (i.e. the 'permanent storage device'), the drive itself may not physically write the data to the platters for quite some time and it may be written in an out-of-order sequence."
>
> "For applications that require tighter guarantees about the integrity of their data, Mac OS X provides the F_FULLFSYNC fcntl."

**Critical finding:** CPython's `os.fsync` on Darwin maps to the standard BSD `fsync(2)`, **not** `F_FULLFSYNC`. The call does not guarantee that disk caches are flushed. APFS honors journal durability, so metadata (the directory entry) will survive; however, if the disk has write-back cache disabled via power-loss surprise, or if the drive is USB/Thunderbolt with its own buffer, data loss is possible.

**Recommendation:** On macOS, consider `fcntl(fd, F_FULLFSYNC)` before the rename. SQLite's `PRAGMA fullfsync=ON` is available for the same reason.

### Windows (NTFS)

[Windows MoveFileExW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexa) with `MOVEFILE_REPLACE_EXISTING` is **atomic at the filesystem level**. NTFS does not require a separate directory fsync; the rename and directory update are transactional.

`os.replace` in CPython on Windows calls `MoveFileExW` with the appropriate flag. **Durability is guaranteed on NTFS without explicit directory fsync.**

### The Parent-Directory fsync Question

**POSIX/Linux:** Parent-dir fsync is the formal requirement per POSIX. However, modern ext4 journal semantics make it safe in practice.

**Windows:** Not needed. NTFS handles it transactionally.

**macOS:** Parent-dir fsync is good practice, but APFS journal will protect the directory entry. The real durability concern is **file data** flush (F_FULLFSYNC), not directory metadata.

---

## 2. `filelock` Library Behavior

### POSIX: flock vs fcntl

The `filelock` Python library on POSIX uses **flock(2)** by default (not fcntl locks). Per [flock(2) man page](https://man7.org/linux/man-pages/man2/flock.2.html):

- **Advisory, not mandatory.** A process can ignore a flock and perform I/O anyway. This is the intended behavior for a "lockfile" pattern — readers and writers voluntarily respect the lock.
- **Associated with the open file description.** If a process forks or dups the FD, the lock is shared. If the process `kill -9`s without closing, the kernel releases the lock when the FD is reclaimed.
- **No deadlock detection.** The call will block indefinitely if two processes try to convert between shared and exclusive locks incorrectly.

### NFS and SMB Quirks

**NFS (pre-2.6.12):** flock didn't work at all over NFS. Only fcntl byte-range locks worked.

**NFS (2.6.12+):** flock is now emulated as fcntl byte-range locks on the entire file. This means flock and fcntl locks interact, and an exclusive flock requires the file to be opened for writing. Compatibility mode (`local_lock` mount option) can disable this.

**CIFS/SMB (pre-5.5):** flock was not propagated over SMB; remote clients couldn't see the lock.

**CIFS/SMB (5.5+):** [Per flock(2) man page](https://man7.org/linux/man-pages/man2/flock.2.html#VERSIONS):

> "Since Linux 5.5, flock() locks are emulated with SMB byte-range locks on the entire file... the locks are not advisory anymore: any IO on a locked file will always fail with EACCES when done from a separate file descriptor."

**This is a design mismatch.** The lockfile semantics assume advisory locks (readers can coexist). SMB5.5+ changed them to mandatory locks, which will reject even read-only access from competing processes.

### Stale Lock Recovery

If the holder process is `kill -9`'d without releasing the lock:

- **POSIX (local FS):** The kernel reclaims the FD and automatically releases the lock when all references to the open file description are closed. This is immediate or very fast (depends on OS cleanup).
- **NFS:** The server eventually times out the lock (often 30-60 seconds) or relies on the client to send a release on reconnect.
- **SMB:** Behavior depends on the server and lock implementation; typical timeout is similar to NFS.

**For a desktop app on a local filesystem, stale locks are not a practical concern.** For cloud-sync folders, they can cause extended lockouts.

### Windows (via `filelock`)

On Windows, `filelock` uses `msvcrt.locking` or `LockFileEx`, which are **mandatory locks**. Only the locking process (or a process with sufficient permissions) can access the file.

---

## 3. Case-Insensitive Filesystems + Unicode Normalization

### Platform Defaults

- **macOS (APFS):** Case-insensitive by default (case-preserving). Historically HFS+ used NFD normalization; modern APFS does not normalize but compares case-insensitively using precomposed forms.
- **Windows (NTFS):** Case-insensitive (case-preserving). Path component limit: 255 characters; full path limit: 260 without `\\?\` prefix, 32K with it.
- **Linux (ext4):** Case-sensitive. Path component limit: 255 bytes; full path limit: 4096.

### The Code's NFC Normalization

The codebase normalizes folder names and filenames to NFC on every write (per D4). 

**Interaction with case-insensitive FS:** If a user writes a folder `[42] Morning`, it's normalized to NFC and stored. Later, if they create `[42] morning` (lowercase), `os.rename()` on macOS/Windows will see this as the same file (case-insensitive) and silently replace it, returning success. The user's folder structure changes unexpectedly. **This is a design issue, not a code bug**, but the code should be aware that case folding and normalization interact.

**Cloud sync (Dropbox, iCloud, OneDrive):** These services often renormalize to NFD (Dropbox) or NFD-then-NFC (OneDrive/iCloud). If the logbook is on a synced folder, the files may be renormalized on sync, potentially breaking the code's assumptions about exact byte-for-byte matches during XML read.

### Path-Length Limits

- **NTFS 260-char limit without `\\?\`:** The code's `sanitize_filename` caps at 255 (good), but `sanitize_folder_name` has no cap. A user with a 200-char jump title will generate a 200+ character folder name. On Windows without the `\\?\` prefix, if the full path exceeds 260, mkdir will fail with `FileNotFoundError` (or similar). The code does not handle this.
- **APFS ~1024 limit:** Unlikely to hit in practice for a logbook.
- **ext4 4096 total:** Unlikely to hit.

**Recommendation:** Add a check or comment on path-length limits before mkdir. Especially important on Windows for very long jump titles.

---

## 4. SQLite WAL on Desktop Filesystems

### WAL Files and Cloud Sync

SQLite's WAL mode uses three files: the main DB, `-wal` (write-ahead log), and `-shm` (shared memory index). These must stay in sync.

**Dropbox, OneDrive, iCloud:** When syncing, these services may:
1. Download the main DB from the cloud.
2. Later (or in parallel) download the WAL and SHM files.
3. If the app is reading/writing during sync, WAL and SHM can be out of sync with the DB on disk.

**Known issue:** Dropbox explicitly recommends against putting SQLite databases with WAL mode in synced folders. The desync can cause corruption or silent data loss.

### WAL + `PRAGMA synchronous=NORMAL` on macOS

Per SQLite docs and [Apple's fsync(2) documentation](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/fsync.2.html), SQLite's `synchronous=NORMAL` does **not** guarantee that the WAL is flushed to disk after every transaction — it relies on OS buffering. On macOS, if fsync doesn't use F_FULLFSYNC, a crash can cause unrecovered transactions to be lost. SQLite has a `fullfsync` pragma to address this.

### NFS and SMB Considerations

WAL mode on NFS/SMB is **not recommended** by SQLite. The shared memory file (`-shm`) doesn't work reliably over network filesystems because multiple machines may need to access the same file, and the memory-mapped regions can desync. SQLite will fall back to locking or may corrupt silently.

**If the user opens the logbook from a network drive or cloud-synced folder, WAL mode will silently degrade or fail.**

---

## Citations

- [POSIX.1-2017 rename()](https://pubs.opengroup.org/onlinepubs/9699919799/functions/rename.html)
- [Linux fsync(2) man page](https://man7.org/linux/man-pages/man2/fsync.2.html)
- [Apple fsync(2) documentation](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/fsync.2.html)
- [Linux flock(2) man page](https://man7.org/linux/man-pages/man2/flock.2.html)
- [Linux flock(1) man page](https://man7.org/linux/man-pages/man1/flock.1.html)
- [SQLite WAL mode documentation](https://www.sqlite.org/wal.html)
- [SQLite PRAGMA fullfsync documentation](https://www.sqlite.org/pragma.html#pragma_fullfsync)
- [CPython os.replace() documentation](https://docs.python.org/3/library/os.html#os.replace)
- [filelock Python library documentation](https://py-filelock.readthedocs.io/)
- IEEE Std 1003.1-2017 (POSIX.1-2017), XBD File System Interface
