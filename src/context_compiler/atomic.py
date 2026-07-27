"""Crash-resistant local file replacement helpers."""

from __future__ import annotations

import os
import secrets
import stat
import tempfile
from pathlib import Path

from .path_safety import (
    ParentDirectoryGuard,
    _is_link_or_reparse,
    supports_atomic_directory_fds,
)


def fsync_directory(path: str | Path) -> None:
    """Persist a directory entry update when the host exposes that primitive."""

    if os.name == "nt":
        return
    directory = Path(path)
    descriptor = os.open(
        directory,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_text(
    path: str | Path,
    value: str,
    *,
    overwrite: bool = True,
) -> None:
    """Install complete UTF-8 text atomically in the destination directory."""

    if not isinstance(value, str):
        raise TypeError("atomic text output must be a string")
    if not isinstance(overwrite, bool):
        raise TypeError("overwrite must be a boolean")
    output_path = Path(path)
    parent_guard = ParentDirectoryGuard.prepare(
        output_path,
        label="atomic output",
    )
    output_path = parent_guard.target

    with parent_guard.pinned_parent() as parent_descriptor:
        use_directory_fd = (
            parent_descriptor is not None
            and supports_atomic_directory_fds()
        )
        try:
            if use_directory_fd:
                existing_stat = os.stat(
                    output_path.name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
            else:
                existing_stat = output_path.lstat()
        except FileNotFoundError:
            existing_stat = None
        if existing_stat is not None and (
            not stat.S_ISREG(existing_stat.st_mode)
            or _is_link_or_reparse(existing_stat)
        ):
            raise ValueError(
                f"atomic output path must be a regular file when it exists: "
                f"{output_path}"
            )
        existing_mode = (
            stat.S_IMODE(existing_stat.st_mode)
            if overwrite and existing_stat is not None
            else None
        )

        descriptor = -1
        temporary_name: str
        temporary_path: Path
        temporary_identity: tuple[int, int] | None = None
        temporary_present = False
        if use_directory_fd:
            flags = (
                os.O_CREAT
                | os.O_EXCL
                | os.O_WRONLY
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOINHERIT", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            for _attempt in range(128):
                temporary_name = f".ctxc-{secrets.token_hex(8)}.tmp"
                try:
                    descriptor = os.open(
                        temporary_name,
                        flags,
                        0o600,
                        dir_fd=parent_descriptor,
                    )
                except FileExistsError:
                    continue
                break
            else:
                raise FileExistsError(
                    f"could not allocate a unique atomic temporary file in "
                    f"{output_path.parent}"
                )
            temporary_path = output_path.parent / temporary_name
        else:
            descriptor, raw_temporary_name = tempfile.mkstemp(
                prefix=".ctxc-",
                suffix=".tmp",
                dir=output_path.parent,
            )
            temporary_path = Path(raw_temporary_name)
            temporary_name = temporary_path.name
        try:
            os.set_inheritable(descriptor, False)
            temporary_stat = os.fstat(descriptor)
            temporary_identity = (
                temporary_stat.st_dev,
                temporary_stat.st_ino,
            )
            temporary_present = True
            if existing_mode is not None:
                if hasattr(os, "fchmod"):
                    os.fchmod(descriptor, existing_mode)
                else:
                    os.chmod(temporary_path, existing_mode)
            stream = os.fdopen(
                descriptor,
                "w",
                encoding="utf-8",
                newline="\n",
            )
            descriptor = -1
            with stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            parent_guard.verify()
            if use_directory_fd:
                if overwrite:
                    os.replace(
                        temporary_name,
                        output_path.name,
                        src_dir_fd=parent_descriptor,
                        dst_dir_fd=parent_descriptor,
                    )
                    temporary_present = False
                else:
                    os.link(
                        temporary_name,
                        output_path.name,
                        src_dir_fd=parent_descriptor,
                        dst_dir_fd=parent_descriptor,
                        follow_symlinks=False,
                    )
                    os.unlink(
                        temporary_name,
                        dir_fd=parent_descriptor,
                    )
                    temporary_present = False
                os.fsync(parent_descriptor)
            else:
                if overwrite:
                    os.replace(temporary_path, output_path)
                    temporary_present = False
                else:
                    os.link(temporary_path, output_path)
                    temporary_path.unlink()
                    temporary_present = False
                fsync_directory(output_path.parent)
            parent_guard.verify()
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary_present:
                if use_directory_fd:
                    try:
                        final_temporary_stat = os.stat(
                            temporary_name,
                            dir_fd=parent_descriptor,
                            follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        pass
                    else:
                        final_identity = (
                            final_temporary_stat.st_dev,
                            final_temporary_stat.st_ino,
                        )
                        if final_identity == temporary_identity:
                            os.unlink(
                                temporary_name,
                                dir_fd=parent_descriptor,
                            )
                else:
                    try:
                        final_temporary_stat = temporary_path.lstat()
                    except FileNotFoundError:
                        pass
                    else:
                        final_identity = (
                            final_temporary_stat.st_dev,
                            final_temporary_stat.st_ino,
                        )
                        if final_identity == temporary_identity:
                            temporary_path.unlink()
