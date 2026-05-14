"""Single-instance lock per logbook folder (D9).

Uses the `filelock` library: fcntl on POSIX, msvcrt on Windows. The lock
is advisory — read-only tools (another shell, a backup script) can coexist.
Only concurrent writers are blocked.
"""
from __future__ import annotations

from pathlib import Path

from filelock import FileLock, Timeout

LOCK_FILENAME = ".logbook.lock"


class LockError(RuntimeError):
    """Raised when another instance already holds the logbook lock."""


def acquire(logbook_root: Path, timeout: float = 0.5) -> FileLock:
    """Acquire an exclusive lock on `<logbook_root>/.logbook.lock`.

    Returns the held FileLock. The caller is responsible for releasing it
    (typically via `try/finally` around the main event loop). Raises
    LockError if another process holds the lock.
    """
    logbook_root = Path(logbook_root)
    logbook_root.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(logbook_root / LOCK_FILENAME), timeout=timeout)
    try:
        lock.acquire()
    except Timeout as e:
        raise LockError(
            f"Another skydive-logbook instance is already using {logbook_root}. "
            "Close it before starting a new one."
        ) from e
    return lock
