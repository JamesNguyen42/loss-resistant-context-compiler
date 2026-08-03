"""Coordinator-only raw Git-tree preparation for SWE-bench tasks.

The public source descriptor remains immutable.  This module verifies a local
SHA-1 bare mirror, exports one exact commit through raw ``git cat-file`` object
frames, and independently reconciles the resulting regular-file tree.  It
does not create a candidate mount or prove execution/network isolation.
"""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import stat
import sys
import tempfile
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from context_compiler.atomic import atomic_write_text

from .json_io import (
    StrictFileEvidence,
    StrictJsonError,
    _open_regular_file,
    hash_bounded_regular_file,
    read_bounded_regular_file,
)
from .literal_process import (
    LiteralProcessLimits,
    run_literal_argv,
)
from .swebench import (
    SweBenchError,
    VerifiedSweBenchSource,
    _revalidate_verified_source,
)
from .swebench import (
    _canonical_sha256 as _source_canonical_sha256,
)
from .swebench_repository_worker import WorkerError, _portable_component

MIRROR_SCHEMA = "ctxc-swebench-bare-mirror-0.1"
PREPARATION_SCHEMA = "ctxc-swebench-repository-preparation-0.1"

_WORKER_RESULT_SCHEMA = "ctxc-swebench-repository-worker-result-0.1"
_WORKER_ERROR_SCHEMA = "ctxc-swebench-repository-worker-error-0.1"
_SHA1_RE = re.compile(r"[0-9a-f]{40}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_REPOSITORY_RE = re.compile(
    r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z"
)
_INSTANCE_RE = re.compile(
    r"[A-Za-z0-9_.-]{1,100}__[A-Za-z0-9_.-]{1,100}-[0-9]{1,12}\Z"
)
_IDENTIFIER_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_MAX_WORKER_SOURCE_BYTES = 4 * 1024 * 1024
_MAX_EXECUTABLE_BYTES = 256 * 1024 * 1024
_MAX_TIMEOUT_SECONDS = 7 * 24 * 60 * 60
_MAX_LITERAL_STREAM_BYTES = 64 * 1024 * 1024
_WORKER_SOURCE = Path(__file__).with_name("swebench_repository_worker.py")
_EXPECTED_WORKER_SOURCE_SHA256 = (
    "cb9c8680d74022b7db8aea8b376790a49131f5fd1a1c0b93ba74341302c8a44f"
)


class RepositoryPreparationError(ValueError):
    """A mirror or prepared repository violated the frozen boundary."""


def _positive_number(value: object, *, label: str, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RepositoryPreparationError(f"{label} must be a positive number")
    try:
        converted = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise RepositoryPreparationError(f"{label} must be a positive number") from exc
    if not math.isfinite(converted) or not 0 < converted <= maximum:
        raise RepositoryPreparationError(
            f"{label} must be no greater than {maximum}"
        )
    return converted


def _bounded_integer(
    value: object,
    *,
    label: str,
    minimum: int,
    maximum: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise RepositoryPreparationError(
            f"{label} must be an integer from {minimum} through {maximum}"
        )
    return value


@dataclass(frozen=True, slots=True)
class RepositoryPreparationLimits:
    """Finite Git metadata, tree, file, disk, and worker-output bounds."""

    timeout_seconds: float = 300.0
    max_worker_stdout_bytes: int = 32 * 1024 * 1024
    max_worker_stderr_bytes: int = 1024 * 1024
    max_commit_bytes: int = 16 * 1024 * 1024
    max_tree_object_bytes: int = 64 * 1024 * 1024
    max_total_tree_bytes: int = 256 * 1024 * 1024
    max_tree_objects: int = 100_000
    max_entries: int = 100_000
    max_path_bytes: int = 1024
    max_depth: int = 64
    max_blob_bytes: int = 128 * 1024 * 1024
    max_total_blob_bytes: int = 2 * 1024 * 1024 * 1024
    max_mirror_entries: int = 1_000_000

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
            "max_worker_stdout_bytes": _MAX_LITERAL_STREAM_BYTES,
            "max_worker_stderr_bytes": _MAX_LITERAL_STREAM_BYTES,
            "max_commit_bytes": 1024 * 1024 * 1024,
            "max_tree_object_bytes": 1024 * 1024 * 1024,
            "max_total_tree_bytes": 4 * 1024 * 1024 * 1024,
            "max_tree_objects": 1_000_000,
            "max_entries": 1_000_000,
            "max_path_bytes": 4096,
            "max_depth": 256,
            "max_blob_bytes": 4 * 1024 * 1024 * 1024,
            "max_total_blob_bytes": 16 * 1024 * 1024 * 1024,
            "max_mirror_entries": 5_000_000,
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
    "max_worker_stdout_bytes",
    "max_worker_stderr_bytes",
    "max_commit_bytes",
    "max_tree_object_bytes",
    "max_total_tree_bytes",
    "max_tree_objects",
    "max_entries",
    "max_path_bytes",
    "max_depth",
    "max_blob_bytes",
    "max_total_blob_bytes",
    "max_mirror_entries",
)


def _limits_document(limits: RepositoryPreparationLimits) -> dict[str, int | float]:
    return {name: getattr(limits, name) for name in _LIMIT_FIELDS}


def _revalidate_limits(value: object) -> RepositoryPreparationLimits:
    if not isinstance(value, RepositoryPreparationLimits):
        raise RepositoryPreparationError(
            "limits must be RepositoryPreparationLimits"
        )
    try:
        return RepositoryPreparationLimits(**_limits_document(value))
    except (AttributeError, TypeError, RepositoryPreparationError) as exc:
        raise RepositoryPreparationError(
            "limits must be a valid RepositoryPreparationLimits value"
        ) from exc


def _decode_limits(value: Any, *, label: str) -> RepositoryPreparationLimits:
    payload = _object(value, fields=set(_LIMIT_FIELDS), label=label)
    try:
        return RepositoryPreparationLimits(**payload)
    except (TypeError, RepositoryPreparationError) as exc:
        raise RepositoryPreparationError(f"{label} is invalid") from exc


def _decode_suite_binding(value: Any) -> dict[str, str]:
    payload = _object(
        value,
        fields={"suite_id", "suite_sha256", "source_snapshot_sha256"},
        label="repository suite binding",
    )
    suite_id = _string(
        payload["suite_id"],
        label="repository suite ID",
        maximum=128,
    )
    if _IDENTIFIER_RE.fullmatch(suite_id) is None:
        raise RepositoryPreparationError("repository suite ID is invalid")
    _sha256(payload["suite_sha256"], label="repository suite SHA-256")
    _sha256(
        payload["source_snapshot_sha256"],
        label="repository source snapshot SHA-256",
    )
    return payload


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8", errors="strict")
    except (MemoryError, OverflowError, RecursionError, TypeError, UnicodeError, ValueError) as exc:
        raise RepositoryPreparationError(
            "repository evidence is not canonical finite JSON"
        ) from exc


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_json(nested) for key, nested in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(nested) for nested in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(nested) for nested in value]
    return value


@dataclass(frozen=True, slots=True)
class VerifiedBareMirror:
    """Runtime path plus self-hashed, non-authenticating mirror evidence."""

    path: Path
    repository: str
    git_executable: Path
    limits: RepositoryPreparationLimits
    document: Mapping[str, Any]

    @property
    def mirror_sha256(self) -> str:
        return str(self.document["mirror_sha256"])

    @property
    def origin_url(self) -> str:
        return str(self.document["origin_url"])

    def to_dict(self) -> dict[str, Any]:
        return _thaw_json(self.document)


@dataclass(frozen=True, slots=True)
class PreparedSweBenchRepository:
    """One exported tree path plus coordinator-only preparation evidence."""

    path: Path
    mirror: VerifiedBareMirror
    limits: RepositoryPreparationLimits
    document: Mapping[str, Any]

    @property
    def preparation_sha256(self) -> str:
        return str(self.document["preparation_sha256"])

    @property
    def claim_ready(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        return _thaw_json(self.document)


PreparedRepository = PreparedSweBenchRepository


def _object(value: Any, *, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise RepositoryPreparationError(f"{label} fields are invalid")
    return value


def _string(
    value: Any,
    *,
    label: str,
    maximum: int = 4096,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str) or (not value and not allow_empty):
        raise RepositoryPreparationError(f"{label} must be a string")
    if len(value) > maximum:
        raise RepositoryPreparationError(f"{label} exceeds its character limit")
    return value


def _sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise RepositoryPreparationError(f"{label} must be lowercase SHA-256")
    return value


def _sha1(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA1_RE.fullmatch(value) is None:
        raise RepositoryPreparationError(f"{label} must be lowercase Git SHA-1")
    return value


def _integer(value: Any, *, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise RepositoryPreparationError(f"{label} must be an integer")
    return value


def _safe_json_loads(encoded: bytes) -> Any:
    if encoded.startswith(b"\xef\xbb\xbf"):
        raise RepositoryPreparationError("repository worker output has a BOM")
    try:
        text = encoded.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise RepositoryPreparationError(
            "repository worker output is not UTF-8"
        ) from exc

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise RepositoryPreparationError(
                    "repository worker output has duplicate keys"
                )
            result[key] = value
        return result

    try:
        return json.loads(
            text,
            object_pairs_hook=pairs_hook,
            parse_constant=lambda value: (_ for _ in ()).throw(
                RepositoryPreparationError(
                    f"repository worker output has invalid constant {value}"
                )
            ),
        )
    except RepositoryPreparationError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise RepositoryPreparationError(
            "repository worker output is not strict JSON"
        ) from exc


def _worker_environment() -> dict[str, str]:
    environment = {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
    }
    for name in ("SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _limits_argv(limits: RepositoryPreparationLimits) -> list[str]:
    return [
        "--timeout-seconds",
        str(limits.timeout_seconds),
        "--max-commit-bytes",
        str(limits.max_commit_bytes),
        "--max-tree-object-bytes",
        str(limits.max_tree_object_bytes),
        "--max-total-tree-bytes",
        str(limits.max_total_tree_bytes),
        "--max-tree-objects",
        str(limits.max_tree_objects),
        "--max-entries",
        str(limits.max_entries),
        "--max-path-bytes",
        str(limits.max_path_bytes),
        "--max-depth",
        str(limits.max_depth),
        "--max-blob-bytes",
        str(limits.max_blob_bytes),
        "--max-total-blob-bytes",
        str(limits.max_total_blob_bytes),
        "--max-mirror-entries",
        str(limits.max_mirror_entries),
    ]


def _stage_worker_source() -> tuple[tempfile.TemporaryDirectory[str], Path, str]:
    try:
        source = read_bounded_regular_file(
            _WORKER_SOURCE,
            max_bytes=_MAX_WORKER_SOURCE_BYTES,
            label="SWE-bench repository worker source",
        )
    except StrictJsonError as exc:
        raise RepositoryPreparationError(str(exc)) from exc
    if source.file_sha256 != _EXPECTED_WORKER_SOURCE_SHA256:
        raise RepositoryPreparationError(
            "repository worker source does not match its release anchor"
        )
    temporary = tempfile.TemporaryDirectory(prefix="ctxc-swebench-worker-")
    staged = Path(temporary.name) / "repository-worker.py"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(staged, flags, 0o600)
        try:
            view = memoryview(source.value)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise RepositoryPreparationError(
                        "could not stage repository worker source"
                    )
                view = view[written:]
        finally:
            os.close(descriptor)
    except BaseException:
        temporary.cleanup()
        raise
    return temporary, staged, source.file_sha256


def _worker_source_evidence() -> StrictFileEvidence:
    try:
        evidence = hash_bounded_regular_file(
            _WORKER_SOURCE,
            max_bytes=_MAX_WORKER_SOURCE_BYTES,
            label="repository worker source",
        )
    except StrictJsonError as exc:
        raise RepositoryPreparationError(str(exc)) from exc
    if evidence.file_sha256 != _EXPECTED_WORKER_SOURCE_SHA256:
        raise RepositoryPreparationError(
            "repository worker source does not match its release anchor"
        )
    return evidence


def _invoke_worker(
    operation: str,
    *,
    mirror_path: Path,
    repository: str,
    git_executable: Path,
    git_sha256: str,
    limits: RepositoryPreparationLimits,
    commit: str | None = None,
    output: Path | None = None,
) -> dict[str, Any]:
    temporary, worker, worker_sha256 = _stage_worker_source()
    try:
        arguments = [
            str(Path(sys.executable).resolve()),
            "-B",
            "-I",
            "-S",
            str(worker),
            operation,
            "--mirror",
            str(mirror_path),
            "--repository",
            repository,
            "--git-executable",
            str(git_executable),
            "--expected-worker-sha256",
            worker_sha256,
            "--expected-git-sha256",
            git_sha256,
            *_limits_argv(limits),
        ]
        if commit is not None:
            arguments.extend(("--commit", commit))
        if output is not None:
            arguments.extend(("--output", str(output)))
        result = run_literal_argv(
            arguments,
            cwd=Path(temporary.name),
            environment=_worker_environment(),
            limits=LiteralProcessLimits(
                timeout_seconds=limits.timeout_seconds,
                max_stdout_bytes=limits.max_worker_stdout_bytes,
                max_stderr_bytes=limits.max_worker_stderr_bytes,
            ),
        )
    finally:
        temporary.cleanup()
    if (
        result.termination_trigger is not None
        or result.execution_error is not None
        or result.cleanup_error is not None
        or not result.stdout.complete
        or not result.stderr.complete
    ):
        raise RepositoryPreparationError(
            "repository worker lifecycle failed closed"
        )
    if result.stderr.observed_bytes != 0:
        raise RepositoryPreparationError("repository worker emitted stderr")
    decoded = _safe_json_loads(result.stdout.prefix)
    if result.exit_code != 0:
        if isinstance(decoded, dict) and decoded.get("schema") == _WORKER_ERROR_SCHEMA:
            error_code = _string(
                decoded.get("error_code"),
                label="repository worker error code",
                maximum=128,
            )
            raise RepositoryPreparationError(
                f"repository worker refused the operation: {error_code}"
            )
        raise RepositoryPreparationError("repository worker exited nonzero")
    if result.stdout.observed_bytes != result.stdout.captured_bytes:
        raise RepositoryPreparationError("repository worker output was not retained")
    if not isinstance(decoded, dict) or decoded.get("schema") != _WORKER_RESULT_SCHEMA:
        raise RepositoryPreparationError("repository worker result schema is invalid")
    return decoded


def _secure_runtime_path(
    value: str | Path,
    *,
    directory: bool,
    label: str,
) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise RepositoryPreparationError(f"{label} must be an absolute path")
    path = Path(value)
    if not path.is_absolute():
        raise RepositoryPreparationError(f"{label} must be an absolute path")
    try:
        resolved = path.resolve(strict=True)
        file_stat = path.lstat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise RepositoryPreparationError(f"{label} could not be inspected") from exc
    if resolved != path or stat.S_ISLNK(file_stat.st_mode):
        raise RepositoryPreparationError(f"{label} cannot be a link or alias")
    if bool(getattr(file_stat, "st_file_attributes", 0) & 0x400):
        raise RepositoryPreparationError(f"{label} cannot be a reparse point")
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(file_stat.st_mode):
        raise RepositoryPreparationError(f"{label} has the wrong type")
    return resolved


def _decode_worker_result(
    value: Any,
    *,
    operation: str,
    repository: str,
    git_byte_count: int,
    git_sha256: str,
    worker_byte_count: int,
    worker_sha256: str,
) -> dict[str, Any]:
    payload = _object(
        value,
        fields={"schema", "operation", "repository", "mirror", "git", "worker", "export"},
        label="repository worker result",
    )
    if payload["schema"] != _WORKER_RESULT_SCHEMA or payload["operation"] != operation:
        raise RepositoryPreparationError("repository worker operation mismatch")
    if payload["repository"] != repository:
        raise RepositoryPreparationError("repository worker repository mismatch")
    git = _object(
        payload["git"],
        fields={"executable_byte_count", "executable_sha256", "version"},
        label="repository worker git evidence",
    )
    if _integer(
        git["executable_byte_count"],
        label="git executable byte count",
        minimum=1,
    ) != git_byte_count:
        raise RepositoryPreparationError("repository worker Git size mismatch")
    if _sha256(git["executable_sha256"], label="git executable SHA-256") != git_sha256:
        raise RepositoryPreparationError("repository worker Git hash mismatch")
    _string(git["version"], label="Git version", maximum=256)
    worker = _object(
        payload["worker"],
        fields={"source_byte_count", "source_sha256"},
        label="repository worker source evidence",
    )
    if _integer(
        worker["source_byte_count"],
        label="worker source byte count",
        minimum=1,
    ) != worker_byte_count:
        raise RepositoryPreparationError("repository worker source size mismatch")
    if _sha256(worker["source_sha256"], label="worker source SHA-256") != worker_sha256:
        raise RepositoryPreparationError("repository worker source hash mismatch")
    mirror = _object(
        payload["mirror"],
        fields={
            "origin_url",
            "object_format",
            "git_version",
            "config_sha256",
            "metadata",
            "origin_authenticated",
        },
        label="repository worker mirror evidence",
    )
    _string(mirror["origin_url"], label="mirror origin URL", maximum=512)
    if mirror["object_format"] != "sha1" or mirror["origin_authenticated"] is not False:
        raise RepositoryPreparationError("repository worker mirror state is invalid")
    if mirror["git_version"] != git["version"]:
        raise RepositoryPreparationError("repository worker Git version mismatch")
    _sha256(mirror["config_sha256"], label="mirror config SHA-256")
    metadata = _object(
        mirror["metadata"],
        fields={"entry_count", "metadata_sha256"},
        label="mirror metadata",
    )
    _integer(metadata["entry_count"], label="mirror metadata entry count", minimum=1)
    _sha256(metadata["metadata_sha256"], label="mirror metadata SHA-256")
    if operation == "verify-mirror" and payload["export"] is not None:
        raise RepositoryPreparationError("mirror verification returned export evidence")
    if operation in {"inspect", "prepare"} and not isinstance(
        payload["export"],
        dict,
    ):
        raise RepositoryPreparationError("repository preparation omitted export evidence")
    return payload


def _mirror_document(
    worker: dict[str, Any],
    repository: str,
    limits: RepositoryPreparationLimits,
) -> dict[str, Any]:
    mirror = worker["mirror"]
    document: dict[str, Any] = {
        "schema": MIRROR_SCHEMA,
        "status": "verified-local-bare-mirror-origin-not-authenticated",
        "repository": repository,
        "origin_url": mirror["origin_url"],
        "object_format": mirror["object_format"],
        "limits": _limits_document(limits),
        "config_sha256": mirror["config_sha256"],
        "metadata": mirror["metadata"],
        "git": worker["git"],
        "worker": worker["worker"],
        "evidence_state": {
            "local_bare_mirror_verified": True,
            "origin_url_matches_source_repository": True,
            "repository_origin_authenticated": False,
            "history_candidate_visible": False,
            "remote_candidate_visible": False,
            "claim_ready": False,
            "self_hash_authenticates_author": False,
        },
    }
    document["mirror_sha256"] = _canonical_sha256(document)
    return document


def verify_local_bare_mirror(
    mirror_path: str | Path,
    repository: str,
    git_executable: str | Path,
    *,
    limits: RepositoryPreparationLimits | None = None,
) -> VerifiedBareMirror:
    """Verify one local SHA-1 mirror without fetching or authenticating origin."""

    if not isinstance(repository, str) or _REPOSITORY_RE.fullmatch(repository) is None:
        raise RepositoryPreparationError("repository is invalid")
    selected_limits = _revalidate_limits(
        RepositoryPreparationLimits() if limits is None else limits
    )
    mirror = _secure_runtime_path(mirror_path, directory=True, label="bare mirror")
    executable = _secure_runtime_path(
        git_executable,
        directory=False,
        label="Git executable",
    )
    try:
        git_evidence = hash_bounded_regular_file(
            executable,
            max_bytes=_MAX_EXECUTABLE_BYTES,
            label="Git executable",
        )
    except StrictJsonError as exc:
        raise RepositoryPreparationError(str(exc)) from exc
    worker_evidence = _worker_source_evidence()
    raw = _invoke_worker(
        "verify-mirror",
        mirror_path=mirror,
        repository=repository,
        git_executable=executable,
        git_sha256=git_evidence.file_sha256,
        limits=selected_limits,
    )
    decoded = _decode_worker_result(
        raw,
        operation="verify-mirror",
        repository=repository,
        git_byte_count=git_evidence.byte_count,
        git_sha256=git_evidence.file_sha256,
        worker_byte_count=worker_evidence.byte_count,
        worker_sha256=worker_evidence.file_sha256,
    )
    document = _mirror_document(decoded, repository, selected_limits)
    decode_bare_mirror(document)
    return VerifiedBareMirror(
        path=mirror,
        repository=repository,
        git_executable=executable,
        limits=selected_limits,
        document=_freeze_json(document),
    )


def _revalidate_mirror(
    mirror: VerifiedBareMirror,
    limits: RepositoryPreparationLimits,
) -> VerifiedBareMirror:
    if not isinstance(mirror, VerifiedBareMirror):
        raise RepositoryPreparationError("mirror must be a VerifiedBareMirror")
    if mirror.limits != limits:
        raise RepositoryPreparationError("mirror limits do not match preparation limits")
    expected = verify_local_bare_mirror(
        mirror.path,
        mirror.repository,
        mirror.git_executable,
        limits=limits,
    )
    if _canonical_json_bytes(expected.to_dict()) != _canonical_json_bytes(
        mirror.to_dict()
    ):
        raise RepositoryPreparationError("bare mirror evidence changed")
    return expected


def _create_output_root(parent: Path, ordinal: int) -> Path:
    try:
        if any(parent.iterdir()):
            raise RepositoryPreparationError(
                "repository output parent must be a fresh empty directory"
            )
    except OSError as exc:
        raise RepositoryPreparationError(
            "repository output parent could not be inspected"
        ) from exc
    for _attempt in range(8):
        candidate = parent / (
            f"swebench-{ordinal:04d}-{secrets.token_hex(16)}"
        )
        try:
            candidate.mkdir(mode=0o700)
        except FileExistsError:
            continue
        except OSError as exc:
            raise RepositoryPreparationError(
                "could not create repository output directory"
            ) from exc
        return candidate
    raise RepositoryPreparationError("could not allocate a fresh repository output")


def _safe_cleanup_output(root: Path, parent: Path) -> None:
    try:
        root_stat = root.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise RepositoryPreparationError(
            "could not inspect failed repository output"
        ) from exc
    if (
        root.parent != parent
        or not root.name.startswith("swebench-")
        or not stat.S_ISDIR(root_stat.st_mode)
        or stat.S_ISLNK(root_stat.st_mode)
        or bool(getattr(root_stat, "st_file_attributes", 0) & 0x400)
    ):
        raise RepositoryPreparationError(
            "failed repository output could not be safely removed"
        )
    try:
        shutil.rmtree(root)
    except OSError as exc:
        raise RepositoryPreparationError(
            "failed repository output cleanup was incomplete"
        ) from exc


def _decode_export(value: Any, limits: RepositoryPreparationLimits) -> dict[str, Any]:
    export = _object(
        value,
        fields={"base_object", "tree"},
        label="repository export",
    )
    base = _object(
        export["base_object"],
        fields={"oid", "byte_count", "sha256"},
        label="repository base object",
    )
    _sha1(base["oid"], label="base object OID")
    if (
        _integer(base["byte_count"], label="base object byte count", minimum=1)
        > limits.max_commit_bytes
    ):
        raise RepositoryPreparationError("base object exceeds its limit")
    _sha256(base["sha256"], label="base object SHA-256")
    tree = _object(
        export["tree"],
        fields={
            "root_tree_oid",
            "root_tree_object_sha256",
            "tree_object_count",
            "total_tree_object_bytes",
            "tree_object_binding_fields",
            "tree_object_bindings",
            "ordered_tree_object_bindings_sha256",
            "entry_count",
            "executable_file_count",
            "total_blob_bytes",
            "path_policy",
            "entry_binding_fields",
            "entry_bindings",
            "ordered_entry_bindings_sha256",
        },
        label="repository tree evidence",
    )
    _sha1(tree["root_tree_oid"], label="root tree OID")
    _sha256(tree["root_tree_object_sha256"], label="root tree SHA-256")
    tree_count = _integer(tree["tree_object_count"], label="tree object count", minimum=1)
    if tree_count > limits.max_tree_objects:
        raise RepositoryPreparationError("tree object count exceeds its limit")
    expected_tree_fields = ["path", "tree_oid", "byte_count", "sha256"]
    if tree["tree_object_binding_fields"] != expected_tree_fields:
        raise RepositoryPreparationError("tree object binding fields mismatch")
    tree_bindings = tree["tree_object_bindings"]
    if not isinstance(tree_bindings, list) or len(tree_bindings) != tree_count:
        raise RepositoryPreparationError("tree object bindings mismatch")
    observed_tree_paths: list[str] = []
    previous_tree_path: bytes | None = None
    for index, binding in enumerate(tree_bindings):
        if not isinstance(binding, list) or len(binding) != 4:
            raise RepositoryPreparationError(f"tree object binding {index} is invalid")
        tree_path = _string(
            binding[0],
            label=f"tree object binding {index} path",
            maximum=limits.max_path_bytes,
            allow_empty=True,
        )
        encoded_tree_path = tree_path.encode("utf-8", errors="strict")
        if len(encoded_tree_path) > limits.max_path_bytes:
            raise RepositoryPreparationError("tree object path exceeds its byte limit")
        if previous_tree_path is not None and encoded_tree_path <= previous_tree_path:
            raise RepositoryPreparationError("tree object paths are not canonical")
        previous_tree_path = encoded_tree_path
        if tree_path:
            components = tree_path.split("/")
            if len(components) > limits.max_depth:
                raise RepositoryPreparationError(
                    "tree object path exceeds its depth limit"
                )
            try:
                for component in components:
                    _portable_component(component.encode("utf-8", errors="strict"))
            except (UnicodeError, WorkerError) as exc:
                raise RepositoryPreparationError(
                    "tree object path is not portable"
                ) from exc
        observed_tree_paths.append(tree_path)
        _sha1(binding[1], label=f"tree object binding {index} OID")
        if (
            _integer(
                binding[2],
                label=f"tree object binding {index} bytes",
                minimum=0,
            )
            > limits.max_tree_object_bytes
        ):
            raise RepositoryPreparationError("tree object exceeds its byte limit")
        _sha256(binding[3], label=f"tree object binding {index} SHA-256")
    if not tree_bindings or tree_bindings[0][0] != "":
        raise RepositoryPreparationError("root tree binding is missing")
    if (
        tree_bindings[0][1] != tree["root_tree_oid"]
        or tree_bindings[0][3] != tree["root_tree_object_sha256"]
    ):
        raise RepositoryPreparationError("root tree binding mismatch")
    total_tree_bytes = _integer(
        tree["total_tree_object_bytes"],
        label="total tree object bytes",
    )
    if total_tree_bytes > limits.max_total_tree_bytes:
        raise RepositoryPreparationError("tree object bytes exceed their limit")
    if sum(int(binding[2]) for binding in tree_bindings) != total_tree_bytes:
        raise RepositoryPreparationError("tree object byte summary mismatch")
    if _canonical_sha256(tree_bindings) != _sha256(
        tree["ordered_tree_object_bindings_sha256"],
        label="ordered tree object bindings SHA-256",
    ):
        raise RepositoryPreparationError("tree object binding digest mismatch")
    entry_count = _integer(tree["entry_count"], label="tree entry count")
    if entry_count > limits.max_entries:
        raise RepositoryPreparationError("tree entry count exceeds its limit")
    executable_count = _integer(
        tree["executable_file_count"],
        label="executable file count",
    )
    total_bytes = _integer(tree["total_blob_bytes"], label="total blob bytes")
    if total_bytes > limits.max_total_blob_bytes:
        raise RepositoryPreparationError("total blob bytes exceed the limit")
    if tree["path_policy"] != "portable-utf8-nfc-regular-files-v1":
        raise RepositoryPreparationError("tree path policy mismatch")
    expected_entry_fields = [
        "path",
        "git_mode",
        "blob_oid",
        "byte_count",
        "sha256",
    ]
    if tree["entry_binding_fields"] != expected_entry_fields:
        raise RepositoryPreparationError("tree entry binding fields mismatch")
    bindings = tree["entry_bindings"]
    if not isinstance(bindings, list) or len(bindings) != entry_count:
        raise RepositoryPreparationError("tree entry bindings mismatch")
    seen_paths: set[str] = set()
    expected_tree_paths = {""}
    observed_total = 0
    observed_executable = 0
    previous_path: bytes | None = None
    for index, binding in enumerate(bindings):
        if not isinstance(binding, list) or len(binding) != 5:
            raise RepositoryPreparationError(f"tree entry binding {index} is invalid")
        path = _string(
            binding[0],
            label=f"tree entry binding {index} path",
            maximum=limits.max_path_bytes,
        )
        encoded_path = path.encode("utf-8", errors="strict")
        if len(encoded_path) > limits.max_path_bytes:
            raise RepositoryPreparationError("tree path exceeds its byte limit")
        if previous_path is not None and encoded_path <= previous_path:
            raise RepositoryPreparationError("tree entry paths are not canonical")
        previous_path = encoded_path
        components = path.split("/")
        if len(components) > limits.max_depth:
            raise RepositoryPreparationError("tree path exceeds its depth limit")
        try:
            for component in components:
                _portable_component(component.encode("utf-8", errors="strict"))
        except (UnicodeError, WorkerError) as exc:
            raise RepositoryPreparationError("tree path is not portable") from exc
        expected_tree_paths.update(
            PurePosixPath(*components[:component_count]).as_posix()
            for component_count in range(1, len(components))
        )
        collision = unicodedata.normalize("NFC", path).casefold()
        if collision in seen_paths:
            raise RepositoryPreparationError("tree paths collide portably")
        seen_paths.add(collision)
        if binding[1] not in {"100644", "100755"}:
            raise RepositoryPreparationError("tree entry mode is invalid")
        _sha1(binding[2], label=f"tree entry binding {index} blob OID")
        byte_count = _integer(
            binding[3],
            label=f"tree entry binding {index} byte count",
        )
        if byte_count > limits.max_blob_bytes:
            raise RepositoryPreparationError("tree blob exceeds its byte limit")
        _sha256(binding[4], label=f"tree entry binding {index} SHA-256")
        observed_total += byte_count
        observed_executable += binding[1] == "100755"
    if observed_total != total_bytes or observed_executable != executable_count:
        raise RepositoryPreparationError("tree entry summary mismatch")
    if _canonical_sha256(bindings) != _sha256(
        tree["ordered_entry_bindings_sha256"],
        label="ordered entry bindings SHA-256",
    ):
        raise RepositoryPreparationError("tree entry binding digest mismatch")
    if set(observed_tree_paths) != expected_tree_paths:
        raise RepositoryPreparationError(
            "tree object bindings contain an empty or missing directory"
        )
    return export


_EVIDENCE_STATE = {
    "task_repository_tree_prepared": True,
    "candidate_mount_created": False,
    "candidate_mount_isolation_verified": False,
    "network_isolation_verified": False,
    "repository_origin_authenticated": False,
    "repository_redistribution_reviewed": False,
    "git_executable_security_reviewed": False,
    "execution_performed": False,
    "grading_performed": False,
    "external_score_generated": False,
    "external_usefulness_claimed": False,
    "claim_ready": False,
    "self_hash_authenticates_author": False,
}


def _preparation_document(
    *,
    mirror: VerifiedBareMirror,
    base_commit: str,
    repository: str,
    instance_id: str,
    source_ordinal: int,
    source_record_sha256: str,
    export: dict[str, Any],
    suite_binding: Mapping[str, Any] | None,
) -> dict[str, Any]:
    normalized_suite = (
        None
        if suite_binding is None
        else _decode_suite_binding(dict(suite_binding))
    )
    document: dict[str, Any] = {
        "schema": PREPARATION_SCHEMA,
        "status": "tree-exported-coordinator-only-not-claim-ready",
        "source_binding": {
            "suite": normalized_suite,
            "source_ordinal": source_ordinal,
            "source_record_sha256": source_record_sha256,
            "instance_id": instance_id,
            "repository": repository,
            "base_commit": base_commit,
        },
        "limits": _limits_document(mirror.limits),
        "mirror": mirror.to_dict(),
        "base_object": export["base_object"],
        "tree": export["tree"],
        "export_policy": {
            "materialization": "raw-git-blob-bytes-no-checkout-no-filters",
            "output_path_serialized": False,
            "manifest_candidate_visible": False,
            "git_metadata_exported": False,
            "history_exported": False,
            "remotes_exported": False,
            "untracked_material_exported": False,
            "symlinks_exported": False,
            "submodules_exported": False,
            "special_files_exported": False,
            "post_export_verified": True,
        },
        "evidence_state": dict(_EVIDENCE_STATE),
    }
    document["preparation_sha256"] = _canonical_sha256(document)
    return document


def decode_repository_preparation(value: Any) -> dict[str, Any]:
    """Strictly decode one self-hashed, permanently non-claim-ready manifest."""

    payload = _object(
        value,
        fields={
            "schema",
            "status",
            "source_binding",
            "limits",
            "mirror",
            "base_object",
            "tree",
            "export_policy",
            "evidence_state",
            "preparation_sha256",
        },
        label="repository preparation",
    )
    if payload["schema"] != PREPARATION_SCHEMA:
        raise RepositoryPreparationError("repository preparation schema mismatch")
    if payload["status"] != "tree-exported-coordinator-only-not-claim-ready":
        raise RepositoryPreparationError("repository preparation status mismatch")
    source = _object(
        payload["source_binding"],
        fields={
            "suite",
            "source_ordinal",
            "source_record_sha256",
            "instance_id",
            "repository",
            "base_commit",
        },
        label="repository source binding",
    )
    _integer(source["source_ordinal"], label="source ordinal")
    _sha256(source["source_record_sha256"], label="source record SHA-256")
    if (
        not isinstance(source["instance_id"], str)
        or _INSTANCE_RE.fullmatch(source["instance_id"]) is None
    ):
        raise RepositoryPreparationError("source instance ID is invalid")
    if (
        not isinstance(source["repository"], str)
        or _REPOSITORY_RE.fullmatch(source["repository"]) is None
    ):
        raise RepositoryPreparationError("source repository is invalid")
    if not source["instance_id"].startswith(
        source["repository"].replace("/", "__") + "-"
    ):
        raise RepositoryPreparationError(
            "source instance ID does not match its repository"
        )
    _sha1(source["base_commit"], label="source base commit")
    if source["suite"] is not None:
        _decode_suite_binding(source["suite"])
    limits = _decode_limits(payload["limits"], label="repository preparation limits")
    mirror = decode_bare_mirror(payload["mirror"])
    mirror_limits = _decode_limits(
        mirror["limits"],
        label="bare mirror limits",
    )
    if mirror_limits != limits:
        raise RepositoryPreparationError(
            "mirror and preparation limits do not match"
        )
    if mirror["repository"] != source["repository"]:
        raise RepositoryPreparationError("mirror/source repository mismatch")
    _decode_export(
        {"base_object": payload["base_object"], "tree": payload["tree"]},
        limits,
    )
    if payload["base_object"]["oid"] != source["base_commit"]:
        raise RepositoryPreparationError("base object/source commit mismatch")
    policy = _object(
        payload["export_policy"],
        fields={
            "materialization",
            "output_path_serialized",
            "manifest_candidate_visible",
            "git_metadata_exported",
            "history_exported",
            "remotes_exported",
            "untracked_material_exported",
            "symlinks_exported",
            "submodules_exported",
            "special_files_exported",
            "post_export_verified",
        },
        label="repository export policy",
    )
    if policy["materialization"] != "raw-git-blob-bytes-no-checkout-no-filters":
        raise RepositoryPreparationError("repository materialization policy mismatch")
    if policy["post_export_verified"] is not True:
        raise RepositoryPreparationError("repository post-export verification is false")
    for name in set(policy) - {"materialization", "post_export_verified"}:
        if policy[name] is not False:
            raise RepositoryPreparationError(f"repository export policy {name} is invalid")
    if payload["evidence_state"] != _EVIDENCE_STATE:
        raise RepositoryPreparationError("repository evidence state mismatch")
    claimed = _sha256(
        payload["preparation_sha256"],
        label="repository preparation SHA-256",
    )
    unsigned = dict(payload)
    unsigned.pop("preparation_sha256")
    if _canonical_sha256(unsigned) != claimed:
        raise RepositoryPreparationError("repository preparation self-hash mismatch")
    return payload


def decode_bare_mirror(value: Any) -> dict[str, Any]:
    """Strictly decode mirror evidence without treating it as provenance auth."""

    payload = _object(
        value,
        fields={
            "schema",
            "status",
            "repository",
            "origin_url",
            "object_format",
            "limits",
            "config_sha256",
            "metadata",
            "git",
            "worker",
            "evidence_state",
            "mirror_sha256",
        },
        label="bare mirror evidence",
    )
    if payload["schema"] != MIRROR_SCHEMA:
        raise RepositoryPreparationError("bare mirror schema mismatch")
    if payload["status"] != "verified-local-bare-mirror-origin-not-authenticated":
        raise RepositoryPreparationError("bare mirror status mismatch")
    repository = _string(payload["repository"], label="mirror repository", maximum=201)
    if _REPOSITORY_RE.fullmatch(repository) is None:
        raise RepositoryPreparationError("mirror repository is invalid")
    if payload["origin_url"] not in {
        f"https://github.com/{repository}",
        f"https://github.com/{repository}.git",
    }:
        raise RepositoryPreparationError("mirror origin URL mismatch")
    if payload["object_format"] != "sha1":
        raise RepositoryPreparationError("mirror object format mismatch")
    limits = _decode_limits(payload["limits"], label="bare mirror limits")
    _sha256(payload["config_sha256"], label="mirror config SHA-256")
    metadata = _object(
        payload["metadata"],
        fields={"entry_count", "metadata_sha256"},
        label="mirror metadata",
    )
    entry_count = _integer(
        metadata["entry_count"],
        label="mirror entry count",
        minimum=1,
    )
    if entry_count > limits.max_mirror_entries:
        raise RepositoryPreparationError("mirror entry count exceeds its limit")
    _sha256(metadata["metadata_sha256"], label="mirror metadata SHA-256")
    git = _object(
        payload["git"],
        fields={"executable_byte_count", "executable_sha256", "version"},
        label="mirror Git evidence",
    )
    _integer(git["executable_byte_count"], label="Git byte count", minimum=1)
    _sha256(git["executable_sha256"], label="Git SHA-256")
    _string(git["version"], label="Git version", maximum=256)
    worker = _object(
        payload["worker"],
        fields={"source_byte_count", "source_sha256"},
        label="mirror worker evidence",
    )
    _integer(worker["source_byte_count"], label="worker byte count", minimum=1)
    _sha256(worker["source_sha256"], label="worker SHA-256")
    expected_state = {
        "local_bare_mirror_verified": True,
        "origin_url_matches_source_repository": True,
        "repository_origin_authenticated": False,
        "history_candidate_visible": False,
        "remote_candidate_visible": False,
        "claim_ready": False,
        "self_hash_authenticates_author": False,
    }
    if payload["evidence_state"] != expected_state:
        raise RepositoryPreparationError("mirror evidence state mismatch")
    claimed = _sha256(payload["mirror_sha256"], label="mirror self-hash")
    unsigned = dict(payload)
    unsigned.pop("mirror_sha256")
    if _canonical_sha256(unsigned) != claimed:
        raise RepositoryPreparationError("mirror self-hash mismatch")
    return payload


def _hash_prepared_file(path: Path, *, maximum: int) -> tuple[int, str, str]:
    try:
        with _open_regular_file(path, label="prepared repository file") as (
            descriptor,
            file_stat,
        ):
            if file_stat.st_nlink != 1:
                raise RepositoryPreparationError(
                    "prepared repository contains a hard-linked file"
                )
            if file_stat.st_size > maximum:
                raise RepositoryPreparationError(
                    "prepared repository file exceeds its limit"
                )
            sha256 = hashlib.sha256()
            git_sha1 = hashlib.sha1(usedforsecurity=False)
            git_sha1.update(f"blob {file_stat.st_size}\0".encode("ascii"))
            total = 0
            while True:
                block = os.read(descriptor, min(1024 * 1024, maximum - total + 1))
                if not block:
                    break
                total += len(block)
                if total > maximum:
                    raise RepositoryPreparationError(
                        "prepared repository file exceeds its limit"
                    )
                sha256.update(block)
                git_sha1.update(block)
    except StrictJsonError as exc:
        raise RepositoryPreparationError(str(exc)) from exc
    return total, sha256.hexdigest(), git_sha1.hexdigest()


def _assert_no_extended_attributes(path: Path, *, label: str) -> None:
    listxattr = getattr(os, "listxattr", None)
    if listxattr is None:
        return
    try:
        attributes = listxattr(path, follow_symlinks=False)
    except OSError as exc:
        if exc.errno in {errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)}:
            return
        raise RepositoryPreparationError(
            f"{label} extended attributes could not be inspected"
        ) from exc
    except TypeError as exc:
        raise RepositoryPreparationError(
            f"{label} extended attributes could not be inspected"
        ) from exc
    unexpected = {
        attribute
        for attribute in attributes
        if attribute not in {"security.selinux", b"security.selinux"}
    }
    if unexpected:
        raise RepositoryPreparationError(
            f"{label} contains extended attributes"
        )


def _assert_windows_streams_and_attributes(
    path: Path,
    file_stat: os.stat_result,
    *,
    directory: bool,
    label: str,
) -> None:
    if os.name != "nt":
        return
    attributes = int(getattr(file_stat, "st_file_attributes", 0))
    allowed_attributes = 0x10 if directory else 0x20 | 0x80
    if (
        bool(attributes & 0x10) != directory
        or attributes & ~allowed_attributes
        or int(getattr(file_stat, "st_reparse_tag", 0)) != 0
    ):
        raise RepositoryPreparationError(f"{label} has unsupported file attributes")

    import ctypes
    from ctypes import wintypes

    class _StreamData(ctypes.Structure):
        _fields_ = [
            ("stream_size", ctypes.c_longlong),
            ("stream_name", wintypes.WCHAR * 296),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    find_first = kernel32.FindFirstStreamW
    find_first.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(_StreamData),
        wintypes.DWORD,
    ]
    find_first.restype = wintypes.HANDLE
    find_next = kernel32.FindNextStreamW
    find_next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_StreamData)]
    find_next.restype = wintypes.BOOL
    find_close = kernel32.FindClose
    find_close.argtypes = [wintypes.HANDLE]
    find_close.restype = wintypes.BOOL

    raw_path = str(path)
    if raw_path.startswith("\\\\"):
        raw_path = "\\\\?\\UNC\\" + raw_path[2:]
    elif not raw_path.startswith("\\\\?\\"):
        raw_path = "\\\\?\\" + raw_path
    data = _StreamData()
    handle = find_first(raw_path, 0, ctypes.byref(data), 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        error = ctypes.get_last_error()
        if error == 38:  # ERROR_HANDLE_EOF: no streams are reported.
            return
        raise RepositoryPreparationError(f"{label} streams could not be inspected")
    try:
        while True:
            if data.stream_name != "::$DATA":
                raise RepositoryPreparationError(
                    f"{label} contains an alternate data stream"
                )
            if find_next(handle, ctypes.byref(data)):
                continue
            error = ctypes.get_last_error()
            if error != 38:  # ERROR_HANDLE_EOF
                raise RepositoryPreparationError(
                    f"{label} streams could not be inspected"
                )
            break
    finally:
        find_close(handle)


def _verify_output_tree(
    path: Path,
    document: dict[str, Any],
    limits: RepositoryPreparationLimits,
) -> None:
    root = _secure_runtime_path(path, directory=True, label="prepared repository")
    expected = {binding[0]: binding for binding in document["tree"]["entry_bindings"]}
    expected_directories = {""}
    for relative in expected:
        parts = PurePosixPath(relative).parts
        expected_directories.update(
            PurePosixPath(*parts[:index]).as_posix()
            for index in range(1, len(parts))
        )
    actual_paths: list[str] = []
    actual_directories = {""}
    scanned_entries = 0
    pending = [root]
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise RepositoryPreparationError(
            "prepared repository root could not be inspected"
        ) from exc
    if os.name != "nt" and stat.S_IMODE(root_stat.st_mode) != 0o700:
        raise RepositoryPreparationError(
            "prepared repository root mode mismatch"
        )
    _assert_no_extended_attributes(root, label="prepared repository root")
    _assert_windows_streams_and_attributes(
        root,
        root_stat,
        directory=True,
        label="prepared repository root",
    )
    while pending:
        directory = pending.pop()
        try:
            children = os.scandir(directory)
        except OSError as exc:
            raise RepositoryPreparationError(
                "prepared repository could not be scanned"
            ) from exc
        try:
            with children:
                for child in children:
                    scanned_entries += 1
                    if (
                        scanned_entries
                        > limits.max_entries + limits.max_tree_objects
                    ):
                        raise RepositoryPreparationError(
                            "prepared repository entry count exceeds its limit"
                        )
                    try:
                        file_stat = child.stat(follow_symlinks=False)
                    except OSError as exc:
                        raise RepositoryPreparationError(
                            "prepared repository entry could not be inspected"
                        ) from exc
                    if child.is_symlink() or bool(
                        getattr(file_stat, "st_file_attributes", 0) & 0x400
                    ):
                        raise RepositoryPreparationError(
                            "prepared repository contains a link or reparse point"
                        )
                    child_path = Path(child.path)
                    if stat.S_ISDIR(file_stat.st_mode):
                        if (
                            os.name != "nt"
                            and stat.S_IMODE(file_stat.st_mode) != 0o700
                        ):
                            raise RepositoryPreparationError(
                                "prepared repository directory mode mismatch"
                            )
                        relative = child_path.relative_to(root).as_posix()
                        actual_directories.add(relative)
                        _assert_no_extended_attributes(
                            child_path,
                            label="prepared repository directory",
                        )
                        _assert_windows_streams_and_attributes(
                            child_path,
                            file_stat,
                            directory=True,
                            label="prepared repository directory",
                        )
                        pending.append(child_path)
                    elif stat.S_ISREG(file_stat.st_mode):
                        _assert_windows_streams_and_attributes(
                            child_path,
                            file_stat,
                            directory=False,
                            label="prepared repository file",
                        )
                        actual_paths.append(
                            child_path.relative_to(root).as_posix()
                        )
                    else:
                        raise RepositoryPreparationError(
                            "prepared repository contains a special file"
                        )
        except OSError as exc:
            raise RepositoryPreparationError(
                "prepared repository could not be scanned"
            ) from exc
    actual_paths.sort(key=lambda item: item.encode("utf-8", errors="strict"))
    if actual_paths != list(expected):
        raise RepositoryPreparationError("prepared repository path set mismatch")
    if actual_directories != expected_directories:
        raise RepositoryPreparationError(
            "prepared repository directory set mismatch"
        )
    for relative in actual_paths:
        binding = expected[relative]
        candidate = root.joinpath(*PurePosixPath(relative).parts)
        byte_count, sha256, blob_oid = _hash_prepared_file(
            candidate,
            maximum=limits.max_blob_bytes,
        )
        if [byte_count, sha256, blob_oid] != [binding[3], binding[4], binding[2]]:
            raise RepositoryPreparationError(
                "prepared repository file evidence mismatch"
            )
        _assert_no_extended_attributes(
            candidate,
            label="prepared repository file",
        )
        if os.name != "nt":
            actual_mode = stat.S_IMODE(
                candidate.stat(follow_symlinks=False).st_mode
            )
            expected_mode = 0o755 if binding[1] == "100755" else 0o644
            if actual_mode != expected_mode:
                raise RepositoryPreparationError(
                    "prepared repository executable mode mismatch"
                )


def prepare_repository_from_commit(
    mirror: VerifiedBareMirror,
    base_commit: str,
    repository: str,
    instance_id: str,
    source_ordinal: int,
    source_record_sha256: str,
    output_parent: str | Path,
    *,
    limits: RepositoryPreparationLimits | None = None,
    suite_binding: Mapping[str, Any] | None = None,
) -> PreparedSweBenchRepository:
    """Export one exact commit into a fresh coordinator-only regular-file tree."""

    if not isinstance(base_commit, str) or _SHA1_RE.fullmatch(base_commit) is None:
        raise RepositoryPreparationError("base commit is invalid")
    if not isinstance(repository, str) or _REPOSITORY_RE.fullmatch(repository) is None:
        raise RepositoryPreparationError("repository is invalid")
    if not isinstance(instance_id, str) or _INSTANCE_RE.fullmatch(instance_id) is None:
        raise RepositoryPreparationError("instance ID is invalid")
    if not instance_id.startswith(repository.replace("/", "__") + "-"):
        raise RepositoryPreparationError(
            "instance ID does not match its repository"
        )
    ordinal = _integer(source_ordinal, label="source ordinal")
    _sha256(source_record_sha256, label="source record SHA-256")
    selected_limits = _revalidate_limits(
        RepositoryPreparationLimits() if limits is None else limits
    )
    verified_mirror = _revalidate_mirror(mirror, selected_limits)
    if verified_mirror.repository != repository:
        raise RepositoryPreparationError("mirror repository mismatch")
    parent = _secure_runtime_path(
        output_parent,
        directory=True,
        label="repository output parent",
    )
    root = _create_output_root(parent, ordinal)
    try:
        worker_evidence = _worker_source_evidence()
        raw = _invoke_worker(
            "prepare",
            mirror_path=verified_mirror.path,
            repository=repository,
            git_executable=verified_mirror.git_executable,
            git_sha256=str(
                verified_mirror.document["git"]["executable_sha256"]
            ),
            limits=selected_limits,
            commit=base_commit,
            output=root,
        )
        decoded = _decode_worker_result(
            raw,
            operation="prepare",
            repository=repository,
            git_byte_count=int(
                verified_mirror.document["git"]["executable_byte_count"]
            ),
            git_sha256=str(
                verified_mirror.document["git"]["executable_sha256"]
            ),
            worker_byte_count=worker_evidence.byte_count,
            worker_sha256=worker_evidence.file_sha256,
        )
        if (
            _mirror_document(decoded, repository, selected_limits)
            != verified_mirror.to_dict()
        ):
            raise RepositoryPreparationError(
                "mirror evidence changed during repository preparation"
            )
        export = _decode_export(decoded["export"], selected_limits)
        if export["base_object"]["oid"] != base_commit:
            raise RepositoryPreparationError("exported base commit mismatch")
        document = _preparation_document(
            mirror=verified_mirror,
            base_commit=base_commit,
            repository=repository,
            instance_id=instance_id,
            source_ordinal=ordinal,
            source_record_sha256=source_record_sha256,
            export=export,
            suite_binding=suite_binding,
        )
        _verify_output_tree(root, document, selected_limits)
        prepared = PreparedSweBenchRepository(
            path=root,
            mirror=verified_mirror,
            limits=selected_limits,
            document=_freeze_json(document),
        )
    except BaseException as exc:
        try:
            _safe_cleanup_output(root, parent)
        except RepositoryPreparationError as cleanup_exc:
            raise RepositoryPreparationError(
                f"repository preparation failed and cleanup was incomplete: {cleanup_exc}"
            ) from exc
        if isinstance(exc, RepositoryPreparationError):
            raise
        if isinstance(exc, StrictJsonError):
            raise RepositoryPreparationError(str(exc)) from exc
        raise RepositoryPreparationError(
            f"repository preparation failed: {type(exc).__name__}"
        ) from exc
    return prepared


def prepare_task_repository(
    source: VerifiedSweBenchSource,
    ordinal: int,
    mirror: VerifiedBareMirror,
    output_parent: str | Path,
    *,
    limits: RepositoryPreparationLimits | None = None,
) -> PreparedSweBenchRepository:
    """Derive repository identity only from a revalidated source-suite row."""

    try:
        suite, records = _revalidate_verified_source(source)
    except SweBenchError as exc:
        raise RepositoryPreparationError(str(exc)) from exc
    selected_ordinal = _integer(ordinal, label="source ordinal")
    if selected_ordinal >= len(records):
        raise RepositoryPreparationError("source ordinal is outside the suite")
    record = records[selected_ordinal]
    source_record_sha256 = _source_canonical_sha256(record)
    suite_binding = {
        "suite_id": suite.suite_id,
        "suite_sha256": suite.suite_sha256,
        "source_snapshot_sha256": suite.snapshot_sha256,
    }
    prepared = prepare_repository_from_commit(
        mirror,
        record["base_commit"],
        record["repo"],
        record["instance_id"],
        selected_ordinal,
        source_record_sha256,
        output_parent,
        limits=limits,
        suite_binding=suite_binding,
    )
    try:
        return verify_prepared_repository(prepared, source=source)
    except BaseException as exc:
        parent = _secure_runtime_path(
            output_parent,
            directory=True,
            label="repository output parent",
        )
        try:
            _safe_cleanup_output(prepared.path, parent)
        except RepositoryPreparationError as cleanup_exc:
            raise RepositoryPreparationError(
                "source-bound repository verification failed and cleanup was "
                f"incomplete: {cleanup_exc}"
            ) from exc
        raise


def verify_prepared_repository(
    prepared: PreparedSweBenchRepository,
    *,
    source: VerifiedSweBenchSource | None = None,
) -> PreparedSweBenchRepository:
    """Revalidate a publicly constructible preparation and its exact tree."""

    if not isinstance(prepared, PreparedSweBenchRepository):
        raise RepositoryPreparationError(
            "prepared repository must be PreparedSweBenchRepository"
        )
    runtime_limits = _revalidate_limits(prepared.limits)
    decoded = decode_repository_preparation(prepared.to_dict())
    if _decode_limits(
        decoded["limits"],
        label="repository preparation limits",
    ) != runtime_limits:
        raise RepositoryPreparationError(
            "prepared repository runtime limits do not match its evidence"
        )
    if not isinstance(prepared.mirror, VerifiedBareMirror):
        raise RepositoryPreparationError(
            "prepared repository mirror evidence is invalid"
        )
    mirror_document = decode_bare_mirror(prepared.mirror.to_dict())
    if mirror_document != decoded["mirror"]:
        raise RepositoryPreparationError(
            "prepared repository runtime mirror does not match its evidence"
        )
    if prepared.mirror.limits != runtime_limits:
        raise RepositoryPreparationError(
            "prepared repository mirror limits do not match"
        )
    worker_evidence = _worker_source_evidence()
    raw = _invoke_worker(
        "inspect",
        mirror_path=prepared.mirror.path,
        repository=str(decoded["source_binding"]["repository"]),
        git_executable=prepared.mirror.git_executable,
        git_sha256=str(mirror_document["git"]["executable_sha256"]),
        limits=runtime_limits,
        commit=str(decoded["source_binding"]["base_commit"]),
    )
    inspected = _decode_worker_result(
        raw,
        operation="inspect",
        repository=str(decoded["source_binding"]["repository"]),
        git_byte_count=int(mirror_document["git"]["executable_byte_count"]),
        git_sha256=str(mirror_document["git"]["executable_sha256"]),
        worker_byte_count=worker_evidence.byte_count,
        worker_sha256=worker_evidence.file_sha256,
    )
    if (
        _mirror_document(inspected, prepared.mirror.repository, runtime_limits)
        != mirror_document
    ):
        raise RepositoryPreparationError(
            "prepared repository mirror changed during verification"
        )
    inspected_export = _decode_export(inspected["export"], runtime_limits)
    retained_export = {
        "base_object": decoded["base_object"],
        "tree": decoded["tree"],
    }
    if _canonical_json_bytes(inspected_export) != _canonical_json_bytes(
        retained_export
    ):
        raise RepositoryPreparationError(
            "prepared repository raw Git evidence mismatch"
        )
    _decode_export(
        retained_export,
        runtime_limits,
    )
    _verify_output_tree(prepared.path, decoded, runtime_limits)
    if source is not None:
        try:
            suite, records = _revalidate_verified_source(source)
        except SweBenchError as exc:
            raise RepositoryPreparationError(str(exc)) from exc
        source_binding = decoded["source_binding"]
        ordinal = int(source_binding["source_ordinal"])
        if ordinal >= len(records):
            raise RepositoryPreparationError(
                "prepared repository source ordinal is outside the suite"
            )
        record = records[ordinal]
        expected_binding = {
            "suite": {
                "suite_id": suite.suite_id,
                "suite_sha256": suite.suite_sha256,
                "source_snapshot_sha256": suite.snapshot_sha256,
            },
            "source_ordinal": ordinal,
            "source_record_sha256": _source_canonical_sha256(record),
            "instance_id": record["instance_id"],
            "repository": record["repo"],
            "base_commit": record["base_commit"],
        }
        if source_binding != expected_binding:
            raise RepositoryPreparationError(
                "prepared repository does not match the verified source row"
            )
    return prepared


def write_repository_preparation(
    path: str | Path,
    prepared: PreparedSweBenchRepository,
) -> None:
    """Write a verified coordinator-only manifest outside the prepared tree."""

    verified = verify_prepared_repository(prepared)
    output = Path(path)
    try:
        if output.resolve(strict=False).is_relative_to(verified.path.resolve()):
            raise RepositoryPreparationError(
                "repository manifest cannot be written inside the prepared tree"
            )
    except (OSError, RuntimeError, ValueError) as exc:
        raise RepositoryPreparationError(
            "repository manifest path could not be inspected"
        ) from exc
    rendered = json.dumps(
        verified.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ) + "\n"
    try:
        atomic_write_text(output, rendered, overwrite=False)
    except FileExistsError as exc:
        raise RepositoryPreparationError(
            "repository manifest output already exists"
        ) from exc
