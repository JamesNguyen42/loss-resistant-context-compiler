"""Cross-platform advisory file locking for single-host archive writers."""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

from .path_safety import ParentDirectoryGuard, _is_link_or_reparse

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

    parent_guard = ParentDirectoryGuard.capture(
        path,
        label="archive lock",
    )
    lock_path = parent_guard.target
    base_flags = (
        os.O_RDWR
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        with parent_guard.pinned_parent() as parent_descriptor:
            for attempt in range(3):
                try:
                    if parent_descriptor is None:
                        candidate_stat = lock_path.lstat()
                    else:
                        candidate_stat = os.stat(
                            lock_path.name,
                            dir_fd=parent_descriptor,
                            follow_symlinks=False,
                        )
                except FileNotFoundError:
                    candidate_stat = None
                if candidate_stat is not None and (
                    not stat.S_ISREG(candidate_stat.st_mode)
                    or _is_link_or_reparse(candidate_stat)
                    or candidate_stat.st_nlink != 1
                ):
                    raise OSError(
                        f"lock path is not a single-link regular file: "
                        f"{lock_path}"
                    )
                flags = base_flags | os.O_CREAT
                if candidate_stat is None:
                    flags |= os.O_EXCL
                try:
                    if parent_descriptor is None:
                        descriptor = os.open(lock_path, flags, 0o600)
                    else:
                        descriptor = os.open(
                            lock_path.name,
                            flags,
                            0o600,
                            dir_fd=parent_descriptor,
                        )
                except FileExistsError:
                    if attempt + 1 < 3:
                        continue
                    raise
                opened_stat = os.fstat(descriptor)
                if opened_stat.st_nlink == 0:
                    close_lock_file(descriptor)
                    descriptor = -1
                    if attempt + 1 < 3:
                        continue
                    raise OSError(
                        f"lock path changed while opening: {lock_path}"
                    )
                if (
                    not stat.S_ISREG(opened_stat.st_mode)
                    or _is_link_or_reparse(opened_stat)
                    or opened_stat.st_nlink != 1
                ):
                    raise OSError(
                        f"lock path is not a single-link regular file: "
                        f"{lock_path}"
                    )
                if candidate_stat is not None and (
                    candidate_stat.st_dev,
                    candidate_stat.st_ino,
                ) != (
                    opened_stat.st_dev,
                    opened_stat.st_ino,
                ):
                    close_lock_file(descriptor)
                    descriptor = -1
                    if attempt + 1 < 3:
                        continue
                    raise OSError(
                        f"lock path changed while opening: {lock_path}"
                    )
                try:
                    if parent_descriptor is None:
                        current_stat = lock_path.lstat()
                    else:
                        current_stat = os.stat(
                            lock_path.name,
                            dir_fd=parent_descriptor,
                            follow_symlinks=False,
                        )
                except FileNotFoundError:
                    current_stat = None
                final_opened_stat = os.fstat(descriptor)
                if (
                    current_stat is None
                    or not stat.S_ISREG(current_stat.st_mode)
                    or _is_link_or_reparse(current_stat)
                    or current_stat.st_nlink != 1
                    or final_opened_stat.st_nlink != 1
                    or (
                        current_stat.st_dev,
                        current_stat.st_ino,
                    )
                    != (
                        final_opened_stat.st_dev,
                        final_opened_stat.st_ino,
                    )
                ):
                    close_lock_file(descriptor)
                    descriptor = -1
                    if attempt + 1 < 3:
                        continue
                    raise OSError(
                        f"lock path changed while opening: {lock_path}"
                    )
                parent_guard.verify()
                os.set_inheritable(descriptor, False)
                return descriptor
            raise OSError(f"lock path changed while opening: {lock_path}")
    except BaseException as exc:
        if descriptor >= 0:
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
