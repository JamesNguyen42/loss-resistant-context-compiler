"""Ancestor-directory validation for security-sensitive local paths."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path


class PathBoundaryError(ValueError):
    """Raised when a target's ancestor-directory boundary is unsafe."""


def _absolute_lexical_path(path: str | Path) -> Path:
    # ``resolve`` would follow links before they can be rejected. ``abspath``
    # removes ambiguous ``.``/``..`` components without dereferencing any
    # filesystem object.
    return Path(os.path.abspath(os.fspath(path)))


def _identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _is_link_or_reparse(value: os.stat_result) -> bool:
    if stat.S_ISLNK(value.st_mode):
        return True
    attributes = getattr(value, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


@dataclass(frozen=True, slots=True)
class _DirectorySnapshot:
    path: Path
    identity: tuple[int, int]


@dataclass(frozen=True, slots=True)
class ParentDirectoryGuard:
    """Pin the lexical ancestor chain of one target path.

    POSIX callers can additionally hold ``pinned_parent`` and perform
    descriptor-relative operations inside the exact parent directory. On
    platforms without directory-relative file APIs, before/after validation
    still detects a changed ancestor before a result is accepted.
    """

    target: Path
    label: str
    directories: tuple[_DirectorySnapshot, ...]

    @classmethod
    def capture(
        cls,
        path: str | Path,
        *,
        label: str,
    ) -> ParentDirectoryGuard:
        if not isinstance(label, str) or not label:
            raise TypeError("path boundary label must be a non-empty string")
        target = _absolute_lexical_path(path)
        parent = target.parent
        paths = list(reversed((parent, *parent.parents)))
        snapshots = tuple(
            _snapshot_directory(directory, label=label)
            for directory in paths
        )
        guard = cls(target=target, label=label, directories=snapshots)
        # Detect a substitution that happened while the chain was captured.
        guard.verify()
        return guard

    @classmethod
    def prepare(
        cls,
        path: str | Path,
        *,
        label: str,
    ) -> ParentDirectoryGuard:
        """Create missing parents without accepting an existing link boundary."""

        target = _absolute_lexical_path(path)
        cursor = target.parent
        missing_names: list[str] = []
        while True:
            try:
                _snapshot_directory(cursor, label=label)
            except FileNotFoundError:
                if cursor.parent == cursor:
                    raise PathBoundaryError(
                        f"{label} has no existing ancestor directory: {cursor}"
                    ) from None
                missing_names.append(cursor.name)
                cursor = cursor.parent
                continue
            break
        if not missing_names:
            return cls.capture(target, label=label)

        base_guard = cls.capture(
            cursor / ".ctxc-parent-boundary",
            label=label,
        )
        with base_guard.pinned_parent() as parent_descriptor:
            if (
                parent_descriptor is not None
                and supports_directory_creation_fds()
            ):
                current_descriptor = os.dup(parent_descriptor)
                try:
                    os.set_inheritable(current_descriptor, False)
                    for name in reversed(missing_names):
                        with suppress(FileExistsError):
                            os.mkdir(
                                name,
                                0o777,
                                dir_fd=current_descriptor,
                            )
                        flags = (
                            os.O_RDONLY
                            | getattr(os, "O_BINARY", 0)
                            | getattr(os, "O_CLOEXEC", 0)
                            | getattr(os, "O_NOINHERIT", 0)
                            | getattr(os, "O_DIRECTORY", 0)
                            | getattr(os, "O_NOFOLLOW", 0)
                        )
                        child_descriptor = os.open(
                            name,
                            flags,
                            dir_fd=current_descriptor,
                        )
                        try:
                            inspected = os.fstat(child_descriptor)
                            if (
                                not stat.S_ISDIR(inspected.st_mode)
                                or _is_link_or_reparse(inspected)
                                or inspected.st_ino == 0
                            ):
                                raise PathBoundaryError(
                                    f"{label} created parent is not a stable "
                                    f"directory: {name}"
                                )
                            os.set_inheritable(child_descriptor, False)
                        except BaseException:
                            os.close(child_descriptor)
                            raise
                        os.close(current_descriptor)
                        current_descriptor = child_descriptor
                finally:
                    os.close(current_descriptor)
            else:
                current_path = cursor
                for name in reversed(missing_names):
                    base_guard.verify()
                    current_path = current_path / name
                    with suppress(FileExistsError):
                        current_path.mkdir()
                    _snapshot_directory(current_path, label=label)
        return cls.capture(target, label=label)

    @property
    def parent(self) -> Path:
        return self.target.parent

    @property
    def parent_identity(self) -> tuple[int, int]:
        return self.directories[-1].identity

    def verify(self) -> None:
        for snapshot in self.directories:
            current = _snapshot_directory(
                snapshot.path,
                label=self.label,
            )
            if current.identity != snapshot.identity:
                raise PathBoundaryError(
                    f"{self.label} ancestor directory changed during access: "
                    f"{snapshot.path}"
                )

    @contextmanager
    def pinned_parent(self) -> Iterator[int | None]:
        """Yield an exact parent directory descriptor where the OS supports it."""

        self.verify()
        if not supports_directory_fds():
            yield None
            self.verify()
            return

        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOINHERIT", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(self.parent, flags)
        except OSError as exc:
            raise PathBoundaryError(
                f"could not pin {self.label} parent directory: {self.parent}"
            ) from exc
        primary_error: BaseException | None = None
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(opened.st_mode)
                or _is_link_or_reparse(opened)
                or _identity(opened) != self.parent_identity
            ):
                raise PathBoundaryError(
                    f"{self.label} parent directory changed while opening: "
                    f"{self.parent}"
                )
            os.set_inheritable(descriptor, False)
            yield descriptor
            self.verify()
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            try:
                os.close(descriptor)
            except OSError as exc:
                if primary_error is None:
                    raise
                primary_error.add_note(
                    "parent directory descriptor close also failed "
                    f"({type(exc).__name__}: {exc})"
                )


def _snapshot_directory(
    path: Path,
    *,
    label: str,
) -> _DirectorySnapshot:
    try:
        inspected = path.lstat()
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise PathBoundaryError(
            f"could not inspect {label} ancestor directory: {path}"
        ) from exc
    if _is_link_or_reparse(inspected):
        raise PathBoundaryError(
            f"{label} ancestor directory must not be a link or reparse point: "
            f"{path}"
        )
    if not stat.S_ISDIR(inspected.st_mode):
        raise PathBoundaryError(
            f"{label} ancestor path must be a directory: {path}"
        )
    identity = _identity(inspected)
    if identity[1] == 0:
        raise PathBoundaryError(
            f"{label} ancestor directory has no stable file identity: {path}"
        )
    return _DirectorySnapshot(path=path, identity=identity)


def supports_directory_fds() -> bool:
    """Return whether exact-parent descriptor-relative access is available."""

    return (
        os.name != "nt"
        and os.open in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
    )


def supports_directory_creation_fds() -> bool:
    """Return whether child directories can be created below a pinned parent."""

    return supports_directory_fds() and os.mkdir in os.supports_dir_fd


def supports_atomic_directory_fds() -> bool:
    """Return whether all atomic install operations accept directory fds."""

    return (
        supports_directory_fds()
        and os.replace in os.supports_dir_fd
        and os.link in os.supports_dir_fd
        and os.unlink in os.supports_dir_fd
    )
