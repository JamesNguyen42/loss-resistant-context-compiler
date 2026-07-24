"""Crash-resistant local file replacement helpers."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path


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


def atomic_write_text(path: str | Path, value: str) -> None:
    """Install complete UTF-8 text with same-directory atomic replacement."""

    if not isinstance(value, str):
        raise TypeError("atomic text output must be a string")
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode: int | None = None
    try:
        existing_stat = output_path.stat()
    except FileNotFoundError:
        pass
    else:
        if stat.S_ISREG(existing_stat.st_mode):
            existing_mode = stat.S_IMODE(existing_stat.st_mode)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".ctxc-",
        suffix=".tmp",
        dir=output_path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        if existing_mode is not None:
            os.chmod(temporary_path, existing_mode)
        stream = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
        descriptor = -1
        with stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, output_path)
        fsync_directory(output_path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)
