"""Coordinator-only SWE-bench patch composition preflight.

This module applies candidate and hidden *text* patches only to independently
copied temporary trees.  Git binary patches are rejected because this local
preflight has no cross-platform native memory or filesystem quota.  It never
executes candidate code or an official grader, and it never mutates the
verified prepared repository used as its source.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from context_compiler.atomic import atomic_write_text

from .json_io import StrictJsonError, StrictJsonLimits, load_strict_json_file
from .literal_process import (
    LiteralProcessError,
    LiteralProcessLimits,
    run_literal_argv,
)
from .swebench import (
    SweBenchError,
    VerifiedSweBenchSource,
    _revalidate_verified_source,
)
from .swebench_repository import (
    PreparedSweBenchRepository,
    RepositoryPreparationError,
    RepositoryPreparationLimits,
    VerifiedBareMirror,
    decode_bare_mirror,
    decode_repository_preparation,
    verify_prepared_repository,
)

PATCH_COMPOSITION_SCHEMA = "ctxc-swebench-patch-composition-0.1"

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_MAX_TIMEOUT_SECONDS = 7 * 24 * 60 * 60
_MAX_STREAM_BYTES = 64 * 1024 * 1024
_MAX_PATCH_BYTES = 128 * 1024 * 1024
_MAX_FILES = 1_000_000
_MAX_PATH_BYTES = 4096
_MAX_DEPTH = 256
_MAX_FILE_BYTES = 4 * 1024 * 1024 * 1024
_MAX_TOTAL_BYTES = 16 * 1024 * 1024 * 1024
_MAX_DOCUMENT_BYTES = 512 * 1024 * 1024
_COPY_CHUNK_BYTES = 1024 * 1024
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_GIT_APPLY_TAIL = (
    "apply",
    "--no-index",
    "--recount",
    "--whitespace=nowarn",
    "--",
)
_GIT_BINARY_PATCH_MARKER = b"GIT binary patch"
_PORTABLE_NEW_FILE_MODE = b"new file mode 100644"
_PORTABLE_DELETED_FILE_MODE = b"deleted file mode 100644"
_FAILURE_DISPOSITION = "preflight-exception-not-a-cohort-result"
_CONTAINMENT_SCOPES = {
    "posix-session-process-group-escapable",
    "windows-job-process-and-descendants",
}
_RESERVED_WINDOWS_NAMES = {
    "aux",
    "clock$",
    "con",
    "conin$",
    "conout$",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
    "com¹",
    "com²",
    "com³",
    "lpt¹",
    "lpt²",
    "lpt³",
}


class PatchCompositionError(ValueError):
    """A local preflight failed without becoming a cohort result.

    ``stage`` and ``disposition`` let an outer complete-denominator ledger map
    the failure without parsing prose.  This exception is deliberately not a
    benchmark-row disposition by itself.
    """

    def __init__(self, message: str, *, stage: str = "preflight-validation") -> None:
        super().__init__(message)
        self.stage = stage
        self.disposition = _FAILURE_DISPOSITION


def _bounded_integer(
    value: object,
    *,
    label: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise PatchCompositionError(f"{label} must be an integer from {minimum} through {maximum}")
    return value


def _positive_number(value: object, *, label: str, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PatchCompositionError(f"{label} must be a positive finite number")
    try:
        converted = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise PatchCompositionError(f"{label} must be a positive finite number") from exc
    if not math.isfinite(converted) or not 0 < converted <= maximum:
        raise PatchCompositionError(f"{label} must be no greater than {maximum}")
    return converted


@dataclass(frozen=True, slots=True)
class PatchCompositionLimits:
    """Finite text-patch, stream, retained-tree, and evidence bounds.

    Tree bounds are verified before and after Git, not enforced as an OS quota.
    Git binary patches are therefore outside this checkpoint's accepted input.
    """

    timeout_seconds: float = 60.0
    max_stdout_bytes: int = 1_000_000
    max_stderr_bytes: int = 1_000_000
    max_patch_bytes: int = 16 * 1024 * 1024
    max_files: int = 100_000
    max_path_bytes: int = 1024
    max_depth: int = 64
    max_file_bytes: int = 128 * 1024 * 1024
    max_total_bytes: int = 2 * 1024 * 1024 * 1024
    max_document_bytes: int = 64 * 1024 * 1024

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "timeout_seconds",
            _positive_number(
                self.timeout_seconds,
                label="timeout_seconds",
                maximum=float(_MAX_TIMEOUT_SECONDS),
            ),
        )
        maxima = {
            "max_stdout_bytes": _MAX_STREAM_BYTES,
            "max_stderr_bytes": _MAX_STREAM_BYTES,
            "max_patch_bytes": _MAX_PATCH_BYTES,
            "max_files": _MAX_FILES,
            "max_path_bytes": _MAX_PATH_BYTES,
            "max_depth": _MAX_DEPTH,
            "max_file_bytes": _MAX_FILE_BYTES,
            "max_total_bytes": _MAX_TOTAL_BYTES,
            "max_document_bytes": _MAX_DOCUMENT_BYTES,
        }
        for name, maximum in maxima.items():
            object.__setattr__(
                self,
                name,
                _bounded_integer(
                    getattr(self, name),
                    label=name,
                    minimum=1,
                    maximum=maximum,
                ),
            )


_LIMIT_FIELDS = (
    "timeout_seconds",
    "max_stdout_bytes",
    "max_stderr_bytes",
    "max_patch_bytes",
    "max_files",
    "max_path_bytes",
    "max_depth",
    "max_file_bytes",
    "max_total_bytes",
    "max_document_bytes",
)


@dataclass(frozen=True, slots=True)
class VerifiedPatchComposition:
    """Strict self-hashed evidence for one temporary composition preflight."""

    limits: PatchCompositionLimits
    document: Mapping[str, Any]

    @property
    def retained_disjoint(self) -> bool:
        """Return only the strictly decoded retained overlap classification."""

        try:
            limits = _revalidate_limits(self.limits)
            decoded = decode_patch_composition(self.to_dict())
            if decoded["limits"] != _limits_document(limits):
                return False
        except (AttributeError, PatchCompositionError, TypeError):
            return False
        return bool(decoded["overlap"]["disjoint"])

    @property
    def claim_ready(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        return _thaw_json(self.document)


def _limits_document(limits: PatchCompositionLimits) -> dict[str, int | float]:
    return {name: getattr(limits, name) for name in _LIMIT_FIELDS}


def _revalidate_limits(value: object) -> PatchCompositionLimits:
    if not isinstance(value, PatchCompositionLimits):
        raise PatchCompositionError("limits must be PatchCompositionLimits")
    try:
        return PatchCompositionLimits(**_limits_document(value))
    except (AttributeError, TypeError, PatchCompositionError) as exc:
        raise PatchCompositionError("limits are invalid") from exc


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8", errors="strict")
    except (
        MemoryError,
        OverflowError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        raise PatchCompositionError(
            "patch-composition evidence is not canonical finite JSON"
        ) from exc


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(nested) for key, nested in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(nested) for nested in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(nested) for nested in value]
    return value


def _object(value: Any, *, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise PatchCompositionError(f"{label} fields are invalid")
    return value


def _sha256(value: Any, *, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise PatchCompositionError(f"{label} must be a lowercase SHA-256")
    return value


def _validate_patch_headers(lines: list[bytes], *, label: str) -> None:
    sections: list[list[bytes]] = []
    current: list[bytes] | None = None
    for line in lines:
        if line.startswith(b"diff --git "):
            if current is not None:
                sections.append(current)
            current = [line]
        elif current is not None:
            current.append(line)
    if current is not None:
        sections.append(current)

    for section in sections:
        for line in section:
            if line == _GIT_BINARY_PATCH_MARKER or (
                line.startswith(b"Binary files ") and line.endswith(b" differ")
            ):
                raise PatchCompositionError(
                    f"{label} contains a Git binary patch indicator, which this "
                    "uncontained preflight rejects",
                    stage="patch-input-validation",
                )
            if line.startswith((b"copy from ", b"copy to ")):
                raise PatchCompositionError(
                    f"{label} contains an extended copy header, which this "
                    "uncontained preflight rejects",
                    stage="patch-input-validation",
                )
            if line.startswith((b"rename from ", b"rename to ")):
                raise PatchCompositionError(
                    f"{label} contains an extended rename header, which this "
                    "uncontained preflight rejects",
                    stage="patch-input-validation",
                )
            if line.startswith((b"old mode ", b"new mode ")):
                raise PatchCompositionError(
                    f"{label} contains a non-portable mode-change header",
                    stage="patch-input-validation",
                )
            if line.startswith(b"new file mode ") and line != _PORTABLE_NEW_FILE_MODE:
                raise PatchCompositionError(
                    f"{label} contains a non-portable new-file mode",
                    stage="patch-input-validation",
                )
            if line.startswith(b"deleted file mode ") and line != _PORTABLE_DELETED_FILE_MODE:
                raise PatchCompositionError(
                    f"{label} contains a non-portable deleted-file mode",
                    stage="patch-input-validation",
                )

        creates_file = b"--- /dev/null" in section
        deletes_file = b"+++ /dev/null" in section
        has_new_mode = _PORTABLE_NEW_FILE_MODE in section
        has_deleted_mode = _PORTABLE_DELETED_FILE_MODE in section
        if creates_file is not has_new_mode:
            raise PatchCompositionError(
                f"{label} new-file headers do not declare exact mode 100644",
                stage="patch-input-validation",
            )
        if deletes_file is not has_deleted_mode:
            raise PatchCompositionError(
                f"{label} deleted-file headers do not declare exact mode 100644",
                stage="patch-input-validation",
            )


def _patch_bytes(value: object, *, label: str, maximum: int) -> bytes:
    if type(value) is not str:
        raise PatchCompositionError(f"{label} must be text")
    if len(value) > maximum:
        raise PatchCompositionError(f"{label} exceeds its byte limit")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise PatchCompositionError(f"{label} is not strict UTF-8") from exc
    if len(encoded) > maximum:
        raise PatchCompositionError(f"{label} exceeds its byte limit")
    if b"\0" in encoded:
        raise PatchCompositionError(f"{label} contains a NUL byte")
    lines = encoded.splitlines()
    for marker in (
        b"new file mode 120000",
        b"old mode 120000",
        b"new mode 120000",
        b"new file mode 160000",
        b"old mode 160000",
        b"new mode 160000",
    ):
        if marker in lines:
            raise PatchCompositionError(
                f"{label} requests a link or submodule mode",
                stage="patch-input-validation",
            )
    _validate_patch_headers(lines, label=label)
    return encoded


def _portable_relative_path(path: str, limits: PatchCompositionLimits) -> str:
    if type(path) is not str:
        raise PatchCompositionError("workspace path must be text")
    if not path or path != unicodedata.normalize("NFC", path):
        raise PatchCompositionError("workspace path is empty or non-NFC")
    if "\\" in path or "\0" in path:
        raise PatchCompositionError("workspace path contains an unsafe separator")
    if len(path.encode("utf-8", errors="strict")) > limits.max_path_bytes:
        raise PatchCompositionError("workspace path exceeds its byte limit")
    pure = PurePosixPath(path)
    if pure.is_absolute() or len(pure.parts) > limits.max_depth:
        raise PatchCompositionError("workspace path is absolute or too deep")
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise PatchCompositionError("workspace path contains traversal")
    for part in pure.parts:
        if part.endswith((" ", ".")) or any(
            ord(char) < 32
            or ord(char) == 127
            or unicodedata.category(char) in {"Cc", "Cf", "Cs"}
            or char in '<>:"/\\|?*'
            for char in part
        ):
            raise PatchCompositionError("workspace path is not portable")
        folded = part.casefold()
        if folded in {".git", "git~1"}:
            raise PatchCompositionError("workspace path contains Git metadata")
        folded_stem = folded.partition(".")[0].rstrip(" ")
        if folded_stem in _RESERVED_WINDOWS_NAMES:
            raise PatchCompositionError("workspace path uses a reserved device name")
    return pure.as_posix()


def _is_reparse(value: os.stat_result) -> bool:
    return bool(getattr(value, "st_file_attributes", 0) & 0x400)


def _open_readonly_regular(path: Path, *, label: str) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PatchCompositionError(f"{label} could not be opened") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or _is_reparse(before):
            raise PatchCompositionError(f"{label} must be a single-link regular file")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor, before


def _hash_open_file(
    descriptor: int,
    before: os.stat_result,
    *,
    label: str,
    maximum: int,
) -> tuple[int, str]:
    digest = hashlib.sha256()
    observed = 0
    while True:
        try:
            chunk = os.read(descriptor, _COPY_CHUNK_BYTES)
        except OSError as exc:
            raise PatchCompositionError(f"{label} could not be read") from exc
        if not chunk:
            break
        observed += len(chunk)
        if observed > maximum:
            raise PatchCompositionError(f"{label} exceeds its byte limit")
        digest.update(chunk)
    try:
        after = os.fstat(descriptor)
    except OSError as exc:
        raise PatchCompositionError(f"{label} could not be re-inspected") from exc

    def snapshot(item: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
        return (
            item.st_dev,
            item.st_ino,
            item.st_mode,
            item.st_nlink,
            item.st_size,
            item.st_mtime_ns,
            item.st_ctime_ns,
        )

    if snapshot(before) != snapshot(after) or observed != after.st_size:
        raise PatchCompositionError(f"{label} changed while it was read")
    return observed, digest.hexdigest()


@dataclass(frozen=True, slots=True)
class _FileState:
    path: str
    mode: str
    byte_count: int
    sha256: str

    def document(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "mode": self.mode,
            "byte_count": self.byte_count,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class _TreeManifest:
    states: tuple[_FileState, ...]
    total_bytes: int

    @property
    def by_path(self) -> dict[str, _FileState]:
        return {state.path: state for state in self.states}

    def summary(self) -> dict[str, Any]:
        entries = [state.document() for state in self.states]
        return {
            "file_count": len(entries),
            "total_bytes": self.total_bytes,
            "entries_sha256": _canonical_sha256(entries),
        }


def _scan_tree(root: Path, limits: PatchCompositionLimits) -> _TreeManifest:
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise PatchCompositionError("workspace root could not be inspected") from exc
    if not stat.S_ISDIR(root_stat.st_mode) or _is_reparse(root_stat):
        raise PatchCompositionError("workspace root is not a plain directory")
    pending = [root]
    states: list[_FileState] = []
    collisions: set[str] = set()
    total_bytes = 0
    while pending:
        directory = pending.pop()
        try:
            iterator = os.scandir(directory)
        except OSError as exc:
            raise PatchCompositionError("workspace could not be scanned") from exc
        with iterator:
            for child in iterator:
                try:
                    child_stat = child.stat(follow_symlinks=False)
                except OSError as exc:
                    raise PatchCompositionError("workspace entry could not be inspected") from exc
                if child.is_symlink() or _is_reparse(child_stat):
                    raise PatchCompositionError("workspace contains a link or reparse point")
                child_path = Path(child.path)
                relative = _portable_relative_path(child_path.relative_to(root).as_posix(), limits)
                collision = relative.casefold()
                if collision in collisions:
                    raise PatchCompositionError("workspace paths collide portably")
                collisions.add(collision)
                if stat.S_ISDIR(child_stat.st_mode):
                    pending.append(child_path)
                    continue
                if not stat.S_ISREG(child_stat.st_mode):
                    raise PatchCompositionError("workspace contains a special file")
                if len(states) >= limits.max_files:
                    raise PatchCompositionError("workspace file count exceeds its limit")
                descriptor, opened = _open_readonly_regular(child_path, label="workspace file")
                try:
                    byte_count, digest = _hash_open_file(
                        descriptor,
                        opened,
                        label="workspace file",
                        maximum=limits.max_file_bytes,
                    )
                finally:
                    os.close(descriptor)
                total_bytes += byte_count
                if total_bytes > limits.max_total_bytes:
                    raise PatchCompositionError("workspace aggregate bytes exceed their limit")
                mode = (
                    "100755"
                    if os.name != "nt" and stat.S_IMODE(opened.st_mode) & 0o111
                    else "100644"
                )
                if os.name != "nt" and stat.S_IMODE(opened.st_mode) not in {
                    0o600,
                    0o644,
                    0o700,
                    0o755,
                }:
                    raise PatchCompositionError("workspace file mode is unsafe")
                states.append(_FileState(relative, mode, byte_count, digest))
    states.sort(key=lambda item: item.path.encode("utf-8", errors="strict"))
    return _TreeManifest(tuple(states), total_bytes)


def _write_all(descriptor: int, value: bytes, *, label: str) -> None:
    offset = 0
    while offset < len(value):
        try:
            written = os.write(descriptor, value[offset:])
        except OSError as exc:
            raise PatchCompositionError(f"{label} could not be written") from exc
        if written <= 0:
            raise PatchCompositionError(f"{label} write made no progress")
        offset += written


def _copy_verified_tree(
    source: Path,
    destination: Path,
    expected: _TreeManifest,
    limits: PatchCompositionLimits,
) -> None:
    try:
        destination.mkdir(mode=0o700)
    except OSError as exc:
        raise PatchCompositionError("temporary workspace could not be created") from exc
    for state in expected.states:
        target = destination.joinpath(*PurePosixPath(state.path).parts)
        try:
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise PatchCompositionError(
                "temporary workspace directory could not be created"
            ) from exc
        source_path = source.joinpath(*PurePosixPath(state.path).parts)
        source_fd, before = _open_readonly_regular(source_path, label="prepared source file")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        try:
            destination_fd = os.open(target, flags, 0o600)
        except OSError as exc:
            os.close(source_fd)
            raise PatchCompositionError("temporary workspace file could not be created") from exc
        digest = hashlib.sha256()
        observed = 0
        try:
            while True:
                chunk = os.read(source_fd, _COPY_CHUNK_BYTES)
                if not chunk:
                    break
                observed += len(chunk)
                if observed > limits.max_file_bytes:
                    raise PatchCompositionError("prepared source file exceeds its byte limit")
                digest.update(chunk)
                _write_all(destination_fd, chunk, label="temporary workspace file")
            after = os.fstat(source_fd)
            if (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_nlink,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_nlink,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise PatchCompositionError("prepared source file changed during copy")
            if observed != state.byte_count or digest.hexdigest() != state.sha256:
                raise PatchCompositionError("prepared source file does not match its manifest")
            if os.name != "nt":
                os.fchmod(destination_fd, 0o755 if state.mode == "100755" else 0o644)
            os.fsync(destination_fd)
        finally:
            os.close(source_fd)
            os.close(destination_fd)
    observed_manifest = _scan_tree(destination, limits)
    if observed_manifest != expected:
        raise PatchCompositionError("temporary workspace does not match the prepared base tree")


def _write_patch_file(path: Path, value: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise PatchCompositionError("temporary patch file could not be created") from exc
    try:
        _write_all(descriptor, value, label="temporary patch file")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _git_environment(home: Path) -> dict[str, str]:
    owned_root = home.parent
    if not owned_root.is_absolute() or os.pathsep in str(owned_root):
        raise PatchCompositionError(
            "owned temporary root cannot be encoded as one Git discovery ceiling",
            stage="git-environment-validation",
        )
    environment = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_COUNT": "0",
        "GIT_CEILING_DIRECTORIES": str(owned_root),
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home),
        "LANG": "C",
        "LC_ALL": "C",
        "TEMP": str(home),
        "TMP": str(home),
    }
    for name in ("SYSTEMROOT", "WINDIR", "COMSPEC"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _stream_document(stream: Any) -> dict[str, Any]:
    return {
        "observed_bytes": stream.observed_bytes,
        "captured_bytes": stream.captured_bytes,
        "captured_sha256": stream.captured_sha256,
        "complete": stream.complete,
    }


def _apply_patch(
    *,
    role: str,
    git_executable: Path,
    workspace: Path,
    patch_path: Path,
    home: Path,
    limits: PatchCompositionLimits,
) -> dict[str, Any]:
    try:
        result = run_literal_argv(
            [str(git_executable), *_GIT_APPLY_TAIL, str(patch_path)],
            cwd=workspace,
            environment=_git_environment(home),
            limits=LiteralProcessLimits(
                timeout_seconds=limits.timeout_seconds,
                max_stdout_bytes=limits.max_stdout_bytes,
                max_stderr_bytes=limits.max_stderr_bytes,
            ),
        )
    except LiteralProcessError as exc:
        raise PatchCompositionError(
            f"{role} patch lifecycle failed before a result was retained",
            stage=f"{role}-apply",
        ) from exc
    if (
        result.termination_trigger is not None
        or result.execution_error is not None
        or result.cleanup_error is not None
        or not result.stdout.complete
        or not result.stderr.complete
        or result.stdout.observed_bytes != result.stdout.captured_bytes
        or result.stderr.observed_bytes != result.stderr.captured_bytes
        or result.exit_code != 0
    ):
        raise PatchCompositionError(
            f"{role} patch application failed closed",
            stage=f"{role}-apply",
        )
    return {
        "role": role,
        "exit_code": result.exit_code,
        "termination_trigger": result.termination_trigger,
        "execution_error": result.execution_error,
        "cleanup_error": result.cleanup_error,
        "stdout": _stream_document(result.stdout),
        "stderr": _stream_document(result.stderr),
        "memory_limit_scope": result.memory_limit_scope,
        "containment_scope": result.containment_scope,
        "process_succeeded": result.process_succeeded,
        "swebench_containment_claim_ready": (result.swebench_containment_claim_ready),
    }


def _state_document(state: _FileState | None) -> dict[str, Any] | None:
    return None if state is None else state.document()


def _delta(before: _TreeManifest, after: _TreeManifest) -> dict[str, Any]:
    before_by_path = before.by_path
    after_by_path = after.by_path
    paths = sorted(
        set(before_by_path) | set(after_by_path),
        key=lambda item: item.encode("utf-8", errors="strict"),
    )
    entries = [
        {
            "path": path,
            "pre": _state_document(before_by_path.get(path)),
            "post": _state_document(after_by_path.get(path)),
        }
        for path in paths
        if before_by_path.get(path) != after_by_path.get(path)
    ]
    return {
        "changed_path_count": len(entries),
        "changed_paths": [entry["path"] for entry in entries],
        "entries": entries,
        "entries_sha256": _canonical_sha256(entries),
    }


def _path_prefixes(path: str) -> tuple[str, ...]:
    """Return canonical component prefixes, including ``path`` itself."""

    parts = path.split("/")
    return tuple("/".join(parts[:index]) for index in range(1, len(parts) + 1))


def _internal_path_conflict(paths: list[str]) -> tuple[str, str] | None:
    path_set = set(paths)
    for path in paths:
        for prefix in _path_prefixes(path)[:-1]:
            if prefix in path_set:
                return prefix, path
    return None


def _overlap(candidate: dict[str, Any], hidden: dict[str, Any]) -> list[str]:
    """Find equal or ancestor conflicts in bounded near-linear work."""

    conflicts: set[str] = set()
    candidate_paths = candidate["changed_paths"]
    hidden_paths = hidden["changed_paths"]
    candidate_set = set(candidate_paths)
    hidden_set = set(hidden_paths)
    for candidate_path in candidate_paths:
        for prefix in _path_prefixes(candidate_path):
            if prefix in hidden_set:
                conflicts.add(candidate_path)
                conflicts.add(prefix)
    for hidden_path in hidden_paths:
        for prefix in _path_prefixes(hidden_path):
            if prefix in candidate_set:
                conflicts.add(hidden_path)
                conflicts.add(prefix)
    return sorted(conflicts, key=lambda item: item.encode("utf-8", errors="strict"))


_FALSE_EVIDENCE = {
    "candidate_code_executed": False,
    "hidden_tests_executed": False,
    "official_grader_executed": False,
    "official_result_generated": False,
    "external_score_generated": False,
    "external_usefulness_claimed": False,
    "candidate_mount_isolation_verified": False,
    "network_isolation_verified": False,
    "filesystem_isolation_verified": False,
    "native_filesystem_quota_verified": False,
    "native_memory_limit_verified": False,
    "git_patch_parser_sandbox_verified": False,
    "git_binary_patch_supported": False,
    "git_extended_copy_headers_supported": False,
    "git_extended_rename_headers_supported": False,
    "git_mode_changes_supported": False,
    "nonportable_file_modes_supported": False,
    "preflight_exceptions_are_cohort_results": False,
    "claim_ready": False,
    "self_hash_authenticates_author": False,
}


def _canonicalize_source(source: VerifiedSweBenchSource) -> VerifiedSweBenchSource:
    try:
        suite, records = _revalidate_verified_source(source)
    except SweBenchError as exc:
        raise PatchCompositionError(str(exc)) from exc
    return VerifiedSweBenchSource(
        suite=suite,
        records=tuple(MappingProxyType(dict(record)) for record in records),
        file_sha256=suite.snapshot_sha256,
        byte_count=int(suite.document["canonical_snapshot"]["byte_count"]),
    )


def _plain_runtime_path(value: object, *, label: str) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise PatchCompositionError(f"{label} must be an absolute path")
    try:
        raw = os.fspath(value)
        if type(raw) is not str:
            raise TypeError
        path = Path(raw)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise PatchCompositionError(f"{label} could not be snapshotted") from exc
    if not path.is_absolute():
        raise PatchCompositionError(f"{label} must be an absolute path")
    return path


def _canonicalize_prepared(
    prepared: PreparedSweBenchRepository,
    *,
    source: VerifiedSweBenchSource,
) -> PreparedSweBenchRepository:
    if not isinstance(prepared, PreparedSweBenchRepository):
        raise PatchCompositionError("prepared repository must be PreparedSweBenchRepository")
    mirror = prepared.mirror
    if not isinstance(mirror, VerifiedBareMirror):
        raise PatchCompositionError("prepared repository mirror evidence is invalid")
    try:
        preparation_document = decode_repository_preparation(prepared.to_dict())
        mirror_document = decode_bare_mirror(mirror.to_dict())
        limits = RepositoryPreparationLimits(**dict(preparation_document["limits"]))
    except (RepositoryPreparationError, TypeError) as exc:
        raise PatchCompositionError(str(exc)) from exc
    canonical_mirror = VerifiedBareMirror(
        path=_plain_runtime_path(mirror.path, label="bare mirror"),
        repository=str(mirror_document["repository"]),
        git_executable=_plain_runtime_path(
            mirror.git_executable,
            label="Git executable",
        ),
        limits=limits,
        document=_freeze_json(mirror_document),
    )
    canonical_prepared = PreparedSweBenchRepository(
        path=_plain_runtime_path(prepared.path, label="prepared repository"),
        mirror=canonical_mirror,
        limits=limits,
        document=_freeze_json(preparation_document),
    )
    try:
        return verify_prepared_repository(canonical_prepared, source=source)
    except RepositoryPreparationError as exc:
        raise PatchCompositionError(str(exc)) from exc


def _source_inputs(
    prepared: PreparedSweBenchRepository,
    source: VerifiedSweBenchSource,
) -> tuple[
    PreparedSweBenchRepository,
    dict[str, Any],
    str,
    VerifiedSweBenchSource,
]:
    canonical_source = _canonicalize_source(source)
    verified = _canonicalize_prepared(prepared, source=canonical_source)
    preparation = verified.to_dict()
    binding = preparation["source_binding"]
    ordinal = binding["source_ordinal"]
    if type(ordinal) is not int or not 0 <= ordinal < len(canonical_source.records):
        raise PatchCompositionError("prepared source ordinal is invalid")
    record = canonical_source.records[ordinal]
    hidden_patch = record["test_patch"]
    if type(hidden_patch) is not str:
        raise PatchCompositionError("verified source test_patch must be text")
    return verified, binding, hidden_patch, canonical_source


def build_patch_composition(
    prepared: PreparedSweBenchRepository,
    source: VerifiedSweBenchSource,
    model_patch: str,
    *,
    limits: PatchCompositionLimits | None = None,
) -> VerifiedPatchComposition:
    """Apply text patches in copies and prove disjoint composition.

    A raised :class:`PatchCompositionError` carries a machine-readable stage
    and the fixed ``preflight-exception-not-a-cohort-result`` disposition.  An
    outer complete-denominator ledger must retain that exception explicitly;
    this single-task preflight never manufactures a cohort result for it.
    """

    selected_limits = _revalidate_limits(PatchCompositionLimits() if limits is None else limits)
    verified, binding, hidden_patch, canonical_source = _source_inputs(prepared, source)
    candidate_bytes = _patch_bytes(
        model_patch,
        label="candidate model_patch",
        maximum=selected_limits.max_patch_bytes,
    )
    hidden_bytes = _patch_bytes(
        hidden_patch,
        label="hidden test_patch",
        maximum=selected_limits.max_patch_bytes,
    )
    source_root = verified.path
    base_manifest = _scan_tree(source_root, selected_limits)
    git_document = verified.mirror.to_dict()["git"]
    git_executable = verified.mirror.git_executable
    temporary_parent = Path(tempfile.gettempdir()).resolve()
    mirror_root = verified.mirror.path.resolve()
    try:
        if temporary_parent.is_relative_to(source_root.resolve()) or (
            temporary_parent.is_relative_to(mirror_root)
        ):
            raise PatchCompositionError(
                "system temporary directory is inside a verified input tree"
            )
    except (OSError, RuntimeError, ValueError) as exc:
        raise PatchCompositionError("system temporary directory could not be inspected") from exc

    with tempfile.TemporaryDirectory(
        prefix="ctxc-swebench-patch-",
        dir=temporary_parent,
    ) as raw_temp:
        temporary = Path(raw_temp).resolve()
        home = temporary / "home"
        home.mkdir(mode=0o700)
        candidate_patch_path = temporary / "candidate.patch"
        hidden_patch_path = temporary / "hidden.patch"
        _write_patch_file(candidate_patch_path, candidate_bytes)
        _write_patch_file(hidden_patch_path, hidden_bytes)

        candidate_root = temporary / "candidate"
        hidden_root = temporary / "hidden"
        _copy_verified_tree(source_root, candidate_root, base_manifest, selected_limits)
        _copy_verified_tree(source_root, hidden_root, base_manifest, selected_limits)
        candidate_apply = _apply_patch(
            role="candidate-isolated",
            git_executable=git_executable,
            workspace=candidate_root,
            patch_path=candidate_patch_path,
            home=home,
            limits=selected_limits,
        )
        candidate_manifest = _scan_tree(candidate_root, selected_limits)
        candidate_delta = _delta(base_manifest, candidate_manifest)
        hidden_apply = _apply_patch(
            role="hidden-isolated",
            git_executable=git_executable,
            workspace=hidden_root,
            patch_path=hidden_patch_path,
            home=home,
            limits=selected_limits,
        )
        hidden_manifest = _scan_tree(hidden_root, selected_limits)
        hidden_delta = _delta(base_manifest, hidden_manifest)
        conflicts = _overlap(candidate_delta, hidden_delta)

        composition: dict[str, Any] | None = None
        status = "overlap-detected-not-composable"
        if not conflicts:
            composition_root = temporary / "composition"
            _copy_verified_tree(source_root, composition_root, base_manifest, selected_limits)
            composition_candidate_apply = _apply_patch(
                role="composition-candidate",
                git_executable=git_executable,
                workspace=composition_root,
                patch_path=candidate_patch_path,
                home=home,
                limits=selected_limits,
            )
            composition_mid = _scan_tree(composition_root, selected_limits)
            if _delta(base_manifest, composition_mid) != candidate_delta:
                raise PatchCompositionError("composition candidate delta does not replay exactly")
            composition_hidden_apply = _apply_patch(
                role="composition-hidden",
                git_executable=git_executable,
                workspace=composition_root,
                patch_path=hidden_patch_path,
                home=home,
                limits=selected_limits,
            )
            composition_final = _scan_tree(composition_root, selected_limits)
            if _delta(composition_mid, composition_final) != hidden_delta:
                raise PatchCompositionError("composition hidden delta does not replay exactly")
            if any(
                composition_final.by_path.get(path) != candidate_manifest.by_path.get(path)
                for path in candidate_delta["changed_paths"]
            ):
                raise PatchCompositionError("hidden composition changed a candidate-owned path")
            composition = {
                "candidate_apply": composition_candidate_apply,
                "hidden_apply": composition_hidden_apply,
                "mid_manifest": composition_mid.summary(),
                "final_manifest": composition_final.summary(),
                "candidate_delta_revalidated": True,
                "hidden_delta_revalidated": True,
            }
            status = "disjoint-composition-preflight-verified-not-a-grader"

    try:
        verify_prepared_repository(verified, source=canonical_source)
    except RepositoryPreparationError as exc:
        raise PatchCompositionError("prepared repository changed during patch preflight") from exc
    if _scan_tree(source_root, selected_limits) != base_manifest:
        raise PatchCompositionError("prepared repository changed during patch preflight")

    document: dict[str, Any] = {
        "schema": PATCH_COMPOSITION_SCHEMA,
        "status": status,
        "source_binding": {
            "preparation_sha256": verified.preparation_sha256,
            "suite": binding["suite"],
            "source_ordinal": binding["source_ordinal"],
            "source_record_sha256": binding["source_record_sha256"],
            "instance_id": binding["instance_id"],
            "repository": binding["repository"],
            "base_commit": binding["base_commit"],
            "candidate_patch": {
                "byte_count": len(candidate_bytes),
                "sha256": hashlib.sha256(candidate_bytes).hexdigest(),
            },
            "hidden_patch": {
                "byte_count": len(hidden_bytes),
                "sha256": hashlib.sha256(hidden_bytes).hexdigest(),
            },
        },
        "limits": _limits_document(selected_limits),
        "git_apply_contract": {
            "git_executable_byte_count": git_document["executable_byte_count"],
            "git_executable_sha256": git_document["executable_sha256"],
            "argv_tail": list(_GIT_APPLY_TAIL),
            "shell": False,
            "stdin": "devnull",
            "sterile_environment": True,
            "repository_discovery_ceiling": "owned-temporary-root",
            "binary_patch_policy": "rejected-fail-closed",
            "extended_copy_policy": "rejected-fail-closed",
            "mode_header_policy": ("mode-changes-rejected-new-and-deleted-files-100644-only"),
            "rename_header_policy": "rejected-fail-closed",
            "native_resource_quota": False,
            "failure_disposition": _FAILURE_DISPOSITION,
            "temporary_paths_serialized": False,
        },
        "base_manifest": base_manifest.summary(),
        "candidate": {
            "apply": candidate_apply,
            "post_manifest": candidate_manifest.summary(),
            "delta": candidate_delta,
        },
        "hidden": {
            "apply": hidden_apply,
            "post_manifest": hidden_manifest.summary(),
            "delta": hidden_delta,
            "new_file_count": sum(
                entry["pre"] is None and entry["post"] is not None
                for entry in hidden_delta["entries"]
            ),
        },
        "overlap": {
            "disjoint": not conflicts,
            "conflicting_paths": conflicts,
        },
        "composition": composition,
        "evidence_state": {
            "prepared_base_reverified_before_and_after": True,
            "candidate_patch_applied_to_temporary_copy": True,
            "hidden_patch_applied_to_independent_temporary_copy": True,
            "disjoint_composition_replayed": not conflicts,
            "hidden_new_file_patch_supported": any(
                entry["pre"] is None and entry["post"] is not None
                for entry in hidden_delta["entries"]
            ),
            **_FALSE_EVIDENCE,
        },
    }
    document["patch_composition_sha256"] = _canonical_sha256(document)
    encoded = _canonical_json_bytes(document)
    if len(encoded) > selected_limits.max_document_bytes:
        raise PatchCompositionError("patch-composition document exceeds its byte limit")
    decoded = decode_patch_composition(document)
    return VerifiedPatchComposition(selected_limits, _freeze_json(decoded))


def _decode_summary(
    value: Any,
    *,
    label: str,
    limits: PatchCompositionLimits,
) -> dict[str, Any]:
    payload = _object(
        value,
        fields={"file_count", "total_bytes", "entries_sha256"},
        label=label,
    )
    for name, maximum in (
        ("file_count", limits.max_files),
        ("total_bytes", limits.max_total_bytes),
    ):
        if type(payload[name]) is not int or not 0 <= payload[name] <= maximum:
            raise PatchCompositionError(f"{label}.{name} is invalid")
    _sha256(payload["entries_sha256"], label=f"{label}.entries_sha256")
    return payload


def _decode_state(
    value: Any,
    *,
    label: str,
    limits: PatchCompositionLimits,
) -> dict[str, Any] | None:
    if value is None:
        return None
    payload = _object(
        value,
        fields={"path", "mode", "byte_count", "sha256"},
        label=label,
    )
    path = _portable_relative_path(payload["path"], limits)
    if path != payload["path"]:
        raise PatchCompositionError(f"{label}.path is not canonical")
    if payload["mode"] not in {"100644", "100755"}:
        raise PatchCompositionError(f"{label}.mode is invalid")
    if type(payload["byte_count"]) is not int or not (
        0 <= payload["byte_count"] <= limits.max_file_bytes
    ):
        raise PatchCompositionError(f"{label}.byte_count is invalid")
    _sha256(payload["sha256"], label=f"{label}.sha256")
    return payload


def _decode_delta(
    value: Any,
    *,
    label: str,
    limits: PatchCompositionLimits,
) -> dict[str, Any]:
    payload = _object(
        value,
        fields={
            "changed_path_count",
            "changed_paths",
            "entries",
            "entries_sha256",
        },
        label=label,
    )
    if not isinstance(payload["entries"], list):
        raise PatchCompositionError(f"{label}.entries must be an array")
    if len(payload["entries"]) > 2 * limits.max_files:
        raise PatchCompositionError(f"{label}.entries exceed the retained limit")
    if (
        not isinstance(payload["changed_paths"], list)
        or len(payload["changed_paths"]) > 2 * limits.max_files
    ):
        raise PatchCompositionError(f"{label}.changed_paths exceed the retained limit")
    entries: list[dict[str, Any]] = []
    for index, raw in enumerate(payload["entries"]):
        entry = _object(
            raw,
            fields={"path", "pre", "post"},
            label=f"{label}.entries[{index}]",
        )
        path = _portable_relative_path(entry["path"], limits)
        if path != entry["path"]:
            raise PatchCompositionError(f"{label} path is not canonical")
        pre = _decode_state(
            entry["pre"],
            label=f"{label}.entries[{index}].pre",
            limits=limits,
        )
        post = _decode_state(
            entry["post"],
            label=f"{label}.entries[{index}].post",
            limits=limits,
        )
        if pre is None and post is None:
            raise PatchCompositionError(f"{label} contains an empty change")
        if pre is not None and pre["path"] != path:
            raise PatchCompositionError(f"{label} pre-state path mismatch")
        if post is not None and post["path"] != path:
            raise PatchCompositionError(f"{label} post-state path mismatch")
        if pre == post:
            raise PatchCompositionError(f"{label} retains an unchanged path")
        entries.append({"path": path, "pre": pre, "post": post})
    paths = [entry["path"] for entry in entries]
    if paths != sorted(paths, key=lambda item: item.encode("utf-8")):
        raise PatchCompositionError(f"{label} paths are not canonical")
    if len(set(paths)) != len(paths):
        raise PatchCompositionError(f"{label} paths are duplicated")
    for state_name, state_paths in (
        ("pre", [entry["path"] for entry in entries if entry["pre"] is not None]),
        ("post", [entry["path"] for entry in entries if entry["post"] is not None]),
    ):
        conflict = _internal_path_conflict(state_paths)
        if conflict is not None:
            raise PatchCompositionError(f"{label} {state_name}-state paths conflict by ancestry")
    if type(payload["changed_path_count"]) is not int or payload["changed_path_count"] != len(
        entries
    ):
        raise PatchCompositionError(f"{label} changed-path count mismatch")
    if payload["changed_paths"] != paths:
        raise PatchCompositionError(f"{label} changed-path list mismatch")
    if payload["entries_sha256"] != _canonical_sha256(entries):
        raise PatchCompositionError(f"{label} digest mismatch")
    return payload


def _reconcile_delta_summaries(
    base: dict[str, Any],
    post: dict[str, Any],
    delta: dict[str, Any],
    *,
    label: str,
) -> None:
    pre_states = [entry["pre"] for entry in delta["entries"] if entry["pre"] is not None]
    post_states = [entry["post"] for entry in delta["entries"] if entry["post"] is not None]
    additions = sum(entry["pre"] is None for entry in delta["entries"])
    deletions = sum(entry["post"] is None for entry in delta["entries"])
    if len(pre_states) > base["file_count"] or len(post_states) > post["file_count"]:
        raise PatchCompositionError(f"{label} file counts are inconsistent")
    pre_bytes = sum(state["byte_count"] for state in pre_states)
    post_bytes = sum(state["byte_count"] for state in post_states)
    if pre_bytes > base["total_bytes"] or post_bytes > post["total_bytes"]:
        raise PatchCompositionError(f"{label} byte counts are inconsistent")
    if post["file_count"] != base["file_count"] + additions - deletions:
        raise PatchCompositionError(f"{label} derived file count mismatch")
    if post["total_bytes"] != base["total_bytes"] - pre_bytes + post_bytes:
        raise PatchCompositionError(f"{label} derived byte count mismatch")


def _decode_apply(
    value: Any,
    *,
    label: str,
    expected_role: str,
    limits: PatchCompositionLimits,
) -> dict[str, Any]:
    payload = _object(
        value,
        fields={
            "role",
            "exit_code",
            "termination_trigger",
            "execution_error",
            "cleanup_error",
            "stdout",
            "stderr",
            "memory_limit_scope",
            "containment_scope",
            "process_succeeded",
            "swebench_containment_claim_ready",
        },
        label=label,
    )
    if payload["role"] != expected_role:
        raise PatchCompositionError(f"{label}.role is invalid")
    if type(payload["exit_code"]) is not int or payload["exit_code"] != 0:
        raise PatchCompositionError(f"{label}.exit_code must be zero")
    for name in ("termination_trigger", "execution_error", "cleanup_error"):
        if payload[name] is not None:
            raise PatchCompositionError(f"{label}.{name} must be null")
    for stream_name in ("stdout", "stderr"):
        stream = _object(
            payload[stream_name],
            fields={
                "observed_bytes",
                "captured_bytes",
                "captured_sha256",
                "complete",
            },
            label=f"{label}.{stream_name}",
        )
        observed = stream["observed_bytes"]
        captured = stream["captured_bytes"]
        maximum = limits.max_stdout_bytes if stream_name == "stdout" else limits.max_stderr_bytes
        if (
            type(observed) is not int
            or type(captured) is not int
            or not 0 <= observed <= maximum
            or captured != observed
            or stream["complete"] is not True
        ):
            raise PatchCompositionError(f"{label}.{stream_name} is incomplete")
        _sha256(
            stream["captured_sha256"],
            label=f"{label}.{stream_name}.captured_sha256",
        )
        if observed == 0 and stream["captured_sha256"] != _EMPTY_SHA256:
            raise PatchCompositionError(f"{label}.{stream_name} empty digest mismatch")
    if payload["process_succeeded"] is not True:
        raise PatchCompositionError(f"{label}.process_succeeded must be true")
    if payload["swebench_containment_claim_ready"] is not False:
        raise PatchCompositionError(f"{label}.swebench_containment_claim_ready must be false")
    if payload["memory_limit_scope"] != "none":
        raise PatchCompositionError(f"{label}.memory_limit_scope is invalid")
    if payload["containment_scope"] not in _CONTAINMENT_SCOPES:
        raise PatchCompositionError(f"{label}.containment_scope is invalid")
    return payload


def decode_patch_composition(value: Any) -> dict[str, Any]:
    """Strictly decode a self-hashed, permanently non-claim-ready document."""

    fields = {
        "schema",
        "status",
        "source_binding",
        "limits",
        "git_apply_contract",
        "base_manifest",
        "candidate",
        "hidden",
        "overlap",
        "composition",
        "evidence_state",
        "patch_composition_sha256",
    }
    payload = _object(value, fields=fields, label="patch composition")
    if payload["schema"] != PATCH_COMPOSITION_SCHEMA:
        raise PatchCompositionError("patch composition schema is invalid")
    if payload["status"] not in {
        "overlap-detected-not-composable",
        "disjoint-composition-preflight-verified-not-a-grader",
    }:
        raise PatchCompositionError("patch composition status is invalid")
    claimed = _sha256(
        payload["patch_composition_sha256"],
        label="patch composition self-hash",
    )
    unsigned = dict(payload)
    del unsigned["patch_composition_sha256"]
    if claimed != _canonical_sha256(unsigned):
        raise PatchCompositionError("patch composition self-hash mismatch")
    limits_payload = _object(payload["limits"], fields=set(_LIMIT_FIELDS), label="patch limits")
    try:
        limits = PatchCompositionLimits(**limits_payload)
    except (TypeError, PatchCompositionError) as exc:
        raise PatchCompositionError("patch composition limits are invalid") from exc
    source_binding = _object(
        payload["source_binding"],
        fields={
            "preparation_sha256",
            "suite",
            "source_ordinal",
            "source_record_sha256",
            "instance_id",
            "repository",
            "base_commit",
            "candidate_patch",
            "hidden_patch",
        },
        label="patch source binding",
    )
    for name in ("preparation_sha256", "source_record_sha256"):
        _sha256(source_binding[name], label=f"patch source binding.{name}")
    suite_binding = _object(
        source_binding["suite"],
        fields={"suite_id", "suite_sha256", "source_snapshot_sha256"},
        label="patch source binding.suite",
    )
    if type(suite_binding["suite_id"]) is not str or not suite_binding["suite_id"]:
        raise PatchCompositionError("patch source binding suite id is invalid")
    for name in ("suite_sha256", "source_snapshot_sha256"):
        _sha256(suite_binding[name], label=f"patch source binding.suite.{name}")
    if type(source_binding["source_ordinal"]) is not int or source_binding["source_ordinal"] < 0:
        raise PatchCompositionError("patch source ordinal is invalid")
    for name in ("instance_id", "repository", "base_commit"):
        if type(source_binding[name]) is not str or not source_binding[name]:
            raise PatchCompositionError(f"patch source binding.{name} is invalid")
    for name in ("candidate_patch", "hidden_patch"):
        evidence = _object(
            source_binding[name],
            fields={"byte_count", "sha256"},
            label=f"patch source binding.{name}",
        )
        if type(evidence["byte_count"]) is not int or not (
            0 <= evidence["byte_count"] <= limits.max_patch_bytes
        ):
            raise PatchCompositionError(f"patch source binding.{name} size is invalid")
        _sha256(evidence["sha256"], label=f"patch source binding.{name}.sha256")
    contract = _object(
        payload["git_apply_contract"],
        fields={
            "git_executable_byte_count",
            "git_executable_sha256",
            "argv_tail",
            "shell",
            "stdin",
            "sterile_environment",
            "repository_discovery_ceiling",
            "binary_patch_policy",
            "extended_copy_policy",
            "mode_header_policy",
            "rename_header_policy",
            "native_resource_quota",
            "failure_disposition",
            "temporary_paths_serialized",
        },
        label="Git apply contract",
    )
    if (
        type(contract["git_executable_byte_count"]) is not int
        or contract["git_executable_byte_count"] <= 0
    ):
        raise PatchCompositionError("Git executable byte count is invalid")
    _sha256(contract["git_executable_sha256"], label="Git executable SHA-256")
    if contract["argv_tail"] != list(_GIT_APPLY_TAIL):
        raise PatchCompositionError("Git apply argv contract changed")
    if (
        contract["shell"] is not False
        or contract["stdin"] != "devnull"
        or contract["sterile_environment"] is not True
        or contract["repository_discovery_ceiling"] != "owned-temporary-root"
        or contract["binary_patch_policy"] != "rejected-fail-closed"
        or contract["extended_copy_policy"] != "rejected-fail-closed"
        or contract["mode_header_policy"]
        != "mode-changes-rejected-new-and-deleted-files-100644-only"
        or contract["rename_header_policy"] != "rejected-fail-closed"
        or contract["native_resource_quota"] is not False
        or contract["failure_disposition"] != _FAILURE_DISPOSITION
        or contract["temporary_paths_serialized"] is not False
    ):
        raise PatchCompositionError("Git apply launch contract is invalid")
    base_summary = _decode_summary(payload["base_manifest"], label="base manifest", limits=limits)
    candidate = _object(
        payload["candidate"],
        fields={"apply", "post_manifest", "delta"},
        label="candidate patch",
    )
    hidden = _object(
        payload["hidden"],
        fields={"apply", "post_manifest", "delta", "new_file_count"},
        label="hidden patch",
    )
    _decode_apply(
        candidate["apply"],
        label="candidate patch apply",
        expected_role="candidate-isolated",
        limits=limits,
    )
    _decode_apply(
        hidden["apply"],
        label="hidden patch apply",
        expected_role="hidden-isolated",
        limits=limits,
    )
    candidate_summary = _decode_summary(
        candidate["post_manifest"],
        label="candidate post manifest",
        limits=limits,
    )
    hidden_summary = _decode_summary(
        hidden["post_manifest"], label="hidden post manifest", limits=limits
    )
    candidate_delta = _decode_delta(candidate["delta"], label="candidate delta", limits=limits)
    hidden_delta = _decode_delta(hidden["delta"], label="hidden delta", limits=limits)
    _reconcile_delta_summaries(
        base_summary,
        candidate_summary,
        candidate_delta,
        label="candidate delta",
    )
    _reconcile_delta_summaries(
        base_summary,
        hidden_summary,
        hidden_delta,
        label="hidden delta",
    )
    expected_new_files = sum(
        entry["pre"] is None and entry["post"] is not None for entry in hidden_delta["entries"]
    )
    if (
        type(hidden["new_file_count"]) is not int
        or not 0 <= hidden["new_file_count"] <= limits.max_files
        or hidden["new_file_count"] != expected_new_files
    ):
        raise PatchCompositionError("hidden new-file count mismatch")
    overlap = _object(
        payload["overlap"],
        fields={"disjoint", "conflicting_paths"},
        label="patch overlap",
    )
    expected_conflicts = _overlap(candidate_delta, hidden_delta)
    if overlap["conflicting_paths"] != expected_conflicts or overlap["disjoint"] is not (
        not expected_conflicts
    ):
        raise PatchCompositionError("patch overlap evidence mismatch")
    if overlap["disjoint"]:
        composition = _object(
            payload["composition"],
            fields={
                "candidate_apply",
                "hidden_apply",
                "mid_manifest",
                "final_manifest",
                "candidate_delta_revalidated",
                "hidden_delta_revalidated",
            },
            label="patch composition replay",
        )
        _decode_apply(
            composition["candidate_apply"],
            label="composition candidate apply",
            expected_role="composition-candidate",
            limits=limits,
        )
        _decode_apply(
            composition["hidden_apply"],
            label="composition hidden apply",
            expected_role="composition-hidden",
            limits=limits,
        )
        mid_summary = _decode_summary(
            composition["mid_manifest"],
            label="composition midpoint",
            limits=limits,
        )
        final_summary = _decode_summary(
            composition["final_manifest"],
            label="composition final",
            limits=limits,
        )
        if mid_summary != candidate_summary:
            raise PatchCompositionError("composition midpoint summary mismatch")
        _reconcile_delta_summaries(
            mid_summary,
            final_summary,
            hidden_delta,
            label="composition final delta",
        )
        if (
            composition["candidate_delta_revalidated"] is not True
            or composition["hidden_delta_revalidated"] is not True
            or payload["status"] != "disjoint-composition-preflight-verified-not-a-grader"
        ):
            raise PatchCompositionError("composition replay evidence is invalid")
    elif (
        payload["composition"] is not None or payload["status"] != "overlap-detected-not-composable"
    ):
        raise PatchCompositionError("overlap result retains composition evidence")
    evidence = _object(
        payload["evidence_state"],
        fields={
            "prepared_base_reverified_before_and_after",
            "candidate_patch_applied_to_temporary_copy",
            "hidden_patch_applied_to_independent_temporary_copy",
            "disjoint_composition_replayed",
            "hidden_new_file_patch_supported",
            *_FALSE_EVIDENCE,
        },
        label="patch evidence state",
    )
    for name in (
        "prepared_base_reverified_before_and_after",
        "candidate_patch_applied_to_temporary_copy",
        "hidden_patch_applied_to_independent_temporary_copy",
    ):
        if evidence[name] is not True:
            raise PatchCompositionError(f"patch evidence {name} must be true")
    if evidence["disjoint_composition_replayed"] is not overlap["disjoint"]:
        raise PatchCompositionError("composition replay state mismatch")
    if evidence["hidden_new_file_patch_supported"] is not bool(expected_new_files):
        raise PatchCompositionError("hidden new-file state mismatch")
    for name in _FALSE_EVIDENCE:
        if evidence[name] is not False:
            raise PatchCompositionError(f"patch evidence {name} must be false")
    if len(_canonical_json_bytes(payload)) > limits.max_document_bytes:
        raise PatchCompositionError("patch-composition document exceeds its byte limit")
    return payload


def _bind_patch_composition_structural(
    composition: VerifiedPatchComposition,
    prepared: PreparedSweBenchRepository,
    source: VerifiedSweBenchSource,
    model_patch: str,
) -> tuple[
    VerifiedPatchComposition,
    PreparedSweBenchRepository,
    VerifiedSweBenchSource,
]:
    """Structurally decode and bind evidence without claiming semantic replay."""

    if not isinstance(composition, VerifiedPatchComposition):
        raise PatchCompositionError("composition must be VerifiedPatchComposition")
    limits = _revalidate_limits(composition.limits)
    decoded = decode_patch_composition(composition.to_dict())
    if decoded["limits"] != _limits_document(limits):
        raise PatchCompositionError("runtime patch limits do not match evidence")
    verified, binding, hidden_patch, canonical_source = _source_inputs(prepared, source)
    candidate_bytes = _patch_bytes(
        model_patch,
        label="candidate model_patch",
        maximum=limits.max_patch_bytes,
    )
    hidden_bytes = _patch_bytes(
        hidden_patch,
        label="hidden test_patch",
        maximum=limits.max_patch_bytes,
    )
    retained = decoded["source_binding"]
    expected_binding = {
        "preparation_sha256": verified.preparation_sha256,
        "suite": binding["suite"],
        "source_ordinal": binding["source_ordinal"],
        "source_record_sha256": binding["source_record_sha256"],
        "instance_id": binding["instance_id"],
        "repository": binding["repository"],
        "base_commit": binding["base_commit"],
        "candidate_patch": {
            "byte_count": len(candidate_bytes),
            "sha256": hashlib.sha256(candidate_bytes).hexdigest(),
        },
        "hidden_patch": {
            "byte_count": len(hidden_bytes),
            "sha256": hashlib.sha256(hidden_bytes).hexdigest(),
        },
    }
    if retained != expected_binding:
        raise PatchCompositionError("patch composition input binding mismatch")
    git = verified.mirror.to_dict()["git"]
    contract = decoded["git_apply_contract"]
    if (
        contract["git_executable_byte_count"] != git["executable_byte_count"]
        or contract["git_executable_sha256"] != git["executable_sha256"]
    ):
        raise PatchCompositionError("patch composition Git identity mismatch")
    if _scan_tree(verified.path, limits).summary() != decoded["base_manifest"]:
        raise PatchCompositionError("patch composition live base manifest mismatch")
    retained_composition = VerifiedPatchComposition(limits, _freeze_json(decoded))
    return retained_composition, verified, canonical_source


def _verify_patch_composition_semantic(
    composition: VerifiedPatchComposition,
    prepared: PreparedSweBenchRepository,
    source: VerifiedSweBenchSource,
    model_patch: str,
) -> tuple[
    VerifiedPatchComposition,
    PreparedSweBenchRepository,
    VerifiedSweBenchSource,
]:
    retained, canonical_prepared, canonical_source = _bind_patch_composition_structural(
        composition,
        prepared,
        source,
        model_patch,
    )
    replayed = build_patch_composition(
        canonical_prepared,
        canonical_source,
        model_patch,
        limits=retained.limits,
    )
    if replayed.to_dict() != retained.to_dict():
        raise PatchCompositionError("patch composition semantic replay mismatch")
    return retained, canonical_prepared, canonical_source


def verify_patch_composition(
    composition: VerifiedPatchComposition,
    prepared: PreparedSweBenchRepository,
    source: VerifiedSweBenchSource,
    model_patch: str,
) -> VerifiedPatchComposition:
    """Require exact live semantic replay before returning verified evidence."""

    retained, _canonical_prepared, _canonical_source = _verify_patch_composition_semantic(
        composition,
        prepared,
        source,
        model_patch,
    )
    return retained


def replay_patch_composition(
    composition: VerifiedPatchComposition,
    prepared: PreparedSweBenchRepository,
    source: VerifiedSweBenchSource,
    model_patch: str,
) -> VerifiedPatchComposition:
    """Repeat all temporary applications and require exact semantic evidence."""

    return verify_patch_composition(composition, prepared, source, model_patch)


def load_patch_composition(
    path: str | Path,
    prepared: PreparedSweBenchRepository,
    source: VerifiedSweBenchSource,
    model_patch: str,
    *,
    replay: bool = False,
) -> VerifiedPatchComposition:
    """Strictly load, live-bind, and semantically replay saved evidence.

    ``replay`` is retained for source compatibility; both boolean values now
    require the same mandatory semantic replay.
    """

    if type(replay) is not bool:
        raise PatchCompositionError("replay must be a boolean")

    try:
        loaded = load_strict_json_file(
            path,
            limits=StrictJsonLimits(
                max_bytes=_MAX_DOCUMENT_BYTES,
                max_line_chars=_MAX_DOCUMENT_BYTES,
                max_depth=128,
            ),
            label="SWE-bench patch composition",
        )
    except StrictJsonError as exc:
        raise PatchCompositionError(str(exc)) from exc
    decoded = decode_patch_composition(loaded.value)
    limits_payload = decoded["limits"]
    limits = PatchCompositionLimits(**limits_payload)
    if loaded.byte_count > limits.max_document_bytes:
        raise PatchCompositionError("patch-composition file exceeds retained limit")
    composition = VerifiedPatchComposition(limits, _freeze_json(decoded))
    return verify_patch_composition(composition, prepared, source, model_patch)


def write_patch_composition(
    path: str | Path,
    composition: VerifiedPatchComposition,
    prepared: PreparedSweBenchRepository,
    source: VerifiedSweBenchSource,
    model_patch: str,
) -> None:
    """Exclusively write live-bound, semantically replayed evidence."""

    verified, canonical_prepared, _canonical_source = _verify_patch_composition_semantic(
        composition,
        prepared,
        source,
        model_patch,
    )
    output = Path(path)
    prepared_root = canonical_prepared.path
    mirror_root = canonical_prepared.mirror.path
    try:
        resolved_output = output.resolve(strict=False)
        if resolved_output.is_relative_to(prepared_root) or (
            resolved_output.is_relative_to(mirror_root)
        ):
            raise PatchCompositionError(
                "patch-composition output cannot be inside a verified input tree"
            )
    except (OSError, RuntimeError, ValueError) as exc:
        raise PatchCompositionError("patch-composition output path could not be inspected") from exc
    rendered = (
        json.dumps(
            verified.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )
    if len(rendered.encode("utf-8")) > verified.limits.max_document_bytes:
        raise PatchCompositionError("patch-composition output exceeds its byte limit")
    try:
        atomic_write_text(output, rendered, overwrite=False)
    except FileExistsError as exc:
        raise PatchCompositionError("patch-composition output already exists") from exc
