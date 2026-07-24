"""Cross-platform advisory file locking for single-host archive writers."""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

_LOCK_BUSY_ERRNOS = {
    errno.EACCES,
    errno.EAGAIN,
    errno.EDEADLK,
}


def close_lock_file(
    descriptor: int,
    *,
    prior_error: BaseException | None = None,
) -> None:
    """Close a lock descriptor without hiding an exception already in flight."""

    try:
        os.close(descriptor)
    except OSError as exc:
        if prior_error is None:
            raise
        prior_error.add_note(
            f"archive lock descriptor close also failed ({type(exc).__name__}: {exc})"
        )


def open_lock_file(path: str | Path) -> int:
    """Open a persistent, non-inheritable regular file used for advisory locking."""

    lock_path = Path(path)
    flags = (
        os.O_CREAT
        | os.O_RDWR
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError(f"lock path is not a regular file: {lock_path}")
        os.set_inheritable(descriptor, False)
        return descriptor
    except BaseException as exc:
        close_lock_file(descriptor, prior_error=exc)
        raise


def try_lock_file(descriptor: int) -> bool:
    """Try to take an exclusive one-byte lock without waiting."""

    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in _LOCK_BUSY_ERRNOS:
                return False
            raise
        return True

    import fcntl

    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in _LOCK_BUSY_ERRNOS:
            return False
        raise
    return True
