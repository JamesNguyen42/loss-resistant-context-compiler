"""Controller-owned, bounded SWE-bench candidate-run evidence.

This source/sdist-only module launches one literal controller command for every
prepared task in the selected cohort.  It writes a two-field candidate request,
checks that a caller-provided workspace starts as an exact copy of the prepared
Git tree, retains bounded raw stdout/stderr artifacts, and derives prediction
captures only by replaying those raw stdout bytes.

The lifecycle helper used here is deliberately not a sandbox.  Consequently a
valid ledger never claims mount, filesystem, network, user, PID, image, model,
token, trajectory, grading, score, or resolution authentication.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import stat
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from context_compiler.atomic import atomic_write_text
from context_compiler.path_safety import ParentDirectoryGuard, PathBoundaryError

from .json_io import (
    StrictJsonError,
    StrictJsonLimits,
    load_strict_json_file,
    read_bounded_regular_file,
)
from .literal_process import (
    LiteralProcessError,
    LiteralProcessLimits,
    LiteralProcessResult,
    StreamEvidence,
    run_literal_argv,
)
from .swebench import VerifiedSweBenchSource
from .swebench import _canonical_sha256 as _source_canonical_sha256
from .swebench_prediction import (
    CandidatePatchCapture,
    PredictionLedgerError,
    RepositoryPreparationOutcome,
    RetainedCaptureEvidence,
    SystemIdentity,
    _revalidate_preparations,
    _source_and_tasks,
    _system_identity,
)
from .swebench_repository import (
    RepositoryPreparationError,
    verify_prepared_repository,
)

RUN_LEDGER_SCHEMA = "ctxc-swebench-run-ledger-0.1"
CONTROLLER_RESULT_SCHEMA = "ctxc-swebench-controller-result-0.1"
CONTROLLER_PROTOCOL = "append-request-json-and-workspace-path-v1"

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_OPAQUE_ID_RE = re.compile(r"task-[0-9a-f]{64}\Z")
_INSTANCE_RE = re.compile(
    r"[A-Za-z0-9_.-]{1,100}__[A-Za-z0-9_.-]{1,100}-[0-9]{1,12}\Z"
)
_CODE_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_TOKENIZER_RE = re.compile(r"tokenizer:[a-z0-9][a-z0-9._-]{0,127}\Z")
_MAX_SELECTED_TASKS = 10_000
_MAX_HARD_LEDGER_BYTES = 128 * 1024 * 1024
_MAX_HARD_REQUEST_BYTES = 128 * 1024 * 1024
_MAX_HARD_PATCH_BYTES = 64 * 1024 * 1024
_MAX_HARD_TRAJECTORY_BYTES = 128 * 1024 * 1024
_MAX_HARD_WORKSPACE_BYTES = 32 * 1024 * 1024 * 1024
_MAX_HARD_WORKSPACE_FILE_BYTES = 4 * 1024 * 1024 * 1024
_MAX_HARD_WORKSPACE_ENTRIES = 2_000_000
_MAX_HARD_EXECUTABLE_BYTES = 1024 * 1024 * 1024
_MAX_HARD_TOKEN_COUNT = 10**12
_MAX_JSON_DEPTH = 32
_MAX_JSON_NODES = 1_000_000
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

_PREPARATION_STATUSES = frozenset({"not-attempted", "refused", "prepared"})
_PROCESS_TRIGGERS = frozenset(
    {
        "timeout",
        "stdout_limit",
        "stderr_limit",
        "stream_observation_failed",
        "process_observation_failed",
    }
)
_DISPOSITIONS = (
    "repository-not-attempted",
    "repository-preparation-refused",
    "workspace-unavailable",
    "workspace-initial-mismatch",
    "launch-failed",
    "execution-timeout",
    "stdout-limit-exceeded",
    "stderr-limit-exceeded",
    "stream-observation-failed",
    "process-observation-failed",
    "cleanup-failed",
    "nonzero-exit",
    "malformed-controller-output",
    "candidate-output-oversized",
    "workspace-observation-failed",
    "run-captured",
)


class SweBenchRunError(ValueError):
    """A run request or retained artifact violated the frozen boundary."""


class _OversizedPatch(SweBenchRunError):
    """Internal signal retaining a bounded oversize witness."""

    def __init__(self, prefix: bytes, observed_bytes: int) -> None:
        super().__init__("controller model_patch exceeds its byte limit")
        self.prefix = prefix
        self.observed_bytes = observed_bytes


def _bounded_integer(
    value: object,
    *,
    label: str,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise SweBenchRunError(
            f"{label} must be an integer from {minimum} through {maximum}"
        )
    return value


def _finite_number(value: object, *, label: str, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SweBenchRunError(f"{label} must be a finite non-negative number")
    try:
        converted = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise SweBenchRunError(f"{label} must be finite") from exc
    if not math.isfinite(converted) or not 0 <= converted <= maximum:
        raise SweBenchRunError(f"{label} exceeds its finite bound")
    return converted


def _sha256(value: object, *, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise SweBenchRunError(f"{label} must be lowercase SHA-256")
    return value


def _code(value: object, *, label: str) -> str:
    if type(value) is not str or _CODE_RE.fullmatch(value) is None:
        raise SweBenchRunError(f"{label} must be a stable lowercase code")
    return value


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
        raise SweBenchRunError("run evidence is not canonical finite JSON") from exc


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


def _object(value: Any, *, fields: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise SweBenchRunError(f"{label} fields are invalid")
    return value


def _boolean(value: object, *, expected: bool, label: str) -> None:
    if value is not expected:
        raise SweBenchRunError(f"{label} must equal {expected!r}")


def _portable_label(value: object, *, label: str, maximum: int = 512) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        raise SweBenchRunError(f"{label} must be bounded non-empty text")
    if value != unicodedata.normalize("NFC", value):
        raise SweBenchRunError(f"{label} must be NFC")
    if value.startswith(("/", "\\", ".")) or "\\" in value or ":" in value:
        raise SweBenchRunError(f"{label} must be a portable label")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise SweBenchRunError(f"{label} has an invalid component")
    if any(
        character == "\ufeff"
        or unicodedata.category(character) in {"Cc", "Cf", "Cs"}
        for character in value
    ):
        raise SweBenchRunError(f"{label} contains an unsafe character")
    return value


def _absolute_path(value: object, *, label: str) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise SweBenchRunError(f"{label} must be an absolute path")
    path = Path(value)
    if not path.is_absolute():
        raise SweBenchRunError(f"{label} must be an absolute path")
    return path


def _safe_directory(value: object, *, label: str) -> tuple[Path, tuple[int, int]]:
    path = _absolute_path(value, label=label)
    try:
        boundary = ParentDirectoryGuard.capture(
            path / ".ctxc-directory-boundary",
            label=label,
        )
        lexical = boundary.parent
        info = lexical.lstat()
        boundary.verify()
    except (OSError, PathBoundaryError, RuntimeError, ValueError) as exc:
        raise SweBenchRunError(f"{label} could not be inspected") from exc
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or bool(getattr(info, "st_file_attributes", 0) & reparse)
    ):
        raise SweBenchRunError(f"{label} must be a real directory")
    return lexical, (info.st_dev, info.st_ino)


def _directory_identity(path: Path, expected: tuple[int, int], *, label: str) -> None:
    try:
        current, identity = _safe_directory(path, label=label)
    except SweBenchRunError as exc:
        raise SweBenchRunError(f"{label} could not be reinspected") from exc
    if current != path or identity != expected:
        raise SweBenchRunError(f"{label} identity changed")


def _single_link_regular(path: Path, *, label: str) -> tuple[int, int]:
    try:
        boundary = ParentDirectoryGuard.capture(path, label=label)
        selected = boundary.target
        info = selected.lstat()
        boundary.verify()
    except (OSError, PathBoundaryError, RuntimeError, ValueError) as exc:
        raise SweBenchRunError(f"could not inspect {label}") from exc
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_nlink != 1
        or bool(getattr(info, "st_file_attributes", 0) & reparse)
    ):
        raise SweBenchRunError(f"{label} must be a single-link regular file")
    return info.st_dev, info.st_ino


def _hash_regular_identity(
    path: Path,
    identity: tuple[int, int],
    *,
    maximum: int,
    label: str,
) -> tuple[int, str]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    digest = hashlib.sha256()
    total = 0
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != identity
        ):
            raise SweBenchRunError(f"{label} identity changed while opened")
        if opened.st_size > maximum:
            raise SweBenchRunError(f"{label} exceeds its byte limit")
        while True:
            block = os.read(descriptor, min(1024 * 1024, maximum - total + 1))
            if not block:
                break
            total += len(block)
            if total > maximum:
                raise SweBenchRunError(f"{label} exceeds its byte limit")
            digest.update(block)
        closed = os.fstat(descriptor)
        if (
            (closed.st_dev, closed.st_ino) != identity
            or closed.st_size != opened.st_size
            or closed.st_mtime_ns != opened.st_mtime_ns
            or closed.st_ctime_ns != opened.st_ctime_ns
        ):
            raise SweBenchRunError(f"{label} changed while hashed")
    except SweBenchRunError:
        raise
    except OSError as exc:
        raise SweBenchRunError(f"{label} could not be hashed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if _single_link_regular(path, label=label) != identity:
        raise SweBenchRunError(f"{label} identity changed while hashed")
    return total, digest.hexdigest()


@dataclass(frozen=True, slots=True)
class RunLedgerLimits:
    """Finite ledger, output-envelope, trajectory, and workspace bounds."""

    max_patch_bytes: int = 4 * 1024 * 1024
    max_request_bytes: int = 16 * 1024 * 1024
    max_ledger_bytes: int = 64 * 1024 * 1024
    max_trajectory_events: int = 20_000
    max_trajectory_bytes: int = 16 * 1024 * 1024
    max_workspace_entries: int = 200_000
    max_workspace_file_bytes: int = 256 * 1024 * 1024
    max_workspace_total_bytes: int = 4 * 1024 * 1024 * 1024
    max_workspace_path_bytes: int = 4096
    max_executable_bytes: int = 256 * 1024 * 1024
    max_token_count: int = 10**10

    def __post_init__(self) -> None:
        bounds = {
            "max_patch_bytes": (1, _MAX_HARD_PATCH_BYTES),
            "max_request_bytes": (1, _MAX_HARD_REQUEST_BYTES),
            "max_ledger_bytes": (1, _MAX_HARD_LEDGER_BYTES),
            "max_trajectory_events": (1, _MAX_JSON_NODES),
            "max_trajectory_bytes": (1, _MAX_HARD_TRAJECTORY_BYTES),
            "max_workspace_entries": (1, _MAX_HARD_WORKSPACE_ENTRIES),
            "max_workspace_file_bytes": (1, _MAX_HARD_WORKSPACE_FILE_BYTES),
            "max_workspace_total_bytes": (1, _MAX_HARD_WORKSPACE_BYTES),
            "max_workspace_path_bytes": (1, 16 * 1024),
            "max_executable_bytes": (1, _MAX_HARD_EXECUTABLE_BYTES),
            "max_token_count": (1, _MAX_HARD_TOKEN_COUNT),
        }
        for name, (minimum, maximum) in bounds.items():
            object.__setattr__(
                self,
                name,
                _bounded_integer(
                    getattr(self, name),
                    label=name,
                    minimum=minimum,
                    maximum=maximum,
                ),
            )
        if self.max_workspace_file_bytes > self.max_workspace_total_bytes:
            raise SweBenchRunError(
                "max_workspace_file_bytes cannot exceed max_workspace_total_bytes"
            )
        if self.max_trajectory_events * 64 > self.max_ledger_bytes:
            raise SweBenchRunError(
                "max_ledger_bytes must cover bounded per-event accounting"
            )


_LIMIT_FIELDS = (
    "max_patch_bytes",
    "max_request_bytes",
    "max_ledger_bytes",
    "max_trajectory_events",
    "max_trajectory_bytes",
    "max_workspace_entries",
    "max_workspace_file_bytes",
    "max_workspace_total_bytes",
    "max_workspace_path_bytes",
    "max_executable_bytes",
    "max_token_count",
)


def _limits_document(limits: RunLedgerLimits) -> dict[str, int]:
    return {name: getattr(limits, name) for name in _LIMIT_FIELDS}


def _revalidate_limits(value: object) -> RunLedgerLimits:
    if type(value) is not RunLedgerLimits:
        raise SweBenchRunError("limits must be RunLedgerLimits")
    try:
        return RunLedgerLimits(**_limits_document(value))
    except (AttributeError, TypeError, SweBenchRunError) as exc:
        raise SweBenchRunError("run ledger limits are invalid") from exc


def _literal_limits_document(limits: LiteralProcessLimits) -> dict[str, Any]:
    return {
        "timeout_seconds": limits.timeout_seconds,
        "poll_interval_seconds": limits.poll_interval_seconds,
        "max_stdout_bytes": limits.max_stdout_bytes,
        "max_stderr_bytes": limits.max_stderr_bytes,
        "max_memory_mb": limits.max_memory_mb,
    }


def _revalidate_literal_limits(value: object) -> LiteralProcessLimits:
    if type(value) is not LiteralProcessLimits:
        raise SweBenchRunError("controller limits must be LiteralProcessLimits")
    try:
        return LiteralProcessLimits(**_literal_limits_document(value))
    except (AttributeError, TypeError, LiteralProcessError) as exc:
        raise SweBenchRunError("controller process limits are invalid") from exc


@dataclass(frozen=True, slots=True)
class ControllerSpec:
    """Exact literal controller command, environment, and executable digest."""

    argv: Sequence[str]
    environment: Mapping[str, str]
    limits: LiteralProcessLimits
    executable_sha256: str
    executable_byte_count: int
    protocol: str = CONTROLLER_PROTOCOL

    def __post_init__(self) -> None:
        if type(self.argv) not in (list, tuple):
            raise SweBenchRunError("controller argv must be a materialized sequence")
        arguments = tuple(self.argv[:257])
        if not 1 <= len(arguments) <= 254:
            raise SweBenchRunError("controller argv count is invalid")
        total = 0
        for index, argument in enumerate(arguments):
            if type(argument) is not str or not argument or "\0" in argument:
                raise SweBenchRunError(f"controller argv[{index}] is invalid")
            try:
                encoded = argument.encode("utf-8", errors="strict")
            except UnicodeError as exc:
                raise SweBenchRunError(
                    f"controller argv[{index}] is not UTF-8"
                ) from exc
            if len(encoded) > 64 * 1024:
                raise SweBenchRunError(f"controller argv[{index}] is too large")
            total += len(encoded)
        if total > 1024 * 1024:
            raise SweBenchRunError("controller argv exceeds its aggregate bound")
        executable = Path(arguments[0])
        if not executable.is_absolute():
            raise SweBenchRunError("controller argv[0] must be absolute")
        if type(self.environment) is not dict:
            raise SweBenchRunError("controller environment must be a plain mapping")
        if len(self.environment) > 1024:
            raise SweBenchRunError("controller environment contains too many variables")
        environment: dict[str, str] = {}
        environment_bytes = 0
        folded_names: set[str] = set()
        for name, value in self.environment.items():
            if (
                type(name) is not str
                or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None
                or type(value) is not str
                or "\0" in value
            ):
                raise SweBenchRunError("controller environment must contain strings")
            folded = name.casefold()
            if os.name == "nt" and folded in folded_names:
                raise SweBenchRunError(
                    "controller environment has case-insensitive duplicate names"
                )
            folded_names.add(folded)
            try:
                name_bytes = name.encode("utf-8", errors="strict")
                value_bytes = value.encode("utf-8", errors="strict")
            except UnicodeError as exc:
                raise SweBenchRunError(
                    "controller environment is not strict UTF-8"
                ) from exc
            if len(name_bytes) > 1024 or len(value_bytes) > 128 * 1024:
                raise SweBenchRunError(
                    "controller environment variable exceeds its byte limit"
                )
            environment_bytes += len(name_bytes) + len(value_bytes)
            if environment_bytes > 2 * 1024 * 1024:
                raise SweBenchRunError(
                    "controller environment exceeds its aggregate byte limit"
                )
            environment[name] = value
        selected_limits = _revalidate_literal_limits(self.limits)
        _sha256(self.executable_sha256, label="controller executable SHA-256")
        _bounded_integer(
            self.executable_byte_count,
            label="controller executable byte count",
            minimum=1,
            maximum=_MAX_HARD_EXECUTABLE_BYTES,
        )
        if self.protocol != CONTROLLER_PROTOCOL:
            raise SweBenchRunError("controller protocol is invalid")
        object.__setattr__(self, "argv", arguments)
        object.__setattr__(self, "environment", MappingProxyType(environment))
        object.__setattr__(self, "limits", selected_limits)


@dataclass(frozen=True, slots=True)
class CandidateWorkspace:
    """One opaque task binding and its separately provisioned mutable tree."""

    opaque_task_id: str
    path: Path | str | None

    def __post_init__(self) -> None:
        if type(self.opaque_task_id) is not str or _OPAQUE_ID_RE.fullmatch(
            self.opaque_task_id
        ) is None:
            raise SweBenchRunError("workspace opaque_task_id is invalid")
        if self.path is not None:
            object.__setattr__(
                self,
                "path",
                _absolute_path(self.path, label="candidate workspace"),
            )


@dataclass(frozen=True, slots=True)
class VerifiedSweBenchRunLedger:
    """Runtime artifact/workspace handles and frozen full-cohort run evidence."""

    artifact_root: Path
    workspaces: tuple[CandidateWorkspace, ...]
    preparations: tuple[RepositoryPreparationOutcome, ...]
    controller: ControllerSpec
    limits: RunLedgerLimits
    captures: tuple[CandidatePatchCapture, ...]
    document: Mapping[str, Any]

    @property
    def run_ledger_sha256(self) -> str:
        return str(self.document["run_ledger_sha256"])

    @property
    def claim_ready(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        return _thaw_json(self.document)

    def prediction_captures(self) -> tuple[CandidatePatchCapture, ...]:
        """Return captures already reconstructed from retained stdout bytes."""

        return self.captures


def _revalidate_controller(
    value: object,
    *,
    limits: RunLedgerLimits,
) -> tuple[ControllerSpec, dict[str, Any], tuple[int, int]]:
    if type(value) is not ControllerSpec:
        raise SweBenchRunError("controller must be ControllerSpec")
    try:
        selected = ControllerSpec(
            argv=list(value.argv),
            environment=dict(value.environment),
            limits=value.limits,
            executable_sha256=value.executable_sha256,
            executable_byte_count=value.executable_byte_count,
            protocol=value.protocol,
        )
    except (AttributeError, TypeError, SweBenchRunError) as exc:
        raise SweBenchRunError("controller specification is invalid") from exc
    executable = Path(selected.argv[0])
    identity = _single_link_regular(executable, label="controller executable")
    byte_count, file_sha256 = _hash_regular_identity(
        executable,
        identity,
        maximum=limits.max_executable_bytes,
        label="controller executable",
    )
    if _single_link_regular(executable, label="controller executable") != identity:
        raise SweBenchRunError("controller executable identity changed while hashed")
    if (
        byte_count != selected.executable_byte_count
        or file_sha256 != selected.executable_sha256
    ):
        raise SweBenchRunError("controller executable evidence mismatch")
    argv_document = list(selected.argv)
    environment_document = dict(selected.environment)
    document = {
        "protocol": selected.protocol,
        "base_argv_count": len(argv_document),
        "base_argv_sha256": _canonical_sha256(argv_document),
        "argv_suffix": ["request-json-path", "candidate-workspace-path"],
        "executable": {
            "byte_count": byte_count,
            "sha256": file_sha256,
        },
        "environment_names": sorted(environment_document),
        "environment_sha256": _canonical_sha256(environment_document),
        "process_limits": _literal_limits_document(selected.limits),
    }
    return selected, document, identity


def _revalidate_workspaces(
    values: Sequence[CandidateWorkspace],
    *,
    tasks: Sequence[Mapping[str, Any]],
) -> tuple[CandidateWorkspace, ...]:
    if type(values) not in (list, tuple):
        raise SweBenchRunError("workspaces must be a materialized sequence")
    retained_inputs = tuple(values[: len(tasks) + 1])
    if len(retained_inputs) != len(tasks):
        raise SweBenchRunError("workspaces must cover the selected cohort")
    retained: list[CandidateWorkspace] = []
    for ordinal, (raw, task) in enumerate(zip(retained_inputs, tasks, strict=True)):
        if type(raw) is not CandidateWorkspace:
            raise SweBenchRunError(
                f"workspaces[{ordinal}] must be CandidateWorkspace"
            )
        try:
            workspace = CandidateWorkspace(raw.opaque_task_id, raw.path)
        except (AttributeError, TypeError, SweBenchRunError) as exc:
            raise SweBenchRunError(f"workspaces[{ordinal}] is invalid") from exc
        if workspace.opaque_task_id != task["opaque_task_id"]:
            raise SweBenchRunError(
                f"workspaces[{ordinal}] opaque task id/order mismatch"
            )
        retained.append(workspace)
    return tuple(retained)


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


def _preflight_workspace_boundaries(
    workspaces: Sequence[CandidateWorkspace],
    preparations: Sequence[RepositoryPreparationOutcome],
    artifact_parent: Path,
) -> None:
    known_workspaces: list[Path] = []
    forbidden: list[Path] = [artifact_parent]
    for preparation in preparations:
        if preparation.prepared is not None:
            try:
                forbidden.extend(
                    (
                        preparation.prepared.path.resolve(strict=True),
                        preparation.prepared.mirror.path.resolve(strict=True),
                    )
                )
            except (OSError, RuntimeError, ValueError) as exc:
                raise SweBenchRunError(
                    "prepared repository boundary could not be inspected"
                ) from exc
    if any(_paths_overlap(artifact_parent, blocked) for blocked in forbidden[1:]):
        raise SweBenchRunError(
            "run artifact root overlaps a prepared tree or bare mirror"
        )
    for ordinal, workspace in enumerate(workspaces):
        if workspace.path is None:
            continue
        path, _ = _safe_directory(
            workspace.path,
            label=f"candidate workspace {ordinal}",
        )
        if any(_paths_overlap(path, blocked) for blocked in forbidden):
            raise SweBenchRunError(
                f"candidate workspace {ordinal} overlaps a coordinator boundary"
            )
        if any(_paths_overlap(path, previous) for previous in known_workspaces):
            raise SweBenchRunError("candidate workspaces overlap")
        known_workspaces.append(path)


def _create_artifact_root(parent: Path) -> tuple[Path, tuple[int, int]]:
    for _ in range(128):
        candidate = parent / f"ctxc-swebench-run-{secrets.token_hex(16)}"
        try:
            os.mkdir(candidate, mode=0o700)
        except FileExistsError:
            continue
        except OSError as exc:
            raise SweBenchRunError("run artifact root could not be created") from exc
        return _safe_directory(candidate, label="run artifact root")
    raise SweBenchRunError("could not allocate a unique run artifact root")


def _artifact_label(ordinal: int, kind: str) -> str:
    return _portable_label(f"{ordinal:05d}-{kind}", label="artifact label")


def _write_exclusive_bytes(
    root: Path,
    root_identity: tuple[int, int],
    label: str,
    encoded: bytes,
) -> dict[str, Any]:
    _directory_identity(root, root_identity, label="run artifact root")
    path = root / _portable_label(label, label="artifact label")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags, 0o600)
        written = 0
        while written < len(encoded):
            count = os.write(descriptor, encoded[written:])
            if count <= 0:
                raise OSError("short artifact write")
            written += count
        os.fsync(descriptor)
        info = os.fstat(descriptor)
    except FileExistsError as exc:
        raise SweBenchRunError(f"run artifact already exists: {label}") from exc
    except OSError as exc:
        raise SweBenchRunError(f"run artifact could not be written: {label}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    identity = _single_link_regular(path, label=f"run artifact {label}")
    if identity != (info.st_dev, info.st_ino):
        raise SweBenchRunError(f"run artifact identity changed: {label}")
    try:
        retained = read_bounded_regular_file(
            path,
            max_bytes=max(1, len(encoded)),
            label=f"run artifact {label}",
            expected_identity=identity,
        )
    except StrictJsonError as exc:
        raise SweBenchRunError(str(exc)) from exc
    if retained.value != encoded:
        raise SweBenchRunError(f"run artifact bytes changed: {label}")
    return {
        "label": label,
        "byte_count": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


@dataclass(frozen=True, slots=True)
class _WorkspaceSnapshot:
    root_identity: tuple[int, int]
    files: tuple[tuple[str, str, int, str], ...]
    directories: tuple[str, ...]
    total_bytes: int
    sha256: str

    def summary(self) -> dict[str, Any]:
        return {
            "file_count": len(self.files),
            "directory_count": len(self.directories),
            "total_bytes": self.total_bytes,
            "sha256": self.sha256,
        }


def _path_bytes(relative: str, *, limits: RunLedgerLimits) -> None:
    try:
        encoded = relative.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise SweBenchRunError("workspace path is not strict UTF-8") from exc
    if len(encoded) > limits.max_workspace_path_bytes:
        raise SweBenchRunError("workspace path exceeds its byte limit")


def _hash_workspace_file(
    path: Path,
    expected: os.stat_result,
    *,
    limits: RunLedgerLimits,
) -> tuple[int, str]:
    if expected.st_size > limits.max_workspace_file_bytes:
        raise SweBenchRunError("workspace file exceeds its byte limit")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    digest = hashlib.sha256()
    total = 0
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino)
            or opened.st_nlink != 1
        ):
            raise SweBenchRunError("workspace file identity changed while opened")
        while True:
            block = os.read(
                descriptor,
                min(1024 * 1024, limits.max_workspace_file_bytes - total + 1),
            )
            if not block:
                break
            total += len(block)
            if total > limits.max_workspace_file_bytes:
                raise SweBenchRunError("workspace file exceeds its byte limit")
            digest.update(block)
        closed = os.fstat(descriptor)
        if (
            (closed.st_dev, closed.st_ino) != (opened.st_dev, opened.st_ino)
            or closed.st_size != opened.st_size
            or closed.st_mtime_ns != opened.st_mtime_ns
            or closed.st_ctime_ns != opened.st_ctime_ns
        ):
            raise SweBenchRunError("workspace file changed while hashed")
    except SweBenchRunError:
        raise
    except OSError as exc:
        raise SweBenchRunError("workspace file could not be hashed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        final = path.lstat()
    except OSError as exc:
        raise SweBenchRunError("workspace file could not be reinspected") from exc
    if (
        (final.st_dev, final.st_ino) != (expected.st_dev, expected.st_ino)
        or final.st_size != expected.st_size
        or final.st_mtime_ns != expected.st_mtime_ns
        or final.st_ctime_ns != expected.st_ctime_ns
    ):
        raise SweBenchRunError("workspace file changed while hashed")
    return total, digest.hexdigest()


def _snapshot_workspace(path: Path, *, limits: RunLedgerLimits) -> _WorkspaceSnapshot:
    root, root_identity = _safe_directory(path, label="candidate workspace")
    pending = [root]
    files: list[tuple[str, str, int, str]] = []
    directories: list[str] = [""]
    entry_count = 0
    total_bytes = 0
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    while pending:
        directory = pending.pop()
        try:
            iterator = os.scandir(directory)
        except OSError as exc:
            raise SweBenchRunError("candidate workspace could not be scanned") from exc
        try:
            with iterator:
                for child in iterator:
                    entry_count += 1
                    if entry_count > limits.max_workspace_entries:
                        raise SweBenchRunError(
                            "candidate workspace exceeds its entry limit"
                        )
                    try:
                        child_path = Path(child.path)
                        info = child_path.lstat()
                    except OSError as exc:
                        raise SweBenchRunError(
                            "candidate workspace entry could not be inspected"
                        ) from exc
                    if child.is_symlink() or bool(
                        getattr(info, "st_file_attributes", 0) & reparse
                    ):
                        raise SweBenchRunError(
                            "candidate workspace contains a link or reparse point"
                        )
                    try:
                        relative = child_path.relative_to(root).as_posix()
                    except ValueError as exc:
                        raise SweBenchRunError(
                            "candidate workspace entry escaped its root"
                        ) from exc
                    _path_bytes(relative, limits=limits)
                    if stat.S_ISDIR(info.st_mode):
                        directories.append(relative)
                        pending.append(child_path)
                    elif stat.S_ISREG(info.st_mode):
                        if info.st_nlink != 1:
                            raise SweBenchRunError(
                                "candidate workspace contains a hard-linked file"
                            )
                        count, digest = _hash_workspace_file(
                            child_path,
                            info,
                            limits=limits,
                        )
                        total_bytes += count
                        if total_bytes > limits.max_workspace_total_bytes:
                            raise SweBenchRunError(
                                "candidate workspace exceeds its aggregate byte limit"
                            )
                        mode = (
                            "executable"
                            if os.name != "nt" and bool(stat.S_IMODE(info.st_mode) & 0o111)
                            else "regular"
                        )
                        files.append((relative, mode, count, digest))
                    else:
                        raise SweBenchRunError(
                            "candidate workspace contains a special file"
                        )
        except OSError as exc:
            raise SweBenchRunError("candidate workspace scan failed") from exc
    files.sort(key=lambda item: item[0].encode("utf-8", errors="strict"))
    directories.sort(key=lambda item: item.encode("utf-8", errors="strict"))
    _directory_identity(root, root_identity, label="candidate workspace")
    document = {
        "directories": directories,
        "files": [list(value) for value in files],
        "total_bytes": total_bytes,
    }
    return _WorkspaceSnapshot(
        root_identity=root_identity,
        files=tuple(files),
        directories=tuple(directories),
        total_bytes=total_bytes,
        sha256=_canonical_sha256(document),
    )


def _expected_prepared_snapshot(
    preparation: RepositoryPreparationOutcome,
) -> tuple[tuple[tuple[str, str, int, str], ...], tuple[str, ...], int, str]:
    assert preparation.prepared is not None
    bindings = preparation.prepared.document["tree"]["entry_bindings"]
    files: list[tuple[str, str, int, str]] = []
    directories = {""}
    total_bytes = 0
    for binding in bindings:
        relative = str(binding[0])
        mode = "executable" if binding[1] == "100755" and os.name != "nt" else "regular"
        count = int(binding[3])
        digest = str(binding[4])
        files.append((relative, mode, count, digest))
        total_bytes += count
        parts = PurePosixPath(relative).parts
        directories.update(
            PurePosixPath(*parts[:index]).as_posix()
            for index in range(1, len(parts))
        )
    files.sort(key=lambda item: item[0].encode("utf-8", errors="strict"))
    ordered_directories = sorted(
        directories,
        key=lambda item: item.encode("utf-8", errors="strict"),
    )
    document = {
        "directories": ordered_directories,
        "files": [list(value) for value in files],
        "total_bytes": total_bytes,
    }
    return (
        tuple(files),
        tuple(ordered_directories),
        total_bytes,
        _canonical_sha256(document),
    )


def _workspace_matches_preparation(
    snapshot: _WorkspaceSnapshot,
    preparation: RepositoryPreparationOutcome,
) -> bool:
    files, directories, total_bytes, digest = _expected_prepared_snapshot(preparation)
    return (
        snapshot.files == files
        and snapshot.directories == directories
        and snapshot.total_bytes == total_bytes
        and snapshot.sha256 == digest
    )


def _workspace_delta(
    before: _WorkspaceSnapshot,
    after: _WorkspaceSnapshot,
) -> dict[str, Any]:
    before_files = {value[0]: value[1:] for value in before.files}
    after_files = {value[0]: value[1:] for value in after.files}
    before_directories = set(before.directories)
    after_directories = set(after.directories)
    added_files = sorted(set(after_files) - set(before_files))
    deleted_files = sorted(set(before_files) - set(after_files))
    modified_files = sorted(
        path
        for path in set(before_files) & set(after_files)
        if before_files[path] != after_files[path]
    )
    delta = {
        "added_files": added_files,
        "deleted_files": deleted_files,
        "modified_files": modified_files,
        "added_directories": sorted(after_directories - before_directories),
        "deleted_directories": sorted(before_directories - after_directories),
    }
    return {
        "added_file_count": len(added_files),
        "deleted_file_count": len(deleted_files),
        "modified_file_count": len(modified_files),
        "added_directory_count": len(delta["added_directories"]),
        "deleted_directory_count": len(delta["deleted_directories"]),
        "sha256": _canonical_sha256(delta),
    }


def _validate_json_shape(
    value: Any,
    *,
    max_nodes: int,
    max_depth: int = _MAX_JSON_DEPTH,
) -> None:
    pending: list[tuple[Any, int]] = [(value, 0)]
    nodes = 0
    while pending:
        nested, depth = pending.pop()
        nodes += 1
        if nodes > max_nodes:
            raise SweBenchRunError("controller JSON exceeds its node limit")
        if depth > max_depth:
            raise SweBenchRunError("controller JSON exceeds its depth limit")
        if type(nested) is dict:
            for key, child in nested.items():
                if type(key) is not str:
                    raise SweBenchRunError("controller JSON has a non-string key")
                pending.append((child, depth + 1))
        elif type(nested) is list:
            pending.extend((child, depth + 1) for child in nested)
        elif nested is None or type(nested) in (str, int, float, bool):
            if type(nested) is float and not math.isfinite(nested):
                raise SweBenchRunError("controller JSON contains a non-finite number")
        else:
            raise SweBenchRunError("controller JSON contains an unsupported value")


def _strict_json_bytes(encoded: bytes) -> Any:
    if encoded.startswith(b"\xef\xbb\xbf"):
        raise SweBenchRunError("controller stdout must not contain a BOM")
    try:
        raw = encoded.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise SweBenchRunError("controller stdout is not strict UTF-8") from exc
    if not raw.strip():
        raise SweBenchRunError("controller stdout is empty")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, nested in pairs:
            if key in value:
                raise SweBenchRunError("controller stdout contains a duplicate key")
            value[key] = nested
        return value

    def finite_float(value: str) -> float:
        converted = float(value)
        if not math.isfinite(converted):
            raise SweBenchRunError("controller stdout contains a non-finite float")
        return converted

    def bounded_int(value: str) -> int:
        if len(value.lstrip("-")) > 32:
            raise SweBenchRunError("controller stdout contains an oversized integer")
        return int(value)

    def reject_constant(value: str) -> None:
        raise SweBenchRunError(f"controller stdout contains {value}")

    try:
        decoded = json.loads(
            raw,
            object_pairs_hook=unique_object,
            parse_float=finite_float,
            parse_int=bounded_int,
            parse_constant=reject_constant,
        )
    except SweBenchRunError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise SweBenchRunError("controller stdout is not strict JSON") from exc
    _validate_json_shape(decoded, max_nodes=_MAX_JSON_NODES)
    return decoded


def _patch_bytes(value: str, *, maximum: int) -> bytes:
    chunks: list[bytes] = []
    retained_count = 0
    observed_count = 0
    for start in range(0, len(value), 64 * 1024):
        try:
            encoded = value[start : start + 64 * 1024].encode(
                "utf-8",
                errors="strict",
            )
        except UnicodeError as exc:
            raise SweBenchRunError("controller model_patch is not strict UTF-8") from exc
        observed_count += len(encoded)
        remaining = maximum + 1 - retained_count
        if remaining > 0:
            retained = encoded[:remaining]
            chunks.append(retained)
            retained_count += len(retained)
    retained = b"".join(chunks)
    if observed_count > maximum:
        raise _OversizedPatch(retained[: maximum + 1], observed_count)
    return retained


def _token_document(value: Any, *, limits: RunLedgerLimits) -> dict[str, Any]:
    payload = _object(
        value,
        fields={"method", "prompt", "model"},
        label="controller token counts",
    )
    method = payload["method"]
    if type(method) is not str or (
        method not in {"provider_reported", "not_measured"}
        and _TOKENIZER_RE.fullmatch(method) is None
    ):
        raise SweBenchRunError("controller token count method is invalid")
    prompt = payload["prompt"]
    model = payload["model"]
    if method == "not_measured":
        if prompt is not None or model is not None:
            raise SweBenchRunError("unmeasured token counts must be null")
    else:
        prompt = _bounded_integer(
            prompt,
            label="controller prompt token count",
            minimum=0,
            maximum=limits.max_token_count,
        )
        model = _bounded_integer(
            model,
            label="controller model token count",
            minimum=0,
            maximum=limits.max_token_count,
        )
    return {"method": method, "prompt": prompt, "model": model}


@dataclass(frozen=True, slots=True)
class _ControllerObservation:
    status: str
    failure_code: str | None
    patch: str | None
    patch_evidence: dict[str, Any] | None
    tokens: dict[str, Any] | None
    trajectory: dict[str, Any] | None
    capture: CandidatePatchCapture | None

    def document(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "failure_code": self.failure_code,
            "patch": self.patch_evidence,
            "tokens": self.tokens,
            "trajectory": self.trajectory,
        }


def _malformed_observation() -> _ControllerObservation:
    return _ControllerObservation(
        status="not-recorded",
        failure_code="malformed-controller-output",
        patch=None,
        patch_evidence=None,
        tokens=None,
        trajectory=None,
        capture=None,
    )


def _parse_controller_output(
    encoded: bytes,
    *,
    opaque_task_id: str,
    limits: RunLedgerLimits,
) -> _ControllerObservation:
    try:
        payload = _object(
            _strict_json_bytes(encoded),
            fields={"schema", "opaque_task_id", "model_patch", "tokens", "trajectory"},
            label="controller result envelope",
        )
        if payload["schema"] != CONTROLLER_RESULT_SCHEMA:
            raise SweBenchRunError("controller result schema mismatch")
        if payload["opaque_task_id"] != opaque_task_id:
            raise SweBenchRunError("controller result opaque task id mismatch")
        if type(payload["model_patch"]) is not str:
            raise SweBenchRunError("controller model_patch must be text")
        tokens = _token_document(payload["tokens"], limits=limits)
        trajectory = payload["trajectory"]
        if type(trajectory) is not list:
            raise SweBenchRunError("controller trajectory must be a list")
        if len(trajectory) > limits.max_trajectory_events:
            raise SweBenchRunError("controller trajectory exceeds its event limit")
        if any(type(event) is not dict for event in trajectory):
            raise SweBenchRunError("controller trajectory events must be objects")
        _validate_json_shape(
            trajectory,
            max_nodes=min(
                _MAX_JSON_NODES,
                1 + limits.max_trajectory_events * 64,
            ),
        )
        trajectory_bytes = _canonical_json_bytes(trajectory)
        if len(trajectory_bytes) > limits.max_trajectory_bytes:
            raise SweBenchRunError("controller trajectory exceeds its byte limit")
    except _OversizedPatch:
        raise
    except SweBenchRunError:
        return _malformed_observation()

    try:
        patch_bytes = _patch_bytes(payload["model_patch"], maximum=limits.max_patch_bytes)
    except _OversizedPatch as exc:
        evidence = RetainedCaptureEvidence(
            observed_bytes=exc.observed_bytes,
            captured_bytes=len(exc.prefix),
            captured_sha256=hashlib.sha256(exc.prefix).hexdigest(),
            complete=exc.observed_bytes == len(exc.prefix),
        )
        capture = CandidatePatchCapture(
            opaque_task_id=opaque_task_id,
            status="oversized",
            capture=evidence,
            failure_code="oversized-candidate-output",
        )
        return _ControllerObservation(
            status="not-recorded",
            failure_code="candidate-output-oversized",
            patch=None,
            patch_evidence={
                "observed_bytes": exc.observed_bytes,
                "retained_prefix_bytes": len(exc.prefix),
                "retained_prefix_sha256": hashlib.sha256(exc.prefix).hexdigest(),
            },
            tokens=tokens,
            trajectory={
                "event_count": len(trajectory),
                "byte_count": len(trajectory_bytes),
                "sha256": hashlib.sha256(trajectory_bytes).hexdigest(),
                "ordered": True,
                "retained_in_raw_stdout": True,
            },
            capture=capture,
        )
    except SweBenchRunError:
        return _malformed_observation()
    evidence = RetainedCaptureEvidence(
        observed_bytes=len(patch_bytes),
        captured_bytes=len(patch_bytes),
        captured_sha256=hashlib.sha256(patch_bytes).hexdigest(),
        complete=True,
    )
    capture = CandidatePatchCapture(
        opaque_task_id=opaque_task_id,
        status="recorded",
        model_patch=payload["model_patch"],
        capture=evidence,
    )
    return _ControllerObservation(
        status="recorded",
        failure_code=None,
        patch=payload["model_patch"],
        patch_evidence={
            "byte_count": len(patch_bytes),
            "sha256": hashlib.sha256(patch_bytes).hexdigest(),
        },
        tokens=tokens,
        trajectory={
            "event_count": len(trajectory),
            "byte_count": len(trajectory_bytes),
            "sha256": hashlib.sha256(trajectory_bytes).hexdigest(),
            "ordered": True,
            "retained_in_raw_stdout": True,
        },
        capture=capture,
    )


def _stream_document(
    stream: StreamEvidence,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    if (
        artifact["byte_count"] != stream.captured_bytes
        or artifact["sha256"] != stream.captured_sha256
    ):
        raise SweBenchRunError("literal stream and retained artifact mismatch")
    return {
        "artifact": artifact,
        "observed_bytes": stream.observed_bytes,
        "captured_bytes": stream.captured_bytes,
        "complete": stream.complete,
    }


def _process_document(result: LiteralProcessResult) -> dict[str, Any]:
    return {
        "argv_count": len(result.argv),
        "argv_sha256": _canonical_sha256(list(result.argv)),
        "cwd_scope": "candidate-workspace",
        "exit_code": result.exit_code,
        "duration_seconds": result.duration_seconds,
        "setup_duration_seconds": result.setup_duration_seconds,
        "process_duration_seconds": result.process_duration_seconds,
        "cleanup_duration_seconds": result.cleanup_duration_seconds,
        "termination_trigger": result.termination_trigger,
        "execution_error": result.execution_error,
        "cleanup_error": result.cleanup_error,
        "cleanup_detail": result.cleanup_detail,
        "memory_limit_scope": result.memory_limit_scope,
        "containment_scope": result.containment_scope,
        "swebench_containment_claim_ready": False,
        "environment_names": list(result.environment_names),
        "environment_sha256": result.environment_sha256,
    }


def _process_disposition(result: LiteralProcessResult) -> str | None:
    trigger = result.termination_trigger
    if trigger == "timeout":
        return "execution-timeout"
    if trigger == "stdout_limit":
        return "stdout-limit-exceeded"
    if trigger == "stderr_limit":
        return "stderr-limit-exceeded"
    if trigger == "stream_observation_failed":
        return "stream-observation-failed"
    if trigger == "process_observation_failed":
        return "process-observation-failed"
    if trigger is not None or result.execution_error is not None:
        raise SweBenchRunError("literal process returned an impossible execution state")
    if result.cleanup_error is not None:
        return "cleanup-failed"
    if not result.stdout.complete or not result.stderr.complete:
        return "stream-observation-failed"
    if result.exit_code != 0:
        return "nonzero-exit"
    return None


def _unobserved_artifact_stream(
    root: Path,
    identity: tuple[int, int],
    *,
    ordinal: int,
    kind: str,
) -> dict[str, Any]:
    artifact = _write_exclusive_bytes(
        root,
        identity,
        _artifact_label(ordinal, f"{kind}.bin"),
        b"",
    )
    return {
        "artifact": artifact,
        "observed_bytes": 0,
        "captured_bytes": 0,
        "complete": False,
    }


def _repository_document(
    outcome: RepositoryPreparationOutcome,
) -> dict[str, Any]:
    return {
        "status": outcome.status,
        "preparation_sha256": (
            outcome.prepared.preparation_sha256
            if outcome.prepared is not None
            else None
        ),
        "failure_code": outcome.failure_code,
    }


def _base_task_document(
    *,
    ordinal: int,
    record: Mapping[str, str],
    task: Mapping[str, Any],
    preparation: RepositoryPreparationOutcome,
    workspace_supplied: bool,
) -> dict[str, Any]:
    return {
        "ordinal": ordinal,
        "instance_id": str(record["instance_id"]),
        "opaque_task_id": str(task["opaque_task_id"]),
        "source_record_sha256": _source_canonical_sha256(record),
        "candidate_input_sha256": _source_canonical_sha256(
            {"problem_statement": record["problem_statement"]}
        ),
        "repository": _repository_document(preparation),
        "workspace": {
            "supplied": workspace_supplied,
            "initial": None,
            "final": None,
            "delta": None,
            "final_failure_code": None,
        },
        "request": None,
        "process": None,
        "stdout": None,
        "stderr": None,
        "observation": {
            "status": "not-recorded",
            "failure_code": None,
            "patch": None,
            "tokens": None,
            "trajectory": None,
        },
        "disposition": "repository-not-attempted",
    }


def _failure_observation(code: str) -> dict[str, Any]:
    _code(code, label="run failure code")
    return {
        "status": "not-recorded",
        "failure_code": code,
        "patch": None,
        "tokens": None,
        "trajectory": None,
    }


def _task_request(task: Mapping[str, Any]) -> bytes:
    return _canonical_json_bytes(
        {
            "opaque_task_id": task["opaque_task_id"],
            "problem_statement": task["problem_statement"],
        }
    )


def _recheck_controller(
    controller: ControllerSpec,
    expected_document: Mapping[str, Any],
    expected_identity: tuple[int, int],
    *,
    limits: RunLedgerLimits,
) -> None:
    _, document, identity = _revalidate_controller(controller, limits=limits)
    if document != expected_document or identity != expected_identity:
        raise SweBenchRunError("controller identity changed during the cohort run")


def build_run_ledger(
    source: VerifiedSweBenchSource,
    task_input: Any,
    *,
    opaque_key: bytes,
    preparations: Sequence[RepositoryPreparationOutcome],
    workspaces: Sequence[CandidateWorkspace],
    system: SystemIdentity,
    controller: ControllerSpec,
    artifact_parent: str | Path,
    limits: RunLedgerLimits | None = None,
) -> VerifiedSweBenchRunLedger:
    """Launch the fixed controller protocol across the full selected cohort."""

    selected_limits = _revalidate_limits(
        RunLedgerLimits() if limits is None else limits
    )
    try:
        selected_system = _system_identity(system)
        suite, records, verified_task_input = _source_and_tasks(
            source,
            task_input,
            opaque_key,
        )
        retained_preparations, _ = _revalidate_preparations(
            preparations,
            source=source,
            task_input=verified_task_input,
        )
    except PredictionLedgerError as exc:
        raise SweBenchRunError(str(exc)) from exc
    selected_workspaces = _revalidate_workspaces(
        workspaces,
        tasks=verified_task_input["tasks"],
    )
    selected_controller, controller_document, controller_identity = (
        _revalidate_controller(controller, limits=selected_limits)
    )
    parent, parent_identity = _safe_directory(
        artifact_parent,
        label="run artifact parent",
    )
    for preparation in retained_preparations:
        if preparation.prepared is None:
            continue
        try:
            repository_boundaries = (
                preparation.prepared.path.resolve(strict=True),
                preparation.prepared.mirror.path.resolve(strict=True),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise SweBenchRunError(
                "prepared repository boundary could not be inspected"
            ) from exc
        if any(
            parent == boundary or parent.is_relative_to(boundary)
            for boundary in repository_boundaries
        ):
            raise SweBenchRunError(
                "run artifact parent is inside a prepared tree or bare mirror"
            )
    for ordinal, (preparation, workspace) in enumerate(
        zip(retained_preparations, selected_workspaces, strict=True)
    ):
        if preparation.status != "prepared" and workspace.path is not None:
            raise SweBenchRunError(
                f"unprepared task {ordinal} must not receive a candidate workspace"
            )
    _directory_identity(parent, parent_identity, label="run artifact parent")
    artifact_root, artifact_identity = _create_artifact_root(parent)
    _preflight_workspace_boundaries(
        selected_workspaces,
        retained_preparations,
        artifact_root,
    )

    tasks: list[dict[str, Any]] = []
    captures: list[CandidatePatchCapture] = []
    dispositions: Counter[str] = Counter()
    launch_attempt_count = 0
    lifecycle_result_count = 0
    measured_token_runs = 0
    unmeasured_token_runs = 0
    prompt_tokens_total = 0
    model_tokens_total = 0

    for ordinal, (record, task, preparation, workspace) in enumerate(
        zip(
            records,
            verified_task_input["tasks"],
            retained_preparations,
            selected_workspaces,
            strict=True,
        )
    ):
        row = _base_task_document(
            ordinal=ordinal,
            record=record,
            task=task,
            preparation=preparation,
            workspace_supplied=workspace.path is not None,
        )
        if preparation.status == "not-attempted":
            row["disposition"] = "repository-not-attempted"
            row["observation"] = _failure_observation(
                "repository-not-attempted"
            )
            dispositions[row["disposition"]] += 1
            tasks.append(row)
            continue
        if preparation.status == "refused":
            row["disposition"] = "repository-preparation-refused"
            row["observation"] = _failure_observation(
                "repository-preparation-refused"
            )
            dispositions[row["disposition"]] += 1
            tasks.append(row)
            continue
        if workspace.path is None:
            row["disposition"] = "workspace-unavailable"
            row["observation"] = _failure_observation("workspace-unavailable")
            dispositions[row["disposition"]] += 1
            tasks.append(row)
            continue

        workspace_path = Path(workspace.path)
        try:
            before = _snapshot_workspace(workspace_path, limits=selected_limits)
        except SweBenchRunError:
            row["disposition"] = "workspace-initial-mismatch"
            row["observation"] = _failure_observation(
                "workspace-inspection-failed"
            )
            dispositions[row["disposition"]] += 1
            tasks.append(row)
            continue
        if not _workspace_matches_preparation(before, preparation):
            row["workspace"]["initial"] = before.summary()
            row["disposition"] = "workspace-initial-mismatch"
            row["observation"] = _failure_observation(
                "workspace-initial-mismatch"
            )
            dispositions[row["disposition"]] += 1
            tasks.append(row)
            continue
        row["workspace"]["initial"] = before.summary()
        assert preparation.prepared is not None
        try:
            verify_prepared_repository(preparation.prepared, source=source)
        except RepositoryPreparationError as exc:
            raise SweBenchRunError(
                f"prepared repository {ordinal} changed before launch"
            ) from exc
        _recheck_controller(
            selected_controller,
            controller_document,
            controller_identity,
            limits=selected_limits,
        )

        request_bytes = _task_request(task)
        if len(request_bytes) > selected_limits.max_request_bytes:
            raise SweBenchRunError(
                f"candidate request {ordinal} exceeds its byte limit"
            )
        request_artifact = _write_exclusive_bytes(
            artifact_root,
            artifact_identity,
            _artifact_label(ordinal, "request.json"),
            request_bytes,
        )
        row["request"] = request_artifact
        request_path = artifact_root / request_artifact["label"]
        literal_argv = (
            *selected_controller.argv,
            str(request_path),
            str(workspace_path.resolve(strict=True)),
        )
        launch_attempt_count += 1
        result: LiteralProcessResult | None = None
        launch_error = False
        try:
            result = run_literal_argv(
                literal_argv,
                cwd=workspace_path,
                environment=selected_controller.environment,
                limits=selected_controller.limits,
            )
        except LiteralProcessError:
            launch_error = True

        if result is None:
            row["stdout"] = _unobserved_artifact_stream(
                artifact_root,
                artifact_identity,
                ordinal=ordinal,
                kind="stdout",
            )
            row["stderr"] = _unobserved_artifact_stream(
                artifact_root,
                artifact_identity,
                ordinal=ordinal,
                kind="stderr",
            )
        else:
            lifecycle_result_count += 1
            stdout_artifact = _write_exclusive_bytes(
                artifact_root,
                artifact_identity,
                _artifact_label(ordinal, "stdout.bin"),
                result.stdout.prefix,
            )
            stderr_artifact = _write_exclusive_bytes(
                artifact_root,
                artifact_identity,
                _artifact_label(ordinal, "stderr.bin"),
                result.stderr.prefix,
            )
            row["stdout"] = _stream_document(result.stdout, stdout_artifact)
            row["stderr"] = _stream_document(result.stderr, stderr_artifact)
            row["process"] = _process_document(result)

        try:
            after = _snapshot_workspace(workspace_path, limits=selected_limits)
        except SweBenchRunError:
            after = None
        try:
            verify_prepared_repository(preparation.prepared, source=source)
        except RepositoryPreparationError as exc:
            raise SweBenchRunError(
                f"prepared repository {ordinal} changed during candidate launch"
            ) from exc
        _recheck_controller(
            selected_controller,
            controller_document,
            controller_identity,
            limits=selected_limits,
        )

        if after is not None:
            if after.root_identity != before.root_identity:
                after = None
            else:
                row["workspace"]["final"] = after.summary()
                row["workspace"]["delta"] = _workspace_delta(before, after)
        if after is None:
            row["workspace"]["final_failure_code"] = (
                "workspace-observation-failed"
            )

        if launch_error:
            disposition = "launch-failed"
        else:
            assert result is not None
            disposition = _process_disposition(result) or ""
        observation: _ControllerObservation | None = None
        if after is None and not disposition:
            disposition = "workspace-observation-failed"
        if not disposition:
            assert result is not None
            observation = _parse_controller_output(
                result.stdout.prefix,
                opaque_task_id=str(task["opaque_task_id"]),
                limits=selected_limits,
            )
            if observation.failure_code == "candidate-output-oversized":
                disposition = "candidate-output-oversized"
            elif observation.status != "recorded":
                disposition = "malformed-controller-output"
        if not disposition:
            disposition = "run-captured"

        row["disposition"] = disposition
        if observation is not None and disposition in {
            "run-captured",
            "candidate-output-oversized",
        }:
            row["observation"] = observation.document()
            if observation.capture is not None:
                captures.append(observation.capture)
            if observation.tokens is not None:
                if observation.tokens["method"] == "not_measured":
                    unmeasured_token_runs += 1
                else:
                    measured_token_runs += 1
                    prompt_tokens_total += int(observation.tokens["prompt"])
                    model_tokens_total += int(observation.tokens["model"])
        else:
            row["observation"] = _failure_observation(disposition)
        dispositions[disposition] += 1
        tasks.append(row)

    _directory_identity(artifact_root, artifact_identity, label="run artifact root")
    _recheck_controller(
        selected_controller,
        controller_document,
        controller_identity,
        limits=selected_limits,
    )
    unsigned: dict[str, Any] = {
        "schema": RUN_LEDGER_SCHEMA,
        "suite": {
            "suite_id": suite.suite_id,
            "suite_sha256": suite.suite_sha256,
            "source_snapshot_sha256": source.file_sha256,
            "selected_count": len(records),
            "physical_source_order": True,
        },
        "task_input": {
            "task_input_sha256": verified_task_input["task_input_sha256"],
            "opaque_key_fingerprint": verified_task_input[
                "opaque_key_fingerprint"
            ],
            "source_record_sha256s_sha256": verified_task_input[
                "source_record_sha256s_sha256"
            ],
            "candidate_input_sha256s_sha256": verified_task_input[
                "candidate_input_sha256s_sha256"
            ],
            "task_count": verified_task_input["task_count"],
        },
        "system": selected_system.to_dict(),
        "controller": controller_document,
        "limits": _limits_document(selected_limits),
        "cohort": {
            "selected_count": len(records),
            "launch_attempt_count": launch_attempt_count,
            "lifecycle_result_count": lifecycle_result_count,
            "capture_count": len(captures),
            "disposition_counts": {
                disposition: dispositions[disposition]
                for disposition in _DISPOSITIONS
            },
            "tokens": {
                "measured_run_count": measured_token_runs,
                "not_measured_run_count": unmeasured_token_runs,
                "prompt_tokens_total": prompt_tokens_total,
                "model_tokens_total": model_tokens_total,
            },
        },
        "tasks": tasks,
        "artifact_layout": {
            "root_path_serialized": False,
            "labels_are_relative": True,
            "request_format": "canonical-json-two-field",
            "raw_stream_format": "exact-bounded-prefix-bytes",
        },
        "evidence_state": {
            "full_selected_cohort_reconciled": True,
            "source_and_task_input_reverified": True,
            "repository_outcomes_reconciled": True,
            "successful_preparations_reverified": True,
            "expected_system_reconciled": True,
            "controller_lifecycle_owned": True,
            "raw_stream_prefixes_retained": True,
            "captures_replayed_from_raw_stdout": True,
            "controller_launch_attempted": launch_attempt_count > 0,
            "controller_lifecycle_result_observed": lifecycle_result_count > 0,
            "candidate_execution_authenticated": False,
            "candidate_mount_created": False,
            "filesystem_isolation_verified": False,
            "network_isolation_verified": False,
            "user_isolation_verified": False,
            "pid_isolation_verified": False,
            "immutable_image_verified": False,
            "controller_producer_authenticated": False,
            "system_identity_authenticated": False,
            "model_identity_authenticated": False,
            "trajectory_authenticated": False,
            "token_counts_authenticated": False,
            "hidden_tests_applied": False,
            "grading_performed": False,
            "resolution_computed": False,
            "external_score_computed": False,
            "usefulness_measured": False,
            "claim_ready": False,
            "self_hash_authenticates_producer": False,
        },
    }
    unsigned["run_ledger_sha256"] = _canonical_sha256(unsigned)
    if len(_canonical_json_bytes(unsigned)) > selected_limits.max_ledger_bytes:
        raise SweBenchRunError("run ledger exceeds its byte limit")
    ledger = VerifiedSweBenchRunLedger(
        artifact_root=artifact_root,
        workspaces=selected_workspaces,
        preparations=retained_preparations,
        controller=selected_controller,
        limits=selected_limits,
        captures=tuple(captures),
        document=_freeze_json(unsigned),
    )
    return verify_run_ledger(
        ledger,
        source,
        verified_task_input,
        opaque_key=opaque_key,
        system=selected_system,
        controller=selected_controller,
    )


run_swebench_controller = build_run_ledger


def _decode_file_evidence(
    value: Any,
    *,
    label: str,
    maximum: int,
    allow_empty: bool,
) -> dict[str, Any]:
    payload = _object(value, fields={"label", "byte_count", "sha256"}, label=label)
    artifact_label = _portable_label(payload["label"], label=f"{label}.label")
    if "/" in artifact_label:
        raise SweBenchRunError(f"{label}.label must be a direct child name")
    _bounded_integer(
        payload["byte_count"],
        label=f"{label}.byte_count",
        minimum=0 if allow_empty else 1,
        maximum=maximum,
    )
    _sha256(payload["sha256"], label=f"{label}.sha256")
    return payload


def _decode_workspace_summary(value: Any, *, label: str) -> dict[str, Any]:
    payload = _object(
        value,
        fields={"file_count", "directory_count", "total_bytes", "sha256"},
        label=label,
    )
    _bounded_integer(
        payload["file_count"],
        label=f"{label}.file_count",
        minimum=0,
        maximum=_MAX_HARD_WORKSPACE_ENTRIES,
    )
    _bounded_integer(
        payload["directory_count"],
        label=f"{label}.directory_count",
        minimum=1,
        maximum=_MAX_HARD_WORKSPACE_ENTRIES + 1,
    )
    _bounded_integer(
        payload["total_bytes"],
        label=f"{label}.total_bytes",
        minimum=0,
        maximum=_MAX_HARD_WORKSPACE_BYTES,
    )
    _sha256(payload["sha256"], label=f"{label}.sha256")
    return payload


def _decode_workspace_delta(value: Any, *, label: str) -> dict[str, Any]:
    fields = {
        "added_file_count",
        "deleted_file_count",
        "modified_file_count",
        "added_directory_count",
        "deleted_directory_count",
        "sha256",
    }
    payload = _object(value, fields=fields, label=label)
    for name in fields - {"sha256"}:
        _bounded_integer(
            payload[name],
            label=f"{label}.{name}",
            minimum=0,
            maximum=_MAX_HARD_WORKSPACE_ENTRIES,
        )
    _sha256(payload["sha256"], label=f"{label}.sha256")
    return payload


def _decode_process_limits(value: Any) -> LiteralProcessLimits:
    payload = _object(
        value,
        fields={
            "timeout_seconds",
            "poll_interval_seconds",
            "max_stdout_bytes",
            "max_stderr_bytes",
            "max_memory_mb",
        },
        label="run controller process limits",
    )
    try:
        return LiteralProcessLimits(**payload)
    except (TypeError, LiteralProcessError) as exc:
        raise SweBenchRunError("run controller process limits are invalid") from exc


def _decode_controller_document(value: Any) -> dict[str, Any]:
    payload = _object(
        value,
        fields={
            "protocol",
            "base_argv_count",
            "base_argv_sha256",
            "argv_suffix",
            "executable",
            "environment_names",
            "environment_sha256",
            "process_limits",
        },
        label="run controller",
    )
    if payload["protocol"] != CONTROLLER_PROTOCOL:
        raise SweBenchRunError("run controller protocol mismatch")
    _bounded_integer(
        payload["base_argv_count"],
        label="run controller base argv count",
        minimum=1,
        maximum=254,
    )
    _sha256(payload["base_argv_sha256"], label="run controller base argv SHA-256")
    if payload["argv_suffix"] != [
        "request-json-path",
        "candidate-workspace-path",
    ]:
        raise SweBenchRunError("run controller argv suffix mismatch")
    executable = _object(
        payload["executable"],
        fields={"byte_count", "sha256"},
        label="run controller executable",
    )
    _bounded_integer(
        executable["byte_count"],
        label="run controller executable byte count",
        minimum=1,
        maximum=_MAX_HARD_EXECUTABLE_BYTES,
    )
    _sha256(executable["sha256"], label="run controller executable SHA-256")
    names = payload["environment_names"]
    if (
        type(names) is not list
        or len(names) > 1024
        or any(type(name) is not str or not name for name in names)
        or names != sorted(set(names))
    ):
        raise SweBenchRunError("run controller environment names are invalid")
    _sha256(payload["environment_sha256"], label="run controller environment SHA-256")
    _decode_process_limits(payload["process_limits"])
    return payload


def _decode_system_document(value: Any) -> dict[str, Any]:
    fields = {
        "code_revision",
        "repository_clean",
        "model_name_or_path",
        "model_sha256",
        "agent_sha256",
        "prompt_sha256",
        "tool_sha256",
        "controller_sha256",
    }
    payload = _object(value, fields=fields, label="run system")
    try:
        return SystemIdentity(**payload).to_dict()
    except (TypeError, PredictionLedgerError) as exc:
        raise SweBenchRunError("run system identity is invalid") from exc


def _decode_repository_document(value: Any, *, label: str) -> dict[str, Any]:
    payload = _object(
        value,
        fields={"status", "preparation_sha256", "failure_code"},
        label=label,
    )
    status = payload["status"]
    if type(status) is not str or status not in _PREPARATION_STATUSES:
        raise SweBenchRunError(f"{label}.status is invalid")
    if status == "prepared":
        _sha256(payload["preparation_sha256"], label=f"{label}.preparation_sha256")
        if payload["failure_code"] is not None:
            raise SweBenchRunError(f"{label} prepared row has a failure code")
    elif payload["preparation_sha256"] is not None:
        raise SweBenchRunError(f"{label} unprepared row has a preparation hash")
    elif status == "refused":
        _code(payload["failure_code"], label=f"{label}.failure_code")
    elif payload["failure_code"] is not None:
        raise SweBenchRunError(f"{label} not-attempted row has a failure code")
    return payload


def _decode_stream(
    value: Any,
    *,
    label: str,
    maximum: int,
) -> dict[str, Any]:
    payload = _object(
        value,
        fields={"artifact", "observed_bytes", "captured_bytes", "complete"},
        label=label,
    )
    artifact = _decode_file_evidence(
        payload["artifact"],
        label=f"{label}.artifact",
        maximum=maximum + 1,
        allow_empty=True,
    )
    observed = _bounded_integer(
        payload["observed_bytes"],
        label=f"{label}.observed_bytes",
        minimum=0,
        maximum=2**63 - 1,
    )
    captured = _bounded_integer(
        payload["captured_bytes"],
        label=f"{label}.captured_bytes",
        minimum=0,
        maximum=maximum + 1,
    )
    if captured != artifact["byte_count"] or captured > observed:
        raise SweBenchRunError(f"{label} retained byte accounting is invalid")
    expected_captured = min(observed, maximum + 1)
    if captured != expected_captured:
        raise SweBenchRunError(f"{label} does not retain the exact bounded prefix")
    if type(payload["complete"]) is not bool:
        raise SweBenchRunError(f"{label}.complete must be a boolean")
    return payload


def _decode_process(value: Any, *, label: str) -> dict[str, Any]:
    payload = _object(
        value,
        fields={
            "argv_count",
            "argv_sha256",
            "cwd_scope",
            "exit_code",
            "duration_seconds",
            "setup_duration_seconds",
            "process_duration_seconds",
            "cleanup_duration_seconds",
            "termination_trigger",
            "execution_error",
            "cleanup_error",
            "cleanup_detail",
            "memory_limit_scope",
            "containment_scope",
            "swebench_containment_claim_ready",
            "environment_names",
            "environment_sha256",
        },
        label=label,
    )
    _bounded_integer(
        payload["argv_count"],
        label=f"{label}.argv_count",
        minimum=3,
        maximum=256,
    )
    _sha256(payload["argv_sha256"], label=f"{label}.argv_sha256")
    if payload["cwd_scope"] != "candidate-workspace":
        raise SweBenchRunError(f"{label}.cwd_scope is invalid")
    exit_code = payload["exit_code"]
    if exit_code is not None:
        _bounded_integer(
            exit_code,
            label=f"{label}.exit_code",
            minimum=-(2**31),
            maximum=2**31 - 1,
        )
    durations: dict[str, float] = {}
    for name in (
        "duration_seconds",
        "setup_duration_seconds",
        "process_duration_seconds",
        "cleanup_duration_seconds",
    ):
        durations[name] = _finite_number(
            payload[name],
            label=f"{label}.{name}",
            maximum=7 * 24 * 3600 + 300,
        )
    if any(
        durations[name] > durations["duration_seconds"]
        for name in (
            "setup_duration_seconds",
            "process_duration_seconds",
            "cleanup_duration_seconds",
        )
    ):
        raise SweBenchRunError(f"{label} phase duration exceeds total duration")
    trigger = payload["termination_trigger"]
    if trigger is not None and trigger not in _PROCESS_TRIGGERS:
        raise SweBenchRunError(f"{label}.termination_trigger is invalid")
    for name in ("execution_error", "cleanup_error", "cleanup_detail"):
        nested = payload[name]
        if nested is not None and (
            type(nested) is not str or not nested or len(nested) > 4096
        ):
            raise SweBenchRunError(f"{label}.{name} is invalid")
    execution_error_expected = trigger in {
        "stream_observation_failed",
        "process_observation_failed",
    }
    if (payload["execution_error"] is not None) is not execution_error_expected:
        raise SweBenchRunError(f"{label} execution trigger/error evidence is inconsistent")
    if (payload["cleanup_error"] is None) is not (payload["cleanup_detail"] is None):
        raise SweBenchRunError(f"{label} cleanup error/detail evidence is incomplete")
    if (
        trigger is None
        and payload["cleanup_error"] is None
        and payload["exit_code"] is None
    ):
        raise SweBenchRunError(f"{label} completed process lacks an exit code")
    for name in ("memory_limit_scope", "containment_scope"):
        nested = payload[name]
        if type(nested) is not str or not nested or len(nested) > 512:
            raise SweBenchRunError(f"{label}.{name} is invalid")
    _boolean(
        payload["swebench_containment_claim_ready"],
        expected=False,
        label=f"{label}.swebench_containment_claim_ready",
    )
    names = payload["environment_names"]
    if (
        type(names) is not list
        or len(names) > 1024
        or any(type(name) is not str or not name for name in names)
        or names != sorted(set(names))
    ):
        raise SweBenchRunError(f"{label}.environment_names is invalid")
    _sha256(payload["environment_sha256"], label=f"{label}.environment_sha256")
    return payload


def _decode_observation(
    value: Any,
    *,
    label: str,
    limits: RunLedgerLimits,
) -> dict[str, Any]:
    payload = _object(
        value,
        fields={"status", "failure_code", "patch", "tokens", "trajectory"},
        label=label,
    )
    if payload["status"] not in {"recorded", "not-recorded"}:
        raise SweBenchRunError(f"{label}.status is invalid")
    if payload["failure_code"] is not None:
        _code(payload["failure_code"], label=f"{label}.failure_code")
    if payload["tokens"] is not None:
        _token_document(payload["tokens"], limits=limits)
    trajectory = payload["trajectory"]
    if trajectory is not None:
        trajectory = _object(
            trajectory,
            fields={
                "event_count",
                "byte_count",
                "sha256",
                "ordered",
                "retained_in_raw_stdout",
            },
            label=f"{label}.trajectory",
        )
        _bounded_integer(
            trajectory["event_count"],
            label=f"{label}.trajectory.event_count",
            minimum=0,
            maximum=limits.max_trajectory_events,
        )
        _bounded_integer(
            trajectory["byte_count"],
            label=f"{label}.trajectory.byte_count",
            minimum=2,
            maximum=limits.max_trajectory_bytes,
        )
        _sha256(trajectory["sha256"], label=f"{label}.trajectory.sha256")
        _boolean(
            trajectory["ordered"],
            expected=True,
            label=f"{label}.trajectory.ordered",
        )
        _boolean(
            trajectory["retained_in_raw_stdout"],
            expected=True,
            label=f"{label}.trajectory.retained_in_raw_stdout",
        )
    patch = payload["patch"]
    if payload["status"] == "recorded":
        if payload["failure_code"] is not None or payload["tokens"] is None or trajectory is None:
            raise SweBenchRunError(f"{label} recorded state is inconsistent")
        patch = _object(
            patch,
            fields={"byte_count", "sha256"},
            label=f"{label}.patch",
        )
        _bounded_integer(
            patch["byte_count"],
            label=f"{label}.patch.byte_count",
            minimum=0,
            maximum=limits.max_patch_bytes,
        )
        _sha256(patch["sha256"], label=f"{label}.patch.sha256")
    elif payload["failure_code"] == "candidate-output-oversized":
        if payload["tokens"] is None or trajectory is None:
            raise SweBenchRunError(f"{label} oversized state is incomplete")
        patch = _object(
            patch,
            fields={
                "observed_bytes",
                "retained_prefix_bytes",
                "retained_prefix_sha256",
            },
            label=f"{label}.patch",
        )
        observed = _bounded_integer(
            patch["observed_bytes"],
            label=f"{label}.patch.observed_bytes",
            minimum=limits.max_patch_bytes + 1,
            maximum=_MAX_HARD_PATCH_BYTES + 1,
        )
        retained = _bounded_integer(
            patch["retained_prefix_bytes"],
            label=f"{label}.patch.retained_prefix_bytes",
            minimum=limits.max_patch_bytes + 1,
            maximum=limits.max_patch_bytes + 1,
        )
        if retained > observed:
            raise SweBenchRunError(f"{label} oversized prefix accounting is invalid")
        _sha256(
            patch["retained_prefix_sha256"],
            label=f"{label}.patch.retained_prefix_sha256",
        )
    elif patch is not None or payload["tokens"] is not None or trajectory is not None:
        raise SweBenchRunError(f"{label} failed state retains controller output")
    return payload


def decode_run_ledger(value: Any) -> dict[str, Any]:
    """Strictly decode one self-hashed, pathless controller-run ledger."""

    payload = _object(
        value,
        fields={
            "schema",
            "suite",
            "task_input",
            "system",
            "controller",
            "limits",
            "cohort",
            "tasks",
            "artifact_layout",
            "evidence_state",
            "run_ledger_sha256",
        },
        label="run ledger",
    )
    if payload["schema"] != RUN_LEDGER_SCHEMA:
        raise SweBenchRunError(f"run ledger.schema must equal {RUN_LEDGER_SCHEMA!r}")
    limits_payload = _object(
        payload["limits"],
        fields=set(_LIMIT_FIELDS),
        label="run ledger limits",
    )
    try:
        limits = RunLedgerLimits(**limits_payload)
    except (TypeError, SweBenchRunError) as exc:
        raise SweBenchRunError("run ledger limits are invalid") from exc

    suite = _object(
        payload["suite"],
        fields={
            "suite_id",
            "suite_sha256",
            "source_snapshot_sha256",
            "selected_count",
            "physical_source_order",
        },
        label="run suite",
    )
    _code(suite["suite_id"], label="run suite id")
    _sha256(suite["suite_sha256"], label="run suite SHA-256")
    _sha256(
        suite["source_snapshot_sha256"],
        label="run source snapshot SHA-256",
    )
    selected_count = _bounded_integer(
        suite["selected_count"],
        label="run selected count",
        minimum=1,
        maximum=_MAX_SELECTED_TASKS,
    )
    _boolean(
        suite["physical_source_order"],
        expected=True,
        label="run suite physical_source_order",
    )
    task_input = _object(
        payload["task_input"],
        fields={
            "task_input_sha256",
            "opaque_key_fingerprint",
            "source_record_sha256s_sha256",
            "candidate_input_sha256s_sha256",
            "task_count",
        },
        label="run task input",
    )
    for name in (
        "task_input_sha256",
        "opaque_key_fingerprint",
        "source_record_sha256s_sha256",
        "candidate_input_sha256s_sha256",
    ):
        _sha256(task_input[name], label=f"run task input {name}")
    if _bounded_integer(
        task_input["task_count"],
        label="run task count",
        minimum=1,
        maximum=_MAX_SELECTED_TASKS,
    ) != selected_count:
        raise SweBenchRunError("run task count mismatch")
    _decode_system_document(payload["system"])
    controller = _decode_controller_document(payload["controller"])
    process_limits = _decode_process_limits(controller["process_limits"])

    cohort = _object(
        payload["cohort"],
        fields={
            "selected_count",
            "launch_attempt_count",
            "lifecycle_result_count",
            "capture_count",
            "disposition_counts",
            "tokens",
        },
        label="run cohort",
    )
    if cohort["selected_count"] != selected_count:
        raise SweBenchRunError("run cohort selected count mismatch")
    launch_count = _bounded_integer(
        cohort["launch_attempt_count"],
        label="run launch attempt count",
        minimum=0,
        maximum=selected_count,
    )
    lifecycle_result_count = _bounded_integer(
        cohort["lifecycle_result_count"],
        label="run lifecycle result count",
        minimum=0,
        maximum=launch_count,
    )
    capture_count = _bounded_integer(
        cohort["capture_count"],
        label="run capture count",
        minimum=0,
        maximum=selected_count,
    )
    disposition_counts = _object(
        cohort["disposition_counts"],
        fields=set(_DISPOSITIONS),
        label="run disposition counts",
    )
    decoded_dispositions = {
        name: _bounded_integer(
            disposition_counts[name],
            label=f"run disposition count {name}",
            minimum=0,
            maximum=selected_count,
        )
        for name in _DISPOSITIONS
    }
    if sum(decoded_dispositions.values()) != selected_count:
        raise SweBenchRunError("run dispositions are not exhaustive")
    token_totals = _object(
        cohort["tokens"],
        fields={
            "measured_run_count",
            "not_measured_run_count",
            "prompt_tokens_total",
            "model_tokens_total",
        },
        label="run cohort tokens",
    )
    for name in ("measured_run_count", "not_measured_run_count"):
        _bounded_integer(
            token_totals[name],
            label=f"run cohort tokens {name}",
            minimum=0,
            maximum=capture_count,
        )
    for name in ("prompt_tokens_total", "model_tokens_total"):
        _bounded_integer(
            token_totals[name],
            label=f"run cohort tokens {name}",
            minimum=0,
            maximum=limits.max_token_count * max(1, selected_count),
        )

    tasks = payload["tasks"]
    if type(tasks) is not list or len(tasks) != selected_count:
        raise SweBenchRunError("run tasks must cover selected_count")
    observed_instances: set[str] = set()
    observed_opaque_ids: set[str] = set()
    observed_labels: set[str] = set()
    observed_dispositions: Counter[str] = Counter()
    observed_launches = 0
    observed_lifecycle_results = 0
    observed_captures = 0
    observed_measured = 0
    observed_unmeasured = 0
    observed_prompt_tokens = 0
    observed_model_tokens = 0
    for ordinal, raw in enumerate(tasks):
        row = _object(
            raw,
            fields={
                "ordinal",
                "instance_id",
                "opaque_task_id",
                "source_record_sha256",
                "candidate_input_sha256",
                "repository",
                "workspace",
                "request",
                "process",
                "stdout",
                "stderr",
                "observation",
                "disposition",
            },
            label=f"run tasks[{ordinal}]",
        )
        if row["ordinal"] != ordinal:
            raise SweBenchRunError(f"run tasks[{ordinal}] ordinal mismatch")
        instance_id = row["instance_id"]
        opaque_id = row["opaque_task_id"]
        if type(instance_id) is not str or _INSTANCE_RE.fullmatch(instance_id) is None:
            raise SweBenchRunError(f"run tasks[{ordinal}] instance id is invalid")
        if type(opaque_id) is not str or _OPAQUE_ID_RE.fullmatch(opaque_id) is None:
            raise SweBenchRunError(f"run tasks[{ordinal}] opaque id is invalid")
        if instance_id in observed_instances or opaque_id in observed_opaque_ids:
            raise SweBenchRunError("run task identities are not unique")
        observed_instances.add(instance_id)
        observed_opaque_ids.add(opaque_id)
        _sha256(
            row["source_record_sha256"],
            label=f"run tasks[{ordinal}] source record SHA-256",
        )
        _sha256(
            row["candidate_input_sha256"],
            label=f"run tasks[{ordinal}] candidate input SHA-256",
        )
        repository = _decode_repository_document(
            row["repository"],
            label=f"run tasks[{ordinal}] repository",
        )
        workspace = _object(
            row["workspace"],
            fields={
                "supplied",
                "initial",
                "final",
                "delta",
                "final_failure_code",
            },
            label=f"run tasks[{ordinal}] workspace",
        )
        if type(workspace["supplied"]) is not bool:
            raise SweBenchRunError("run workspace supplied must be a boolean")
        if workspace["initial"] is not None:
            _decode_workspace_summary(
                workspace["initial"],
                label=f"run tasks[{ordinal}] workspace initial",
            )
        if workspace["final"] is not None:
            _decode_workspace_summary(
                workspace["final"],
                label=f"run tasks[{ordinal}] workspace final",
            )
        if workspace["delta"] is not None:
            _decode_workspace_delta(
                workspace["delta"],
                label=f"run tasks[{ordinal}] workspace delta",
            )
        if (workspace["final"] is None) is not (workspace["delta"] is None):
            raise SweBenchRunError("run workspace final/delta evidence is incomplete")
        final_failure_code = workspace["final_failure_code"]
        if final_failure_code is not None and (
            final_failure_code != "workspace-observation-failed"
        ):
            raise SweBenchRunError("run workspace final failure code is invalid")
        if workspace["final"] is not None and final_failure_code is not None:
            raise SweBenchRunError(
                "run workspace retains both final evidence and a failure code"
            )

        request = row["request"]
        stdout = row["stdout"]
        stderr = row["stderr"]
        process = row["process"]
        if request is not None:
            request = _decode_file_evidence(
                request,
                label=f"run tasks[{ordinal}] request",
                maximum=limits.max_request_bytes,
                allow_empty=False,
            )
            observed_launches += 1
        if stdout is not None:
            stdout = _decode_stream(
                stdout,
                label=f"run tasks[{ordinal}] stdout",
                maximum=process_limits.max_stdout_bytes,
            )
        if stderr is not None:
            stderr = _decode_stream(
                stderr,
                label=f"run tasks[{ordinal}] stderr",
                maximum=process_limits.max_stderr_bytes,
            )
        if process is not None:
            process = _decode_process(process, label=f"run tasks[{ordinal}] process")
            observed_lifecycle_results += 1
        for artifact in (
            request,
            stdout["artifact"] if stdout is not None else None,
            stderr["artifact"] if stderr is not None else None,
        ):
            if artifact is not None:
                if artifact["label"] in observed_labels:
                    raise SweBenchRunError("one run artifact label is reused")
                observed_labels.add(artifact["label"])

        observation = _decode_observation(
            row["observation"],
            label=f"run tasks[{ordinal}] observation",
            limits=limits,
        )
        disposition = row["disposition"]
        if type(disposition) is not str or disposition not in _DISPOSITIONS:
            raise SweBenchRunError(f"run tasks[{ordinal}] disposition is invalid")
        observed_dispositions[disposition] += 1
        if repository["status"] == "not-attempted":
            expected = "repository-not-attempted"
        elif repository["status"] == "refused":
            expected = "repository-preparation-refused"
        else:
            expected = None
        if expected is not None and (
            disposition != expected
            or workspace["supplied"]
            or any(
                nested is not None
                for nested in (
                    request,
                    stdout,
                    stderr,
                    process,
                    workspace["initial"],
                    workspace["final"],
                    workspace["delta"],
                    workspace["final_failure_code"],
                )
            )
        ):
            raise SweBenchRunError("unprepared run task retains candidate state")
        if repository["status"] == "prepared":
            if not workspace["supplied"]:
                if disposition != "workspace-unavailable" or request is not None:
                    raise SweBenchRunError("missing workspace disposition mismatch")
            elif request is None:
                if disposition != "workspace-initial-mismatch":
                    raise SweBenchRunError(
                        "prepared supplied workspace lacks an exact prelaunch outcome"
                    )
            elif disposition in {
                "repository-not-attempted",
                "repository-preparation-refused",
                "workspace-unavailable",
                "workspace-initial-mismatch",
            }:
                raise SweBenchRunError(
                    "launched prepared task has a prelaunch disposition"
                )
        if disposition == "workspace-unavailable" and (
            workspace["supplied"]
            or any(
                workspace[name] is not None
                for name in ("initial", "final", "delta", "final_failure_code")
            )
        ):
            raise SweBenchRunError(
                "workspace-unavailable task retains workspace evidence"
            )
        if disposition == "workspace-initial-mismatch" and not workspace["supplied"]:
            raise SweBenchRunError("workspace mismatch lacks a supplied tree")
        if disposition == "workspace-observation-failed" and (
            process is None
            or workspace["final"] is not None
            or workspace["final_failure_code"] != "workspace-observation-failed"
        ):
            raise SweBenchRunError(
                "workspace-observation-failed task has inconsistent evidence"
            )
        if disposition in {
            "run-captured",
            "candidate-output-oversized",
            "malformed-controller-output",
        } and workspace["final"] is None:
            raise SweBenchRunError(
                "controller-output disposition lacks final workspace evidence"
            )
        if request is None:
            if stdout is not None or stderr is not None or process is not None:
                raise SweBenchRunError("run process evidence lacks its request")
            if workspace["final"] is not None or workspace["delta"] is not None:
                raise SweBenchRunError(
                    "unlaunched run retains final workspace evidence"
                )
            if workspace["final_failure_code"] is not None:
                raise SweBenchRunError(
                    "unlaunched run retains a final workspace failure"
                )
            if disposition not in {
                "repository-not-attempted",
                "repository-preparation-refused",
                "workspace-unavailable",
                "workspace-initial-mismatch",
            }:
                raise SweBenchRunError("run disposition lacks a launch request")
        else:
            if not workspace["supplied"]:
                raise SweBenchRunError("launched run lacks a supplied workspace")
            if stdout is None or stderr is None or workspace["initial"] is None:
                raise SweBenchRunError("launched run evidence is incomplete")
            if process is None and disposition != "launch-failed":
                raise SweBenchRunError("missing process result is not launch-failed")
            if process is not None and disposition == "launch-failed":
                raise SweBenchRunError("launch-failed row retains a process result")
            if process is None and (
                stdout["observed_bytes"] != 0
                or stdout["captured_bytes"] != 0
                or stdout["complete"]
                or stderr["observed_bytes"] != 0
                or stderr["captured_bytes"] != 0
                or stderr["complete"]
            ):
                raise SweBenchRunError(
                    "launch-failed row has inconsistent unobserved streams"
                )
            if workspace["final"] is None:
                if workspace["final_failure_code"] != "workspace-observation-failed":
                    raise SweBenchRunError(
                        "launched run lacks final workspace failure evidence"
                    )
            elif workspace["final_failure_code"] is not None:
                raise SweBenchRunError(
                    "launched run has inconsistent final workspace evidence"
                )
            if process is not None:
                trigger = process["termination_trigger"]
                stdout_overflow = (
                    stdout["observed_bytes"] > process_limits.max_stdout_bytes
                )
                stderr_overflow = (
                    stderr["observed_bytes"] > process_limits.max_stderr_bytes
                )
                if trigger is None and (
                    stdout_overflow
                    or stderr_overflow
                    or not stdout["complete"]
                    or not stderr["complete"]
                ):
                    raise SweBenchRunError(
                        "successful process has impossible stream evidence"
                    )
                if trigger == "stdout_limit" and not stdout_overflow:
                    raise SweBenchRunError(
                        "stdout-limit process lacks an overflow witness"
                    )
                if trigger == "stderr_limit" and not stderr_overflow:
                    raise SweBenchRunError(
                        "stderr-limit process lacks an overflow witness"
                    )
                if trigger == "stream_observation_failed" and (
                    stdout["complete"] and stderr["complete"]
                ):
                    raise SweBenchRunError(
                        "stream-observation failure requires an incomplete stream"
                    )
        if disposition == "run-captured":
            if observation["status"] != "recorded":
                raise SweBenchRunError("run-captured task lacks a recorded observation")
            observed_captures += 1
        elif disposition == "candidate-output-oversized":
            if observation["failure_code"] != "candidate-output-oversized":
                raise SweBenchRunError("oversized task observation mismatch")
            observed_captures += 1
        elif observation["status"] == "recorded":
            raise SweBenchRunError("failed run retains a recorded observation")
        if (
            disposition not in {"run-captured", "candidate-output-oversized"}
            and observation["failure_code"] is None
        ):
            raise SweBenchRunError("failed run lacks a failure code")
        if disposition == "workspace-initial-mismatch":
            expected_failure = (
                "workspace-initial-mismatch"
                if workspace["initial"] is not None
                else "workspace-inspection-failed"
            )
            if observation["failure_code"] != expected_failure:
                raise SweBenchRunError(
                    "workspace mismatch evidence/failure code is inconsistent"
                )
        elif disposition == "malformed-controller-output":
            if observation["failure_code"] != "malformed-controller-output":
                raise SweBenchRunError("malformed output failure code mismatch")
        elif (
            disposition not in {"run-captured", "candidate-output-oversized"}
            and observation["failure_code"] != disposition
        ):
            raise SweBenchRunError("run disposition/failure code mismatch")
        tokens = observation["tokens"]
        if tokens is not None:
            if tokens["method"] == "not_measured":
                observed_unmeasured += 1
            else:
                observed_measured += 1
                observed_prompt_tokens += int(tokens["prompt"])
                observed_model_tokens += int(tokens["model"])

    if (
        observed_launches != launch_count
        or observed_lifecycle_results != lifecycle_result_count
    ):
        raise SweBenchRunError(
            "run launch/lifecycle-result counts do not match task rows"
        )
    if observed_captures != capture_count:
        raise SweBenchRunError("run capture count does not match task rows")
    if any(
        observed_dispositions[name] != decoded_dispositions[name]
        for name in _DISPOSITIONS
    ):
        raise SweBenchRunError("run disposition counts do not match task rows")
    expected_tokens = {
        "measured_run_count": observed_measured,
        "not_measured_run_count": observed_unmeasured,
        "prompt_tokens_total": observed_prompt_tokens,
        "model_tokens_total": observed_model_tokens,
    }
    if token_totals != expected_tokens:
        raise SweBenchRunError("run token totals do not match task rows")

    layout = _object(
        payload["artifact_layout"],
        fields={
            "root_path_serialized",
            "labels_are_relative",
            "request_format",
            "raw_stream_format",
        },
        label="run artifact layout",
    )
    if layout != {
        "root_path_serialized": False,
        "labels_are_relative": True,
        "request_format": "canonical-json-two-field",
        "raw_stream_format": "exact-bounded-prefix-bytes",
    }:
        raise SweBenchRunError("run artifact layout is invalid")
    evidence_fields = {
        "full_selected_cohort_reconciled",
        "source_and_task_input_reverified",
        "repository_outcomes_reconciled",
        "successful_preparations_reverified",
        "expected_system_reconciled",
        "controller_lifecycle_owned",
        "raw_stream_prefixes_retained",
        "captures_replayed_from_raw_stdout",
        "controller_launch_attempted",
        "controller_lifecycle_result_observed",
        "candidate_execution_authenticated",
        "candidate_mount_created",
        "filesystem_isolation_verified",
        "network_isolation_verified",
        "user_isolation_verified",
        "pid_isolation_verified",
        "immutable_image_verified",
        "controller_producer_authenticated",
        "system_identity_authenticated",
        "model_identity_authenticated",
        "trajectory_authenticated",
        "token_counts_authenticated",
        "hidden_tests_applied",
        "grading_performed",
        "resolution_computed",
        "external_score_computed",
        "usefulness_measured",
        "claim_ready",
        "self_hash_authenticates_producer",
    }
    evidence = _object(
        payload["evidence_state"],
        fields=evidence_fields,
        label="run evidence state",
    )
    for name in (
        "full_selected_cohort_reconciled",
        "source_and_task_input_reverified",
        "repository_outcomes_reconciled",
        "successful_preparations_reverified",
        "expected_system_reconciled",
        "controller_lifecycle_owned",
        "raw_stream_prefixes_retained",
        "captures_replayed_from_raw_stdout",
    ):
        _boolean(evidence[name], expected=True, label=f"run evidence {name}")
    _boolean(
        evidence["controller_launch_attempted"],
        expected=launch_count > 0,
        label="run evidence controller_launch_attempted",
    )
    _boolean(
        evidence["controller_lifecycle_result_observed"],
        expected=lifecycle_result_count > 0,
        label="run evidence controller_lifecycle_result_observed",
    )
    for name in evidence_fields - {
        "full_selected_cohort_reconciled",
        "source_and_task_input_reverified",
        "repository_outcomes_reconciled",
        "successful_preparations_reverified",
        "expected_system_reconciled",
        "controller_lifecycle_owned",
        "raw_stream_prefixes_retained",
        "captures_replayed_from_raw_stdout",
        "controller_launch_attempted",
        "controller_lifecycle_result_observed",
    }:
        _boolean(evidence[name], expected=False, label=f"run evidence {name}")
    claimed = _sha256(payload["run_ledger_sha256"], label="run ledger SHA-256")
    unsigned = dict(payload)
    unsigned.pop("run_ledger_sha256")
    if _canonical_sha256(unsigned) != claimed:
        raise SweBenchRunError("run ledger SHA-256 mismatch")
    if len(_canonical_json_bytes(payload)) > limits.max_ledger_bytes:
        raise SweBenchRunError("run ledger exceeds its byte limit")
    return payload


def _expected_workspace_summary(
    preparation: RepositoryPreparationOutcome,
) -> dict[str, Any]:
    files, directories, total_bytes, digest = _expected_prepared_snapshot(preparation)
    return {
        "file_count": len(files),
        "directory_count": len(directories),
        "total_bytes": total_bytes,
        "sha256": digest,
    }


def _prepared_workspace_snapshot(
    preparation: RepositoryPreparationOutcome,
    *,
    root_identity: tuple[int, int],
) -> _WorkspaceSnapshot:
    files, directories, total_bytes, digest = _expected_prepared_snapshot(preparation)
    return _WorkspaceSnapshot(
        root_identity=root_identity,
        files=files,
        directories=directories,
        total_bytes=total_bytes,
        sha256=digest,
    )


def _read_artifact(
    root: Path,
    root_identity: tuple[int, int],
    evidence: Mapping[str, Any],
    *,
    label: str,
    maximum: int,
) -> bytes:
    _directory_identity(root, root_identity, label="run artifact root")
    artifact_label = _portable_label(evidence["label"], label=f"{label}.label")
    if "/" in artifact_label:
        raise SweBenchRunError(f"{label}.label must be a direct child name")
    path = root / artifact_label
    identity = _single_link_regular(path, label=label)
    try:
        retained = read_bounded_regular_file(
            path,
            max_bytes=max(1, maximum),
            label=label,
            expected_identity=identity,
        )
    except StrictJsonError as exc:
        raise SweBenchRunError(str(exc)) from exc
    if _single_link_regular(path, label=label) != identity:
        raise SweBenchRunError(f"{label} identity changed while read")
    if (
        retained.byte_count != evidence["byte_count"]
        or retained.file_sha256 != evidence["sha256"]
    ):
        raise SweBenchRunError(f"{label} bytes do not match the run ledger")
    return retained.value


def _scan_artifact_labels(
    root: Path,
    identity: tuple[int, int],
    *,
    maximum: int,
) -> set[str]:
    _directory_identity(root, identity, label="run artifact root")
    labels: set[str] = set()
    try:
        iterator = os.scandir(root)
    except OSError as exc:
        raise SweBenchRunError("run artifact root could not be scanned") from exc
    try:
        with iterator:
            for child in iterator:
                if len(labels) >= maximum:
                    raise SweBenchRunError("run artifact root exceeds its entry limit")
                try:
                    child_path = Path(child.path)
                    info = child_path.lstat()
                except OSError as exc:
                    raise SweBenchRunError(
                        "run artifact entry could not be inspected"
                    ) from exc
                reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                if (
                    child.is_symlink()
                    or not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or bool(getattr(info, "st_file_attributes", 0) & reparse)
                ):
                    raise SweBenchRunError(
                        "run artifact root contains an unsafe entry"
                    )
                _portable_label(child.name, label="run artifact filename")
                labels.add(child.name)
    except OSError as exc:
        raise SweBenchRunError("run artifact root scan failed") from exc
    _directory_identity(root, identity, label="run artifact root")
    return labels


def _document_process_disposition(process: Mapping[str, Any]) -> str | None:
    trigger = process["termination_trigger"]
    if trigger == "timeout":
        return "execution-timeout"
    if trigger == "stdout_limit":
        return "stdout-limit-exceeded"
    if trigger == "stderr_limit":
        return "stderr-limit-exceeded"
    if trigger == "stream_observation_failed":
        return "stream-observation-failed"
    if trigger == "process_observation_failed":
        return "process-observation-failed"
    if trigger is not None or process["execution_error"] is not None:
        raise SweBenchRunError("run process retains an impossible execution state")
    if process["cleanup_error"] is not None:
        return "cleanup-failed"
    if process["exit_code"] != 0:
        return "nonzero-exit"
    return None


def _expected_process_scopes(limits: LiteralProcessLimits) -> tuple[str, str]:
    memory = (
        "none"
        if limits.max_memory_mb is None
        else "windows-job-process-and-aggregate"
    )
    containment = (
        "windows-job-process-and-descendants"
        if os.name == "nt"
        else "posix-session-process-group-escapable"
    )
    return memory, containment


def verify_run_ledger(
    ledger: VerifiedSweBenchRunLedger,
    source: VerifiedSweBenchSource,
    task_input: Any,
    *,
    opaque_key: bytes,
    system: SystemIdentity,
    controller: ControllerSpec,
    preparations: Sequence[RepositoryPreparationOutcome] | None = None,
    workspaces: Sequence[CandidateWorkspace] | None = None,
) -> VerifiedSweBenchRunLedger:
    """Replay source, preparation, raw output, and live workspace bindings."""

    if type(ledger) is not VerifiedSweBenchRunLedger:
        raise SweBenchRunError("ledger must be VerifiedSweBenchRunLedger")
    runtime_limits = _revalidate_limits(ledger.limits)
    try:
        decoded = decode_run_ledger(ledger.to_dict())
        expected_system = _system_identity(system)
        suite, records, verified_task_input = _source_and_tasks(
            source,
            task_input,
            opaque_key,
        )
    except PredictionLedgerError as exc:
        raise SweBenchRunError(str(exc)) from exc
    decoded_limits = RunLedgerLimits(**decoded["limits"])
    if decoded_limits != runtime_limits:
        raise SweBenchRunError("runtime run limits do not match the ledger")
    runtime_controller, controller_document, _ = _revalidate_controller(
        controller,
        limits=runtime_limits,
    )
    if decoded["controller"] != controller_document:
        raise SweBenchRunError("run ledger controller binding mismatch")
    ledger_controller, ledger_controller_document, _ = _revalidate_controller(
        ledger.controller,
        limits=runtime_limits,
    )
    if ledger_controller_document != controller_document:
        raise SweBenchRunError("runtime ledger controller does not match expected")
    if decoded["system"] != expected_system.to_dict():
        raise SweBenchRunError("run ledger system identity mismatch")
    expected_suite = {
        "suite_id": suite.suite_id,
        "suite_sha256": suite.suite_sha256,
        "source_snapshot_sha256": source.file_sha256,
        "selected_count": len(records),
        "physical_source_order": True,
    }
    if decoded["suite"] != expected_suite:
        raise SweBenchRunError("run ledger suite binding mismatch")
    expected_task_input = {
        "task_input_sha256": verified_task_input["task_input_sha256"],
        "opaque_key_fingerprint": verified_task_input["opaque_key_fingerprint"],
        "source_record_sha256s_sha256": verified_task_input[
            "source_record_sha256s_sha256"
        ],
        "candidate_input_sha256s_sha256": verified_task_input[
            "candidate_input_sha256s_sha256"
        ],
        "task_count": verified_task_input["task_count"],
    }
    if decoded["task_input"] != expected_task_input:
        raise SweBenchRunError("run ledger task-input binding mismatch")

    runtime_preparations = (
        ledger.preparations if preparations is None else preparations
    )
    try:
        retained_preparations, _ = _revalidate_preparations(
            runtime_preparations,
            source=source,
            task_input=verified_task_input,
        )
    except PredictionLedgerError as exc:
        raise SweBenchRunError(str(exc)) from exc
    runtime_workspaces = ledger.workspaces if workspaces is None else workspaces
    retained_workspaces = _revalidate_workspaces(
        runtime_workspaces,
        tasks=verified_task_input["tasks"],
    )
    artifact_root, artifact_identity = _safe_directory(
        ledger.artifact_root,
        label="run artifact root",
    )
    _preflight_workspace_boundaries(
        retained_workspaces,
        retained_preparations,
        artifact_root,
    )

    expected_labels: set[str] = set()
    replayed_captures: list[CandidatePatchCapture] = []
    for ordinal, (record, task, preparation, workspace, row) in enumerate(
        zip(
            records,
            verified_task_input["tasks"],
            retained_preparations,
            retained_workspaces,
            decoded["tasks"],
            strict=True,
        )
    ):
        expected_binding = {
            "ordinal": ordinal,
            "instance_id": record["instance_id"],
            "opaque_task_id": task["opaque_task_id"],
            "source_record_sha256": _source_canonical_sha256(record),
            "candidate_input_sha256": _source_canonical_sha256(
                {"problem_statement": record["problem_statement"]}
            ),
        }
        if any(row[name] != value for name, value in expected_binding.items()):
            raise SweBenchRunError(f"run task {ordinal} source binding mismatch")
        if row["repository"] != _repository_document(preparation):
            raise SweBenchRunError(f"run task {ordinal} preparation mismatch")
        if row["workspace"]["supplied"] is not (workspace.path is not None):
            raise SweBenchRunError(f"run task {ordinal} workspace binding mismatch")
        if preparation.status != "prepared":
            if workspace.path is not None:
                raise SweBenchRunError(
                    f"unprepared task {ordinal} has a runtime workspace"
                )
            continue
        if workspace.path is None:
            if row["disposition"] != "workspace-unavailable":
                raise SweBenchRunError(
                    f"prepared task {ordinal} lacks its unavailable disposition"
                )
            continue
        if row["request"] is None and row["disposition"] != (
            "workspace-initial-mismatch"
        ):
            raise SweBenchRunError(
                f"prepared task {ordinal} has an invalid prelaunch disposition"
            )
        if row["workspace"]["initial"] is not None:
            expected_initial = _expected_workspace_summary(preparation)
            initial_matches = row["workspace"]["initial"] == expected_initial
            if row["disposition"] == "workspace-initial-mismatch":
                if initial_matches:
                    raise SweBenchRunError(
                        f"run task {ordinal} falsely reports a workspace mismatch"
                    )
            elif not initial_matches:
                raise SweBenchRunError(
                    f"run task {ordinal} initial workspace was not the prepared tree"
                )
        workspace_path = Path(workspace.path)

        if row["request"] is None:
            if row["workspace"]["initial"] is not None:
                live = _snapshot_workspace(workspace_path, limits=runtime_limits)
                if live.summary() != row["workspace"]["initial"]:
                    raise SweBenchRunError(
                        f"unlaunched workspace {ordinal} changed after recording"
                    )
            continue
        request_evidence = row["request"]
        if request_evidence["label"] != _artifact_label(ordinal, "request.json"):
            raise SweBenchRunError(f"run request {ordinal} label mismatch")
        request_bytes = _read_artifact(
            artifact_root,
            artifact_identity,
            request_evidence,
            label=f"run request {ordinal}",
            maximum=runtime_limits.max_request_bytes,
        )
        expected_labels.add(str(request_evidence["label"]))
        if request_bytes != _task_request(task):
            raise SweBenchRunError(
                f"run request {ordinal} is not the exact two-field task payload"
            )
        stdout_evidence = row["stdout"]
        stderr_evidence = row["stderr"]
        assert stdout_evidence is not None and stderr_evidence is not None
        if stdout_evidence["artifact"]["label"] != _artifact_label(
            ordinal,
            "stdout.bin",
        ):
            raise SweBenchRunError(f"run stdout {ordinal} label mismatch")
        if stderr_evidence["artifact"]["label"] != _artifact_label(
            ordinal,
            "stderr.bin",
        ):
            raise SweBenchRunError(f"run stderr {ordinal} label mismatch")
        stdout_bytes = _read_artifact(
            artifact_root,
            artifact_identity,
            stdout_evidence["artifact"],
            label=f"run stdout {ordinal}",
            maximum=runtime_controller.limits.max_stdout_bytes + 1,
        )
        stderr_bytes = _read_artifact(
            artifact_root,
            artifact_identity,
            stderr_evidence["artifact"],
            label=f"run stderr {ordinal}",
            maximum=runtime_controller.limits.max_stderr_bytes + 1,
        )
        expected_labels.update(
            {
                str(stdout_evidence["artifact"]["label"]),
                str(stderr_evidence["artifact"]["label"]),
            }
        )
        if len(stdout_bytes) != stdout_evidence["captured_bytes"]:
            raise SweBenchRunError(f"run stdout {ordinal} capture count mismatch")
        if len(stderr_bytes) != stderr_evidence["captured_bytes"]:
            raise SweBenchRunError(f"run stderr {ordinal} capture count mismatch")

        process = row["process"]
        if process is None:
            if (
                stdout_bytes
                or stderr_bytes
                or stdout_evidence["complete"]
                or stderr_evidence["complete"]
            ):
                raise SweBenchRunError(
                    "launch-failed task retained inconsistent stream evidence"
                )
        else:
            request_path = artifact_root / str(request_evidence["label"])
            expected_argv = [
                *runtime_controller.argv,
                str(request_path),
                str(workspace_path.resolve(strict=True)),
            ]
            if (
                process["argv_count"] != len(expected_argv)
                or process["argv_sha256"] != _canonical_sha256(expected_argv)
            ):
                raise SweBenchRunError(f"run task {ordinal} argv binding mismatch")
            if (
                process["environment_names"]
                != controller_document["environment_names"]
                or process["environment_sha256"]
                != controller_document["environment_sha256"]
            ):
                raise SweBenchRunError(
                    f"run task {ordinal} environment binding mismatch"
                )
            expected_memory_scope, expected_containment_scope = (
                _expected_process_scopes(runtime_controller.limits)
            )
            if (
                process["memory_limit_scope"] != expected_memory_scope
                or process["containment_scope"] != expected_containment_scope
            ):
                raise SweBenchRunError(
                    f"run task {ordinal} literal-process scope mismatch"
                )
            process_disposition = _document_process_disposition(process)
            if process_disposition is None and (
                not stdout_evidence["complete"] or not stderr_evidence["complete"]
            ):
                process_disposition = "stream-observation-failed"
            if row["disposition"] == "workspace-observation-failed":
                if process_disposition is not None:
                    raise SweBenchRunError(
                        f"run task {ordinal} hides a process failure behind "
                        "workspace observation failure"
                    )
            else:
                if process_disposition is not None and row["disposition"] != process_disposition:
                    raise SweBenchRunError(
                        f"run task {ordinal} process disposition mismatch"
                    )
                if process_disposition is None:
                    replayed = _parse_controller_output(
                        stdout_bytes,
                        opaque_task_id=str(task["opaque_task_id"]),
                        limits=runtime_limits,
                    )
                    if row["disposition"] == "run-captured":
                        if replayed.status != "recorded":
                            raise SweBenchRunError(
                                f"run task {ordinal} raw stdout no longer yields a patch"
                            )
                    elif row["disposition"] == "candidate-output-oversized":
                        if replayed.failure_code != "candidate-output-oversized":
                            raise SweBenchRunError(
                                f"run task {ordinal} oversized stdout mismatch"
                            )
                    elif row["disposition"] == "malformed-controller-output":
                        if replayed.failure_code != "malformed-controller-output":
                            raise SweBenchRunError(
                                f"run task {ordinal} malformed stdout mismatch"
                            )
                    else:
                        raise SweBenchRunError(
                            f"run task {ordinal} successful process disposition is invalid"
                        )
                    if row["disposition"] in {
                        "run-captured",
                        "candidate-output-oversized",
                    }:
                        if replayed.document() != row["observation"]:
                            raise SweBenchRunError(
                                f"run task {ordinal} output evidence mismatch"
                            )
                        assert replayed.capture is not None
                        replayed_captures.append(replayed.capture)

        final_document = row["workspace"]["final"]
        if final_document is not None:
            live_after = _snapshot_workspace(workspace_path, limits=runtime_limits)
            if live_after.summary() != final_document:
                raise SweBenchRunError(
                    f"run task {ordinal} live final workspace changed"
                )
            expected_before = _prepared_workspace_snapshot(
                preparation,
                root_identity=live_after.root_identity,
            )
            if _workspace_delta(expected_before, live_after) != row["workspace"]["delta"]:
                raise SweBenchRunError(
                    f"run task {ordinal} workspace delta evidence mismatch"
                )

    actual_labels = _scan_artifact_labels(
        artifact_root,
        artifact_identity,
        maximum=len(expected_labels),
    )
    if actual_labels != expected_labels:
        raise SweBenchRunError("run artifact root path set does not match the ledger")
    _recheck_controller(
        runtime_controller,
        controller_document,
        _single_link_regular(Path(runtime_controller.argv[0]), label="controller executable"),
        limits=runtime_limits,
    )
    return VerifiedSweBenchRunLedger(
        artifact_root=artifact_root,
        workspaces=retained_workspaces,
        preparations=retained_preparations,
        controller=ledger_controller,
        limits=runtime_limits,
        captures=tuple(replayed_captures),
        document=_freeze_json(decoded),
    )


def _output_path(value: str | Path, *, label: str) -> Path:
    path = _absolute_path(value, label=label)
    name = path.name
    if (
        not name
        or name != unicodedata.normalize("NFC", name)
        or name.endswith((".", " "))
        or ":" in name
        or "/" in name
        or "\\" in name
        or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in name)
    ):
        raise SweBenchRunError(f"{label} filename is not portable")
    return path


def _preflight_ledger_output(
    path: Path,
    ledger: VerifiedSweBenchRunLedger,
) -> None:
    try:
        destination = path.resolve(strict=False)
        forbidden = [ledger.artifact_root.resolve(strict=True)]
        for workspace in ledger.workspaces:
            if workspace.path is not None:
                forbidden.append(Path(workspace.path).resolve(strict=True))
        for preparation in ledger.preparations:
            if preparation.prepared is not None:
                forbidden.extend(
                    (
                        preparation.prepared.path.resolve(strict=True),
                        preparation.prepared.mirror.path.resolve(strict=True),
                    )
                )
    except (OSError, RuntimeError, ValueError) as exc:
        raise SweBenchRunError("run ledger output boundary could not be inspected") from exc
    if any(destination.is_relative_to(root) for root in forbidden):
        raise SweBenchRunError(
            "run ledger output must remain outside artifacts, workspaces, and repositories"
        )


def load_run_ledger(
    ledger_path: str | Path,
    artifact_root: str | Path,
    source: VerifiedSweBenchSource,
    task_input: Any,
    *,
    opaque_key: bytes,
    preparations: Sequence[RepositoryPreparationOutcome],
    workspaces: Sequence[CandidateWorkspace],
    system: SystemIdentity,
    controller: ControllerSpec,
) -> VerifiedSweBenchRunLedger:
    """Load bounded ledger JSON and replay all live coordinator artifacts."""

    input_path = _output_path(ledger_path, label="run ledger input")
    identity = _single_link_regular(input_path, label="run ledger")
    try:
        loaded = load_strict_json_file(
            input_path,
            limits=StrictJsonLimits(
                max_bytes=_MAX_HARD_LEDGER_BYTES,
                max_line_chars=_MAX_HARD_LEDGER_BYTES,
                max_depth=64,
            ),
            label="run ledger",
            allow_bom=False,
            expected_identity=identity,
        )
    except StrictJsonError as exc:
        raise SweBenchRunError(str(exc)) from exc
    if _single_link_regular(input_path, label="run ledger") != identity:
        raise SweBenchRunError("run ledger identity changed while read")
    decoded = decode_run_ledger(loaded.value)
    limits = RunLedgerLimits(**decoded["limits"])
    if loaded.byte_count > limits.max_ledger_bytes:
        raise SweBenchRunError("serialized run ledger exceeds its byte limit")
    retained_preparations = tuple(preparations[: _MAX_SELECTED_TASKS + 1])
    retained_workspaces = tuple(workspaces[: _MAX_SELECTED_TASKS + 1])
    runtime = VerifiedSweBenchRunLedger(
        artifact_root=_absolute_path(artifact_root, label="run artifact root"),
        workspaces=retained_workspaces,
        preparations=retained_preparations,
        controller=controller,
        limits=limits,
        captures=(),
        document=_freeze_json(decoded),
    )
    return verify_run_ledger(
        runtime,
        source,
        task_input,
        opaque_key=opaque_key,
        preparations=retained_preparations,
        workspaces=retained_workspaces,
        system=system,
        controller=controller,
    )


def write_run_ledger(
    path: str | Path,
    ledger: VerifiedSweBenchRunLedger,
    source: VerifiedSweBenchSource,
    task_input: Any,
    *,
    opaque_key: bytes,
    system: SystemIdentity,
    controller: ControllerSpec,
    preparations: Sequence[RepositoryPreparationOutcome] | None = None,
    workspaces: Sequence[CandidateWorkspace] | None = None,
) -> None:
    """Write one live-replayed run ledger outside every candidate boundary."""

    verified = verify_run_ledger(
        ledger,
        source,
        task_input,
        opaque_key=opaque_key,
        preparations=preparations,
        workspaces=workspaces,
        system=system,
        controller=controller,
    )
    output = _output_path(path, label="run ledger output")
    _preflight_ledger_output(output, verified)
    rendered = json.dumps(
        verified.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ) + "\n"
    try:
        encoded = rendered.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise SweBenchRunError("run ledger is not strict UTF-8") from exc
    if len(encoded) > verified.limits.max_ledger_bytes:
        raise SweBenchRunError("serialized run ledger exceeds its byte limit")
    try:
        output_identity = atomic_write_text(output, rendered, overwrite=False)
    except FileExistsError as exc:
        raise SweBenchRunError("run ledger output already exists") from exc
    except (OSError, ValueError) as exc:
        raise SweBenchRunError("run ledger could not be written safely") from exc
    try:
        if _single_link_regular(output, label="run ledger") != output_identity:
            raise SweBenchRunError("written run ledger identity changed")
        try:
            retained = read_bounded_regular_file(
                output,
                max_bytes=verified.limits.max_ledger_bytes,
                label="run ledger",
                expected_identity=output_identity,
            )
        except StrictJsonError as exc:
            raise SweBenchRunError(str(exc)) from exc
        if _single_link_regular(output, label="run ledger") != output_identity:
            raise SweBenchRunError("written run ledger identity changed")
        if retained.value != encoded:
            raise SweBenchRunError("written run ledger bytes changed")
        loaded = load_run_ledger(
            output,
            verified.artifact_root,
            source,
            task_input,
            opaque_key=opaque_key,
            preparations=verified.preparations,
            workspaces=verified.workspaces,
            system=system,
            controller=controller,
        )
        if loaded.to_dict() != verified.to_dict():
            raise SweBenchRunError("written run ledger replay changed its evidence")
    except BaseException as exc:
        try:
            current = output.lstat()
            if (
                stat.S_ISREG(current.st_mode)
                and (current.st_dev, current.st_ino) == output_identity
            ):
                os.unlink(output)
            else:
                raise SweBenchRunError(
                    "failed run ledger output no longer identifies the owned file"
                )
        except (OSError, SweBenchRunError) as cleanup_exc:
            raise SweBenchRunError(
                "run ledger verification failed and cleanup was incomplete: "
                f"{cleanup_exc}"
            ) from exc
        raise
