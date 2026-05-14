"""Subprocess-kill test for the single-instance lockfile (D9).

TEST-7 (audit 2026-04-29): pin that a SIGKILL'd ``backend.main`` does
not leak the logbook lock. The kernel releases ``flock(2)`` advisory
locks when the holding process dies — by SIGTERM, by ``exit()``, by
SIGKILL, by any other path — but pinning the SIGKILL case is what
matters operationally: a clean shutdown's ``finally`` hooks would
release the lock anyway; SIGKILL skips them all.

Mechanic:

  1. Pick an ephemeral port (bind/close a transient socket, take the
     OS-assigned port number).
  2. Spawn ``python -m backend.main`` with ``SKYDIVE_LOGBOOK_ROOT``
     pointed at a fresh tmp dir and ``SKYDIVE_BIND_PORT`` pointed at
     the ephemeral port.
  3. Poll the port for readiness (TCP connect) until uvicorn is
     accepting connections — proves main.py made it past the lockfile
     acquire AND past the uvicorn boot.
  4. ``SIGKILL`` the child. ``proc.wait`` confirms it's dead.
  5. Acquire the lock from this process via the public
     ``backend.storage.lockfile.acquire`` API. Success here proves
     the kernel released the dead process's lock.

POSIX-only: the test depends on ``signal.SIGKILL`` semantics. Windows
has no exact equivalent (TerminateProcess is the closest, and
``filelock`` on Windows uses ``msvcrt`` not ``flock``). The CI matrix
runs Ubuntu and macOS unconditionally; Windows skips this test.

Refs:
  - flock(2) "If a process holding a lock dies, ... the lock is
    released" — POSIX 2024 §3.272 ("Lock"):
    https://pubs.opengroup.org/onlinepubs/9799919799/functions/flock.html
  - ``filelock`` library uses fcntl on POSIX, msvcrt on Windows:
    https://py-filelock.readthedocs.io/en/latest/
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from backend.storage.lockfile import LOCK_FILENAME, acquire

# Skip the entire module on Windows — the test depends on POSIX
# SIGKILL semantics.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="SIGKILL is POSIX-only; Windows uses different lockfile primitives",
)


def _ephemeral_port() -> int:
    """Ask the kernel for a free TCP port, then close the socket.

    The brief race between close() and the child binding is acceptable
    for a test; production code uses 0 for ephemeral binding directly.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_port(host: str, port: int, timeout: float = 15.0) -> None:
    """Block until ``host:port`` accepts a TCP connection or timeout fires."""
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError as exc:
            last_err = exc
            time.sleep(0.1)
    raise TimeoutError(
        f"port {host}:{port} did not accept connections within {timeout}s "
        f"(last error: {last_err})"
    )


def _spawn_main(
    logbook_root: Path, port: int
) -> subprocess.Popen[str]:
    """Spawn ``python -m backend.main`` pointing at the test root."""
    env = {
        **os.environ,
        "SKYDIVE_LOGBOOK_ROOT": str(logbook_root),
        "SKYDIVE_BIND_HOST": "127.0.0.1",
        "SKYDIVE_BIND_PORT": str(port),
        # Quiet the child's logs; the test doesn't read them.
        "SKYDIVE_LOG_LEVEL": "WARNING",
        # PYTHONPATH so the child resolves ``backend`` regardless of
        # cwd (mirrors the crash-harness pattern in
        # ``test_crash_recovery.py``).
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
    }
    return subprocess.Popen(
        [sys.executable, "-m", "backend.main"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


class TestSigkillReleasesLock:
    def test_lock_is_acquirable_after_sigkill(self, tmp_path: Path) -> None:
        root = tmp_path / "logbook"
        port = _ephemeral_port()

        proc = _spawn_main(root, port)
        try:
            try:
                _wait_for_port("127.0.0.1", port)
            except TimeoutError as exc:
                # Surface stderr if the child never came up — most
                # likely a bootstrap failure or already-bound port.
                proc.kill()
                stdout, stderr = proc.communicate(timeout=5)
                pytest.fail(
                    f"backend.main never bound the port: {exc}\n"
                    f"stdout:\n{stdout}\nstderr:\n{stderr}"
                )

            # Sanity: while the child is alive, the lock IS held —
            # an in-process acquire times out. Belt-and-braces against
            # a future refactor that changes the lock acquisition
            # ordering inside main.py.
            from backend.storage.lockfile import LockError

            with pytest.raises(LockError):
                acquire(root, timeout=0.2)

            # Pull the trigger.
            proc.send_signal(signal.SIGKILL)
            rc = proc.wait(timeout=5)
            # SIGKILL → exit by signal 9 → returncode == -9 on POSIX.
            assert rc == -signal.SIGKILL, (
                f"expected SIGKILL exit, got returncode={rc}"
            )

            # The lock file remains on disk — filelock does not delete
            # it on release; only the kernel-held flock state goes
            # away. That distinction is documented in py-filelock and
            # is what allows a graceful retry without permission
            # juggling.
            assert (root / LOCK_FILENAME).exists()

            # The actual invariant: a fresh acquire from this process
            # succeeds. If the kernel hadn't released the dead
            # process's lock, this would time out into LockError.
            lock = acquire(root, timeout=2.0)
            try:
                assert lock.is_locked
            finally:
                lock.release()
        finally:
            # Belt-and-braces — if the test bailed before SIGKILL,
            # don't leak a uvicorn process.
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
