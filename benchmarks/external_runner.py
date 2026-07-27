"""Resource-bounded, non-interpolating runner for LRCBench external adapters."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, BinaryIO

from context_compiler.atomic import atomic_write_text
from context_compiler.local_qwen import (
    QWEN_Q4_CONTEXT_LENGTH,
    QWEN_Q4_VARIANT,
)
from context_compiler.process_tree import (
    prove_darwin_process_group_all_zombies,
)

from .json_io import (
    StrictFileEvidence,
    StrictJsonError,
    StrictJsonLimits,
    hash_bounded_regular_file,
    load_strict_json_file,
)
from .lrcbench import (
    CANDIDATE_SCHEMA,
    CORPUS_SCHEMA,
    LEGACY_ADAPTER_CANDIDATE_SCHEMA,
    TOKENIZER_ID,
    CandidateProducerMetadata,
    ExternalBaselineError,
    candidate_document,
    decode_corpus_document,
    decode_external_candidate,
)

RUNNER_MANIFEST_SCHEMA = "lrcbench-external-run-manifest-0.13"
_SYSTEM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_REVISION_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_ENVIRONMENT_ID_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ISOLATION_MODES = frozenset({"whole_corpus", "per_case"})
NETWORK_ISOLATION_MODES = frozenset(
    {
        "unverified",
        "container-no-network",
        "network-namespace",
        "host-firewall",
    }
)
CLAIM_NETWORK_ISOLATION_MODES = NETWORK_ISOLATION_MODES - {"unverified"}
_NETWORK_ISOLATION_EVIDENCE_MAX_BYTES = 1_000_000
_DEPENDENCY_LOCK_EVIDENCE_MAX_BYTES = 20_000_000
_ADAPTER_ENTRYPOINT_EVIDENCE_MAX_BYTES = 256_000_000
_ADAPTER_SOURCE_TREE_ALGORITHM = "lrcbench-adapter-source-tree-0.1"
_ADAPTER_SOURCE_MAX_FILES = 10_000
_ADAPTER_SOURCE_MAX_FILE_BYTES = 256_000_000
_ADAPTER_SOURCE_MAX_TOTAL_BYTES = 512_000_000
_ADAPTER_SOURCE_MAX_RELATIVE_PATH_BYTES = 4_096
_ADAPTER_SOURCE_MAX_PATH_BYTES = 1_000_000
_ADAPTER_RUNTIME_EXECUTABLE_MAX_BYTES = 2_000_000_000
_PROCESS_ENVIRONMENT_ALGORITHM = "lrcbench-process-environment-0.1"
_PROCESS_ENVIRONMENT_MAX_VARIABLES = 1_024
_PROCESS_ENVIRONMENT_MAX_NAME_BYTES = 4_096
_PROCESS_ENVIRONMENT_MAX_VALUE_BYTES = 1_000_000
_PROCESS_ENVIRONMENT_MAX_TOTAL_BYTES = 4_000_000
_ENVIRONMENT_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_SENSITIVE_ENVIRONMENT_NAME_RE = re.compile(
    r"(?:^|_)(?:API_?KEY|AUTH_?TOKEN|ACCESS_?TOKEN|TOKEN|SECRET|"
    r"PASSWORD|PASSWD|CREDENTIALS?|PRIVATE_?KEY)(?:$|_)",
    re.IGNORECASE,
)
_DEFAULT_WINDOWS_ENVIRONMENT_NAMES = (
    "COMSPEC",
    "OS",
    "PATH",
    "PATHEXT",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "WINDIR",
)
_DEFAULT_POSIX_ENVIRONMENT_NAMES = (
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "TMPDIR",
    "TZ",
)
_FIXED_PROCESS_ENVIRONMENT = {
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONHASHSEED": "0",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONNOUSERSITE": "1",
}
_INFERENCE_SERVICE_EXECUTABLE_MAX_BYTES = 2_000_000_000
_INFERENCE_SERVICE_MEMORY_METRICS = frozenset(
    {"resident-set-bytes", "working-set-bytes"}
)
_POSIX_SIGKILL = getattr(signal, "SIGKILL", 9)
_DARWIN_PROC_PIDTBSDINFO = 3
_DARWIN_PROC_PIDTASKINFO = 4
_DARWIN_PROC_PIDPATHINFO_MAXSIZE = 4_096
_PROCESS_GROUP_GRACE_SECONDS = 0.5
_WINDOWS_CREATE_SUSPENDED = 0x00000004
_WINDOWS_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_WINDOWS_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
_WINDOWS_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_WINDOWS_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_WINDOWS_TH32CS_SNAPTHREAD = 0x00000004
_WINDOWS_THREAD_SUSPEND_RESUME = 0x0002
_CORPUS_JSON_LIMITS = StrictJsonLimits(
    max_bytes=128 * 1024 * 1024,
    max_line_chars=8 * 1024 * 1024,
    max_depth=128,
)
_MANIFEST_JSON_LIMITS = StrictJsonLimits(
    max_bytes=5_000_000,
    max_line_chars=1_000_000,
    max_depth=64,
)
_DARWIN_ENVIRONMENT_HANDOFF_PROTOCOL = b"ctxc-darwin-environment-v1\x00"
_DARWIN_ENVIRONMENT_HANDOFF_MAX_BYTES = (
    len(_DARWIN_ENVIRONMENT_HANDOFF_PROTOCOL)
    + _PROCESS_ENVIRONMENT_MAX_TOTAL_BYTES
    + (2 * _PROCESS_ENVIRONMENT_MAX_VARIABLES)
)
_DARWIN_PRELIMIT_LAUNCHER_PROTOCOL = "ctxc-darwin-prelimit-v1"
_DARWIN_PRELIMIT_LAUNCHER = """\
if [ "$#" -lt 3 ] || [ "$0" != "ctxc-darwin-prelimit-v1" ] || [ "$2" != "--" ]; then
    printf '%s\n' 'invalid Darwin pre-limit launcher contract' >&2
    exit 125
fi
case "$1" in
    ''|*[!0-9]*)
        printf '%s\n' 'invalid Darwin RLIMIT_AS value' >&2
        exit 125
        ;;
esac
if ! ulimit -S -H -v "$1"; then
    printf '%s\n' 'could not apply exact Darwin RLIMIT_AS limit' >&2
    exit 125
fi
shift 2
exec "$@"
"""
_DARWIN_LIMIT_LAUNCHER_PROTOCOL = "ctxc-darwin-limit-v1"
_DARWIN_LIMIT_LAUNCHER = """\
import hashlib
import os
import resource
import stat
import sys


ENVIRONMENT_PROTOCOL = b"ctxc-darwin-environment-v1\\x00"
ENVIRONMENT_MAX_BYTES = 4_002_075
ENVIRONMENT_MAX_VARIABLES = 1_024
ENVIRONMENT_MAX_NAME_BYTES = 4_096
ENVIRONMENT_MAX_VALUE_BYTES = 1_000_000
ENVIRONMENT_MAX_TOTAL_BYTES = 4_000_000


def reject(message):
    raise SystemExit(message)


def decimal(raw, label):
    if (
        not raw
        or len(raw) > 19
        or any(character not in "0123456789" for character in raw)
    ):
        reject(f"invalid Darwin {label} value")
    value = int(raw)
    if value > 9_223_372_036_854_775_807:
        reject(f"invalid Darwin {label} value")
    return value


def require_exact(resource_name, resource_label, requested):
    observed = resource.getrlimit(resource_name)
    if observed != (requested, requested):
        raise SystemExit(
            f"inherited {resource_label} limit is not exact: "
            f"{observed[0]}/{observed[1]} != {requested}/{requested}"
        )


def bound(resource_name, resource_label, requested):
    _soft, hard = resource.getrlimit(resource_name)
    if hard != resource.RLIM_INFINITY and hard < requested:
        raise SystemExit(
            f"inherited hard {resource_label} limit {hard} "
            f"is below requested {requested}"
        )
    try:
        resource.setrlimit(resource_name, (requested, requested))
    except (OSError, OverflowError, ValueError) as exc:
        raise SystemExit(
            f"could not apply exact {resource_label} limit: {type(exc).__name__}"
        ) from exc


def consume_environment_payload(fd, expected_size, expected_sha256):
    if fd < 3 or fd > 2_147_483_647:
        reject("invalid Darwin environment handoff descriptor")
    if not len(ENVIRONMENT_PROTOCOL) <= expected_size <= ENVIRONMENT_MAX_BYTES:
        reject("invalid Darwin environment handoff size")
    if (
        len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        reject("invalid Darwin environment handoff digest")
    try:
        information = os.fstat(fd)
        if (
            not stat.S_ISREG(information.st_mode)
            or information.st_nlink != 0
            or information.st_size != expected_size
        ):
            reject("invalid Darwin environment handoff file")
        with os.fdopen(fd, "r+b", closefd=True) as stream:
            stream.seek(0)
            payload = stream.read(expected_size + 1)
            stream.seek(0)
            zero_block = b"\\x00" * 65_536
            remaining = information.st_size
            while remaining:
                chunk = zero_block[: min(remaining, len(zero_block))]
                if stream.write(chunk) != len(chunk):
                    reject("could not scrub Darwin environment handoff")
                remaining -= len(chunk)
            stream.truncate(0)
            stream.flush()
    except (OSError, OverflowError, ValueError) as exc:
        raise SystemExit(
            "could not consume Darwin environment handoff: "
            f"{type(exc).__name__}"
        ) from exc
    if len(payload) != expected_size:
        reject("Darwin environment handoff length mismatch")
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        reject("Darwin environment handoff digest mismatch")
    if not payload.startswith(ENVIRONMENT_PROTOCOL):
        reject("Darwin environment handoff protocol mismatch")
    return payload


def decode_environment(payload):
    body = payload[len(ENVIRONMENT_PROTOCOL) :]
    if not body:
        return {}
    if not body.endswith(b"\\x00"):
        reject("Darwin environment handoff is not canonical")
    records = body[:-1].split(b"\\x00")
    if not records or len(records) > ENVIRONMENT_MAX_VARIABLES:
        reject("Darwin environment handoff has invalid record count")
    environment = {}
    previous_name = None
    aggregate_bytes = 0
    first_characters = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_"
    remaining_characters = first_characters + "0123456789"
    for record in records:
        if not record or b"=" not in record:
            reject("Darwin environment handoff record is invalid")
        encoded_name, encoded_value = record.split(b"=", 1)
        if (
            not encoded_name
            or len(encoded_name) > ENVIRONMENT_MAX_NAME_BYTES
            or len(encoded_value) > ENVIRONMENT_MAX_VALUE_BYTES
        ):
            reject("Darwin environment handoff record exceeds its limit")
        aggregate_bytes += len(encoded_name) + len(encoded_value)
        if aggregate_bytes > ENVIRONMENT_MAX_TOTAL_BYTES:
            reject("Darwin environment handoff exceeds its aggregate limit")
        try:
            name = encoded_name.decode("ascii")
            value = encoded_value.decode("utf-8")
        except UnicodeError as exc:
            raise SystemExit(
                "Darwin environment handoff encoding is invalid"
            ) from exc
        if (
            name[0] not in first_characters
            or any(character not in remaining_characters for character in name[1:])
            or (previous_name is not None and name <= previous_name)
        ):
            reject("Darwin environment handoff names are not canonical")
        environment[name] = value
        previous_name = name
    return environment


if (
    len(sys.argv) < 9
    or sys.argv[1] != "ctxc-darwin-limit-v1"
    or sys.argv[7] != "--"
    or not os.path.isabs(sys.argv[8])
):
    raise SystemExit("invalid Darwin limit-launcher contract")
memory_bytes = decimal(sys.argv[2], "RLIMIT_AS")
file_bytes = decimal(sys.argv[3], "RLIMIT_FSIZE")
environment_fd = decimal(sys.argv[4], "environment handoff descriptor")
environment_size = decimal(sys.argv[5], "environment size")
require_exact(resource.RLIMIT_AS, "RLIMIT_AS", memory_bytes)
environment_payload = consume_environment_payload(
    environment_fd,
    environment_size,
    sys.argv[6],
)
bound(resource.RLIMIT_FSIZE, "RLIMIT_FSIZE", file_bytes)
adapter_environment = decode_environment(environment_payload)
os.execve(sys.argv[8], sys.argv[8:], adapter_environment)
"""


class _DarwinProcBsdInfo(ctypes.Structure):
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


class _DarwinProcTaskInfo(ctypes.Structure):
    _fields_ = [
        ("pti_virtual_size", ctypes.c_uint64),
        ("pti_resident_size", ctypes.c_uint64),
        ("pti_total_user", ctypes.c_uint64),
        ("pti_total_system", ctypes.c_uint64),
        ("pti_threads_user", ctypes.c_uint64),
        ("pti_threads_system", ctypes.c_uint64),
        ("pti_policy", ctypes.c_int32),
        ("pti_faults", ctypes.c_int32),
        ("pti_pageins", ctypes.c_int32),
        ("pti_cow_faults", ctypes.c_int32),
        ("pti_messages_sent", ctypes.c_int32),
        ("pti_messages_received", ctypes.c_int32),
        ("pti_syscalls_mach", ctypes.c_int32),
        ("pti_syscalls_unix", ctypes.c_int32),
        ("pti_csw", ctypes.c_int32),
        ("pti_threadnum", ctypes.c_int32),
        ("pti_numrunning", ctypes.c_int32),
        ("pti_priority", ctypes.c_int32),
    ]


class ExternalRunnerError(RuntimeError):
    """The adapter runner could not establish a safe execution boundary."""


@dataclass(frozen=True, slots=True)
class ExternalRunReference:
    system: str
    candidate_path: Path | None
    candidate_sha256: str | None
    candidate_bytes: int | None
    failure_reason: str | None
    manifest_sha256: str
    isolation_mode: str
    limits: RunnerLimits
    identity: RunnerIdentity
    dependency_lock: DependencyLockEvidence
    adapter_entrypoint: AdapterEntrypointEvidence
    adapter_source: AdapterSourceEvidence
    adapter_runtime: AdapterRuntimeEvidence
    process_environment: ProcessEnvironmentEvidence
    network_isolation: NetworkIsolationEvidence
    inference_service: InferenceServiceAccounting
    command_sha256: str
    adapter_revision: str
    environment_id: str
    model_id: str
    model_service_cost_usd: float


@dataclass(frozen=True, slots=True)
class RunnerIdentity:
    adapter_revision: str = "unrecorded"
    environment_id: str = "unrecorded"
    model_id: str = "unrecorded"
    model_context_length: int = 0
    tokenizer_id: str = "unrecorded"
    inference_concurrency: int = 0
    retry_count: int = 0
    model_service_cost_usd: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "adapter_revision",
            "environment_id",
            "model_id",
            "tokenizer_id",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise TypeError(f"{name} must be a non-empty string")
        for name in ("model_context_length", "inference_concurrency", "retry_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
        cost = self.model_service_cost_usd
        if isinstance(cost, bool) or not isinstance(cost, (int, float)):
            raise TypeError("model_service_cost_usd must be numeric")
        if not 0 <= float(cost) < float("inf"):
            raise ValueError("model_service_cost_usd must be finite and non-negative")

    @property
    def claim_metadata_complete(self) -> bool:
        return (
            _REVISION_RE.fullmatch(self.adapter_revision) is not None
            and _ENVIRONMENT_ID_RE.fullmatch(self.environment_id) is not None
            and self.model_id == QWEN_Q4_VARIANT
            and self.model_context_length == QWEN_Q4_CONTEXT_LENGTH
            and self.tokenizer_id == TOKENIZER_ID
            and self.inference_concurrency == 1
            and self.retry_count == 0
            and self.model_service_cost_usd == 0.0
        )

    def to_candidate_producer(self) -> CandidateProducerMetadata:
        return CandidateProducerMetadata(
            adapter_revision=self.adapter_revision,
            environment_id=self.environment_id,
            model_id=self.model_id,
            model_context_length=self.model_context_length,
            tokenizer_id=self.tokenizer_id,
            inference_concurrency=self.inference_concurrency,
            retry_count=self.retry_count,
            model_service_cost_usd=self.model_service_cost_usd,
        )


@dataclass(frozen=True, slots=True)
class RunnerLimits:
    timeout_seconds: float = 300.0
    max_stdout_bytes: int = 1_000_000
    max_stderr_bytes: int = 1_000_000
    max_candidate_bytes: int = 20_000_000
    max_memory_mb: int | None = None
    poll_interval_seconds: float = 0.02

    def __post_init__(self) -> None:
        for name in (
            "max_stdout_bytes",
            "max_stderr_bytes",
            "max_candidate_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.max_memory_mb is not None:
            if isinstance(self.max_memory_mb, bool) or not isinstance(self.max_memory_mb, int):
                raise TypeError("max_memory_mb must be an integer or None")
            if self.max_memory_mb <= 0:
                raise ValueError("max_memory_mb must be positive")
        for name in ("timeout_seconds", "poll_interval_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be numeric")
            if not 0 < float(value) < float("inf"):
                raise ValueError(f"{name} must be positive and finite")


def _memory_limit_attested(
    limits: RunnerLimits,
    *,
    process_succeeded: bool,
) -> bool:
    """Report only memory limits whose platform-specific setup is attested."""

    return limits.max_memory_mb is not None and (
        sys.platform != "darwin" or process_succeeded
    )


@dataclass(frozen=True, slots=True)
class DependencyLockEvidence:
    """Retained bytes that define the manifest's dependency environment."""

    evidence_path: str | None = None
    evidence_sha256: str | None = None
    evidence_bytes: int | None = None

    def __post_init__(self) -> None:
        fields = (
            self.evidence_path,
            self.evidence_sha256,
            self.evidence_bytes,
        )
        if all(value is None for value in fields):
            return
        if (
            not isinstance(self.evidence_path, str)
            or not self.evidence_path
            or not _is_portable_absolute_path(self.evidence_path)
        ):
            raise ValueError(
                "dependency-lock evidence requires an absolute path"
            )
        if not _is_sha256(self.evidence_sha256):
            raise ValueError(
                "dependency-lock evidence requires a SHA-256 digest"
            )
        if (
            isinstance(self.evidence_bytes, bool)
            or not isinstance(self.evidence_bytes, int)
            or self.evidence_bytes <= 0
        ):
            raise ValueError(
                "dependency-lock evidence requires a positive byte count"
            )

    @property
    def claim_evidence_complete(self) -> bool:
        return self.evidence_path is not None


@dataclass(frozen=True, slots=True)
class AdapterEntrypointEvidence:
    """Retained bytes for the adapter file referenced by the command."""

    entrypoint_path: str | None = None
    entrypoint_sha256: str | None = None
    entrypoint_bytes: int | None = None

    def __post_init__(self) -> None:
        fields = (
            self.entrypoint_path,
            self.entrypoint_sha256,
            self.entrypoint_bytes,
        )
        if all(value is None for value in fields):
            return
        if (
            not isinstance(self.entrypoint_path, str)
            or not self.entrypoint_path
            or not _is_portable_absolute_path(self.entrypoint_path)
        ):
            raise ValueError(
                "adapter entrypoint evidence requires an absolute path"
            )
        if not _is_sha256(self.entrypoint_sha256):
            raise ValueError(
                "adapter entrypoint evidence requires a SHA-256 digest"
            )
        if (
            isinstance(self.entrypoint_bytes, bool)
            or not isinstance(self.entrypoint_bytes, int)
            or self.entrypoint_bytes <= 0
        ):
            raise ValueError(
                "adapter entrypoint evidence requires a positive byte count"
            )

    @property
    def claim_evidence_complete(self) -> bool:
        return self.entrypoint_path is not None


@dataclass(frozen=True, slots=True)
class AdapterSourceFileEvidence:
    """One immutable regular file below the retained adapter source root."""

    relative_path: str
    file_sha256: str
    file_bytes: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.relative_path, str)
            or not self.relative_path
            or "\\" in self.relative_path
        ):
            raise ValueError(
                "adapter source relative paths must be non-empty POSIX paths"
            )
        if (
            len(self.relative_path.encode("utf-8"))
            > _ADAPTER_SOURCE_MAX_RELATIVE_PATH_BYTES
        ):
            raise ValueError(
                "adapter source relative path exceeds the byte limit"
            )
        path = PurePosixPath(self.relative_path)
        if (
            path.is_absolute()
            or path.as_posix() != self.relative_path
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError(
                "adapter source relative paths must be normalized and confined"
            )
        if not _is_sha256(self.file_sha256):
            raise ValueError(
                "adapter source files require a SHA-256 digest"
            )
        if (
            isinstance(self.file_bytes, bool)
            or not isinstance(self.file_bytes, int)
            or self.file_bytes < 0
        ):
            raise ValueError(
                "adapter source files require a non-negative byte count"
            )


@dataclass(frozen=True, slots=True)
class AdapterSourceEvidence:
    """Bounded recursive file inventory for one immutable adapter source root."""

    tree_algorithm: str = _ADAPTER_SOURCE_TREE_ALGORITHM
    source_root: str | None = None
    tree_sha256: str | None = None
    file_count: int | None = None
    total_bytes: int | None = None
    files: tuple[AdapterSourceFileEvidence, ...] = ()

    def __post_init__(self) -> None:
        if self.tree_algorithm != _ADAPTER_SOURCE_TREE_ALGORITHM:
            raise ValueError("adapter source tree algorithm is invalid")
        scalar_fields = (
            self.source_root,
            self.tree_sha256,
            self.file_count,
            self.total_bytes,
        )
        if all(value is None for value in scalar_fields) and not self.files:
            return
        if (
            not isinstance(self.source_root, str)
            or not self.source_root
            or not _is_portable_absolute_path(self.source_root)
        ):
            raise ValueError(
                "adapter source evidence requires an absolute root"
            )
        if not _is_sha256(self.tree_sha256):
            raise ValueError(
                "adapter source evidence requires a SHA-256 tree digest"
            )
        if (
            isinstance(self.file_count, bool)
            or not isinstance(self.file_count, int)
            or self.file_count <= 0
            or self.file_count > _ADAPTER_SOURCE_MAX_FILES
        ):
            raise ValueError(
                "adapter source evidence has an invalid file count"
            )
        if (
            isinstance(self.total_bytes, bool)
            or not isinstance(self.total_bytes, int)
            or self.total_bytes < 0
            or self.total_bytes > _ADAPTER_SOURCE_MAX_TOTAL_BYTES
        ):
            raise ValueError(
                "adapter source evidence has an invalid total byte count"
            )
        if (
            not isinstance(self.files, tuple)
            or not all(
                isinstance(item, AdapterSourceFileEvidence)
                for item in self.files
            )
        ):
            raise ValueError(
                "adapter source evidence files must be immutable records"
            )
        if (
            len(self.files) != self.file_count
            or tuple(sorted(self.files, key=lambda item: item.relative_path))
            != self.files
            or len({item.relative_path for item in self.files})
            != len(self.files)
            or sum(item.file_bytes for item in self.files)
            != self.total_bytes
            or sum(
                len(item.relative_path.encode("utf-8"))
                for item in self.files
            )
            > _ADAPTER_SOURCE_MAX_PATH_BYTES
        ):
            raise ValueError(
                "adapter source evidence file inventory is inconsistent"
            )
        if _adapter_source_tree_sha256(self.files) != self.tree_sha256:
            raise ValueError(
                "adapter source evidence tree digest is inconsistent"
            )

    @property
    def claim_evidence_complete(self) -> bool:
        return self.source_root is not None


@dataclass(frozen=True, slots=True)
class AdapterRuntimeEvidence:
    """Exact executable bytes used to launch the adapter command."""

    executable_path: str | None = None
    executable_sha256: str | None = None
    executable_bytes: int | None = None

    def __post_init__(self) -> None:
        fields = (
            self.executable_path,
            self.executable_sha256,
            self.executable_bytes,
        )
        if all(value is None for value in fields):
            return
        if (
            not isinstance(self.executable_path, str)
            or not self.executable_path
            or not _is_portable_absolute_path(self.executable_path)
        ):
            raise ValueError(
                "adapter runtime evidence requires an absolute executable path"
            )
        if not _is_sha256(self.executable_sha256):
            raise ValueError(
                "adapter runtime evidence requires a SHA-256 digest"
            )
        if (
            isinstance(self.executable_bytes, bool)
            or not isinstance(self.executable_bytes, int)
            or self.executable_bytes <= 0
        ):
            raise ValueError(
                "adapter runtime evidence requires a positive byte count"
            )

    @property
    def claim_evidence_complete(self) -> bool:
        return self.executable_path is not None


@dataclass(frozen=True, slots=True)
class ProcessEnvironmentEvidence:
    """Value-redacted digest of the exact environment passed to the adapter."""

    algorithm: str = _PROCESS_ENVIRONMENT_ALGORITHM
    platform: str | None = None
    variable_names: tuple[str, ...] = ()
    environment_sha256: str | None = None
    encoded_bytes: int | None = None

    def __post_init__(self) -> None:
        if self.algorithm != _PROCESS_ENVIRONMENT_ALGORITHM:
            raise ValueError("process environment algorithm is invalid")
        fields = (
            self.platform,
            self.environment_sha256,
            self.encoded_bytes,
        )
        if all(value is None for value in fields) and not self.variable_names:
            return
        if self.platform not in {"posix", "windows"}:
            raise ValueError("process environment platform is invalid")
        if (
            not isinstance(self.variable_names, tuple)
            or len(self.variable_names)
            > _PROCESS_ENVIRONMENT_MAX_VARIABLES
        ):
            raise ValueError(
                "process environment variable names are invalid"
            )
        normalized_names: set[str] = set()
        previous_sort_key: tuple[str, str] | None = None
        for name in self.variable_names:
            try:
                _validate_environment_name(name)
            except ExternalRunnerError as exc:
                raise ValueError(
                    "process environment variable names are invalid"
                ) from exc
            normalized_name = (
                name.casefold() if self.platform == "windows" else name
            )
            if normalized_name in normalized_names:
                raise ValueError(
                    "process environment variable names are ambiguous"
                )
            normalized_names.add(normalized_name)
            sort_key = (
                normalized_name,
                name,
            )
            if (
                previous_sort_key is not None
                and sort_key <= previous_sort_key
            ):
                raise ValueError(
                    "process environment variable names are not canonical"
                )
            previous_sort_key = sort_key
        if not _is_sha256(self.environment_sha256):
            raise ValueError(
                "process environment evidence requires a SHA-256 digest"
            )
        if (
            isinstance(self.encoded_bytes, bool)
            or not isinstance(self.encoded_bytes, int)
            or self.encoded_bytes < 0
            or self.encoded_bytes > _PROCESS_ENVIRONMENT_MAX_TOTAL_BYTES
        ):
            raise ValueError(
                "process environment evidence has an invalid byte count"
            )

    @property
    def claim_evidence_complete(self) -> bool:
        return (
            self.environment_sha256 is not None
            and not any(
                _SENSITIVE_ENVIRONMENT_NAME_RE.search(name)
                for name in self.variable_names
            )
        )


def _validate_environment_name(name: object) -> str:
    if (
        not isinstance(name, str)
        or not name
        or _ENVIRONMENT_NAME_RE.fullmatch(name) is None
    ):
        raise ExternalRunnerError(
            "environment variable names must be portable ASCII identifiers"
        )
    encoded_name = name.encode("ascii")
    if len(encoded_name) > _PROCESS_ENVIRONMENT_MAX_NAME_BYTES:
        raise ExternalRunnerError(
            "environment variable name exceeds the byte limit"
        )
    return name


def _environment_platform() -> str:
    return "windows" if os.name == "nt" else "posix"


def _environment_sort_key(
    name: str,
    *,
    platform_name: str,
) -> tuple[str, str]:
    return (
        name.casefold() if platform_name == "windows" else name,
        name,
    )


def _default_process_environment() -> dict[str, str]:
    names = (
        _DEFAULT_WINDOWS_ENVIRONMENT_NAMES
        if os.name == "nt"
        else _DEFAULT_POSIX_ENVIRONMENT_NAMES
    )
    environment = {
        name: os.environ[name]
        for name in names
        if name in os.environ
    }
    environment.update(_FIXED_PROCESS_ENVIRONMENT)
    return environment


def _prepare_process_environment(
    environment: Mapping[str, str] | None,
) -> tuple[dict[str, str], ProcessEnvironmentEvidence]:
    source = (
        _default_process_environment()
        if environment is None
        else dict(environment)
    )
    if len(source) > _PROCESS_ENVIRONMENT_MAX_VARIABLES:
        raise ExternalRunnerError(
            "environment exceeds the variable-count limit"
        )
    platform_name = _environment_platform()
    normalized_names: set[str] = set()
    entries: list[dict[str, str]] = []
    encoded_bytes = 0
    process_environment: dict[str, str] = {}
    for name, value in sorted(
        source.items(),
        key=lambda item: _environment_sort_key(
            item[0],
            platform_name=platform_name,
        )
        if isinstance(item[0], str)
        else ("", ""),
    ):
        _validate_environment_name(name)
        if not isinstance(value, str) or "\x00" in value:
            raise ExternalRunnerError(
                "environment values must be strings without NUL"
            )
        canonical_name = (
            name.upper() if platform_name == "windows" else name
        )
        normalized_name = (
            canonical_name.casefold()
            if platform_name == "windows"
            else canonical_name
        )
        if normalized_name in normalized_names:
            raise ExternalRunnerError(
                "environment contains ambiguous variable names"
            )
        normalized_names.add(normalized_name)
        try:
            encoded_name = canonical_name.encode("utf-8")
            encoded_value = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ExternalRunnerError(
                "environment names and values must be valid Unicode"
            ) from exc
        if len(encoded_value) > _PROCESS_ENVIRONMENT_MAX_VALUE_BYTES:
            raise ExternalRunnerError(
                f"environment variable {name!r} exceeds the value byte limit"
            )
        encoded_bytes += len(encoded_name) + len(encoded_value)
        if encoded_bytes > _PROCESS_ENVIRONMENT_MAX_TOTAL_BYTES:
            raise ExternalRunnerError(
                "environment exceeds the aggregate byte limit"
            )
        process_environment[canonical_name] = value
        entries.append(
            {
                "name": canonical_name,
                "value_sha256": hashlib.sha256(
                    encoded_value
                ).hexdigest(),
            }
        )
    evidence_document = {
        "algorithm": _PROCESS_ENVIRONMENT_ALGORITHM,
        "platform": platform_name,
        "variables": entries,
    }
    evidence = ProcessEnvironmentEvidence(
        platform=platform_name,
        variable_names=tuple(process_environment),
        environment_sha256=_canonical_sha256(evidence_document),
        encoded_bytes=encoded_bytes,
    )
    return process_environment, evidence


def _encode_darwin_process_environment(
    environment: Mapping[str, str],
) -> bytes:
    normalized_environment, _evidence = _prepare_process_environment(environment)
    if tuple(normalized_environment.items()) != tuple(environment.items()):
        raise ExternalRunnerError("Darwin adapter environment handoff must be canonical")
    payload = bytearray(_DARWIN_ENVIRONMENT_HANDOFF_PROTOCOL)
    for name, value in normalized_environment.items():
        payload.extend(name.encode("ascii"))
        payload.extend(b"=")
        payload.extend(value.encode("utf-8"))
        payload.extend(b"\x00")
    if len(payload) > _DARWIN_ENVIRONMENT_HANDOFF_MAX_BYTES:
        raise ExternalRunnerError("Darwin adapter environment handoff exceeds its byte limit")
    return bytes(payload)


def _open_darwin_environment_handoff(directory: Path) -> BinaryIO:
    try:
        return tempfile.TemporaryFile(
            mode="w+b",
            prefix=".lrcbench-env-",
            dir=directory,
        )
    except OSError as exc:
        raise ExternalRunnerError("could not create the Darwin environment handoff") from exc


@contextmanager
def _darwin_environment_handoff(
    environment: Mapping[str, str],
    *,
    directory: Path,
) -> Iterator[tuple[int, int, str]]:
    payload = _encode_darwin_process_environment(environment)
    with _open_darwin_environment_handoff(directory) as stream:
        try:
            if stream.write(payload) != len(payload):
                raise ExternalRunnerError("could not write the exact Darwin environment handoff")
            stream.flush()
            descriptor = stream.fileno()
            information = os.fstat(descriptor)
            if (
                descriptor < 3
                or not stat.S_ISREG(information.st_mode)
                or information.st_nlink != 0
                or information.st_size != len(payload)
            ):
                raise ExternalRunnerError(
                    "Darwin environment handoff is not an exact anonymous file"
                )
            stream.seek(0)
        except ExternalRunnerError:
            raise
        except OSError as exc:
            raise ExternalRunnerError("could not prepare the Darwin environment handoff") from exc
        yield (
            descriptor,
            len(payload),
            hashlib.sha256(payload).hexdigest(),
        )


def capture_process_environment_evidence(
    environment: Mapping[str, str] | None = None,
) -> ProcessEnvironmentEvidence:
    """Hash an exact bounded adapter environment without retaining values."""

    return _prepare_process_environment(environment)[1]


def _process_environment_with_pass_through(
    variable_names: Sequence[str],
) -> dict[str, str]:
    environment = _default_process_environment()
    for raw_name in variable_names:
        name = _validate_environment_name(raw_name)
        if name not in os.environ:
            raise ExternalRunnerError(
                f"requested environment variable is not set: {name}"
            )
        if os.name == "nt":
            existing_name = next(
                (
                    candidate
                    for candidate in environment
                    if candidate.casefold() == name.casefold()
                ),
                None,
            )
        else:
            existing_name = name if name in environment else None
        environment[existing_name or name] = os.environ[name]
    return environment


@dataclass(frozen=True, slots=True)
class NetworkIsolationEvidence:
    """Retained host/container evidence for an externally enforced offline run."""

    mode: str = "unverified"
    evidence_path: str | None = None
    evidence_sha256: str | None = None
    evidence_bytes: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, str) or self.mode not in NETWORK_ISOLATION_MODES:
            raise ValueError("network isolation mode is invalid")
        fields = (
            self.evidence_path,
            self.evidence_sha256,
            self.evidence_bytes,
        )
        if self.mode == "unverified":
            if any(value is not None for value in fields):
                raise ValueError(
                    "unverified network isolation cannot carry evidence"
                )
            return
        if (
            not isinstance(self.evidence_path, str)
            or not self.evidence_path
            or not _is_portable_absolute_path(self.evidence_path)
        ):
            raise ValueError(
                "evidenced network isolation requires an absolute evidence path"
            )
        if not _is_sha256(self.evidence_sha256):
            raise ValueError(
                "evidenced network isolation requires a SHA-256 evidence digest"
            )
        if (
            isinstance(self.evidence_bytes, bool)
            or not isinstance(self.evidence_bytes, int)
            or self.evidence_bytes <= 0
        ):
            raise ValueError(
                "evidenced network isolation requires a positive evidence byte count"
            )

    @property
    def claim_evidence_complete(self) -> bool:
        return self.mode in CLAIM_NETWORK_ISOLATION_MODES


@dataclass(frozen=True, slots=True)
class InferenceServiceContract:
    """Stable identity and memory ceiling for one pre-existing model service."""

    process_id: int | None = None
    process_start_token: str | None = None
    executable_path: str | None = None
    executable_sha256: str | None = None
    executable_bytes: int | None = None
    memory_metric: str | None = None
    max_memory_mb: int | None = None

    def __post_init__(self) -> None:
        values = tuple(getattr(self, name) for name in self.__dataclass_fields__)
        if all(value is None for value in values):
            return
        if any(value is None for value in values):
            raise ValueError("inference-service contract must be complete or disabled")
        if (
            isinstance(self.process_id, bool)
            or not isinstance(self.process_id, int)
            or self.process_id <= 0
        ):
            raise ValueError("inference-service process_id must be positive")
        if (
            not isinstance(self.process_start_token, str)
            or not self.process_start_token
            or len(self.process_start_token) > 256
        ):
            raise ValueError("inference-service process start token is invalid")
        if (
            not isinstance(self.executable_path, str)
            or not self.executable_path
            or not _is_portable_absolute_path(self.executable_path)
        ):
            raise ValueError(
                "inference-service executable requires an absolute path"
            )
        if not _is_sha256(self.executable_sha256):
            raise ValueError(
                "inference-service executable requires a SHA-256 digest"
            )
        if (
            isinstance(self.executable_bytes, bool)
            or not isinstance(self.executable_bytes, int)
            or self.executable_bytes <= 0
        ):
            raise ValueError(
                "inference-service executable requires a positive byte count"
            )
        if self.memory_metric not in _INFERENCE_SERVICE_MEMORY_METRICS:
            raise ValueError("inference-service memory metric is invalid")
        if (
            isinstance(self.max_memory_mb, bool)
            or not isinstance(self.max_memory_mb, int)
            or self.max_memory_mb <= 0
        ):
            raise ValueError("inference-service memory ceiling must be positive")

    @property
    def enabled(self) -> bool:
        return self.process_id is not None


@dataclass(frozen=True, slots=True)
class InferenceServiceAccounting:
    """Historical samples for one stable pre-existing model-service process."""

    process_id: int | None = None
    process_start_token: str | None = None
    executable_path: str | None = None
    executable_sha256: str | None = None
    executable_bytes: int | None = None
    memory_metric: str | None = None
    max_memory_mb: int | None = None
    sample_count: int = 0
    peak_memory_bytes: int | None = None

    def __post_init__(self) -> None:
        contract_values = (
            self.process_id,
            self.process_start_token,
            self.executable_path,
            self.executable_sha256,
            self.executable_bytes,
            self.memory_metric,
            self.max_memory_mb,
        )
        if all(value is None for value in contract_values):
            if self.sample_count != 0 or self.peak_memory_bytes is not None:
                raise ValueError(
                    "disabled inference-service accounting cannot carry samples"
                )
            return
        InferenceServiceContract(*contract_values)
        if (
            isinstance(self.sample_count, bool)
            or not isinstance(self.sample_count, int)
            or self.sample_count <= 0
        ):
            raise ValueError(
                "inference-service accounting requires a positive sample count"
            )
        if (
            isinstance(self.peak_memory_bytes, bool)
            or not isinstance(self.peak_memory_bytes, int)
            or self.peak_memory_bytes <= 0
        ):
            raise ValueError(
                "inference-service accounting requires positive peak memory"
            )

    @property
    def claim_evidence_complete(self) -> bool:
        return (
            self.process_id is not None
            and self.sample_count >= 2
            and self.peak_memory_bytes is not None
            and self.max_memory_mb is not None
            and self.peak_memory_bytes <= self.max_memory_mb * 1024 * 1024
        )


@dataclass(frozen=True, slots=True)
class _InferenceProcessSnapshot:
    process_id: int
    process_start_token: str
    executable_path: str
    memory_metric: str
    memory_bytes: int


@dataclass(frozen=True, slots=True)
class CaseRunRecord:
    case_id: str
    command: tuple[str, ...]
    corpus_path: str
    candidate_path: str
    started_at: str
    duration_seconds: float
    corpus_sha256: str
    corpus_file_sha256: str
    candidate_sha256: str | None
    candidate_payload_sha256: str | None
    candidate_bytes: int | None
    exit_code: int | None
    termination_reason: str | None
    process_succeeded: bool
    candidate_valid: bool
    validation_error: str | None
    stdout_bytes: int
    stdout_sha256: str
    stderr_bytes: int
    stderr_sha256: str


@dataclass(frozen=True, slots=True)
class ExternalRunManifest:
    system: str
    isolation_mode: str
    case_count: int
    case_runs: tuple[CaseRunRecord, ...]
    command: tuple[str, ...]
    command_contract: tuple[str, ...]
    command_sha256: str
    working_directory: str
    started_at: str
    duration_seconds: float
    corpus_path: str
    corpus_schema: str
    corpus_sha256: str
    corpus_file_sha256: str
    dataset_sha256: str
    candidate_path: str
    candidate_schema: str
    candidate_sha256: str | None
    candidate_bytes: int | None
    exit_code: int | None
    termination_reason: str | None
    process_succeeded: bool
    candidate_valid: bool
    ready_for_scoring: bool
    validation_error: str | None
    stdout_bytes: int
    stdout_sha256: str
    stderr_bytes: int
    stderr_sha256: str
    limits: RunnerLimits
    identity: RunnerIdentity
    dependency_lock: DependencyLockEvidence
    adapter_entrypoint: AdapterEntrypointEvidence
    adapter_source: AdapterSourceEvidence
    adapter_runtime: AdapterRuntimeEvidence
    process_environment: ProcessEnvironmentEvidence
    network_isolation: NetworkIsolationEvidence
    inference_service: InferenceServiceAccounting
    claim_metadata_complete: bool
    memory_limit_enforced: bool
    python_version: str
    platform: str

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": RUNNER_MANIFEST_SCHEMA,
            **asdict(self),
        }
        payload["manifest_sha256"] = _canonical_sha256(payload)
        return payload

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _case_stream_sha256(
    case_runs: Sequence[CaseRunRecord | Mapping[str, object]],
    stream: str,
) -> str:
    records: list[dict[str, object]] = []
    for case_run in case_runs:
        if isinstance(case_run, Mapping):
            case_id = case_run["case_id"]
            byte_count = case_run[f"{stream}_bytes"]
            digest = case_run[f"{stream}_sha256"]
        else:
            case_id = case_run.case_id
            byte_count = getattr(case_run, f"{stream}_bytes")
            digest = getattr(case_run, f"{stream}_sha256")
        records.append(
            {
                "case_id": case_id,
                "bytes": byte_count,
                "sha256": digest,
            }
        )
    return _canonical_sha256(records)


def _stream_size(stream: BinaryIO, *, label: str) -> int:
    try:
        byte_count = int(os.fstat(stream.fileno()).st_size)
    except (OSError, ValueError) as exc:
        raise ExternalRunnerError(
            f"could not inspect the retained {label} descriptor"
        ) from exc
    if byte_count < 0:
        raise ExternalRunnerError(
            f"retained {label} descriptor has a negative byte count"
        )
    return byte_count


def _bounded_stream_snapshot(
    stream: BinaryIO,
    *,
    max_bytes: int,
    label: str,
) -> tuple[int, int, str]:
    """Hash one finite descriptor prefix fixed by a single size observation."""

    observed_bytes = _stream_size(stream, label=label)
    target_bytes = min(observed_bytes, max_bytes + 1)
    digest = hashlib.sha256()
    captured_bytes = 0
    try:
        stream.seek(0)
        while captured_bytes < target_bytes:
            requested_bytes = min(
                1024 * 1024,
                target_bytes - captured_bytes,
            )
            block = stream.read(requested_bytes)
            if not block:
                break
            if not isinstance(block, bytes) or len(block) > requested_bytes:
                raise ExternalRunnerError(
                    f"retained {label} descriptor returned invalid bytes"
                )
            digest.update(block)
            captured_bytes += len(block)
    except ExternalRunnerError:
        raise
    except (OSError, ValueError) as exc:
        raise ExternalRunnerError(
            f"could not read the retained {label} descriptor"
        ) from exc
    return observed_bytes, captured_bytes, digest.hexdigest()


def _bounded_file_evidence(
    path: Path,
    *,
    max_bytes: int,
    label: str,
) -> StrictFileEvidence:
    try:
        return hash_bounded_regular_file(
            path,
            max_bytes=max_bytes,
            label=label,
        )
    except StrictJsonError as exc:
        raise ExternalRunnerError(str(exc)) from exc


def _bounded_file_sha256(path: Path, *, max_bytes: int, label: str) -> str:
    return _bounded_file_evidence(
        path,
        max_bytes=max_bytes,
        label=label,
    ).file_sha256


def _bounded_file_matches(
    path: Path,
    expected_sha256: str,
    *,
    max_bytes: int,
    label: str,
) -> bool:
    try:
        evidence = hash_bounded_regular_file(
            path,
            max_bytes=max_bytes,
            label=label,
        )
    except StrictJsonError:
        return False
    return evidence.file_sha256 == expected_sha256


def capture_dependency_lock_evidence(
    path: Path | str | None = None,
) -> DependencyLockEvidence:
    """Hash the retained dependency lock that defines one claim environment."""

    if path is None:
        return DependencyLockEvidence()
    resolved = Path(path).expanduser().resolve()
    evidence = _bounded_file_evidence(
        resolved,
        max_bytes=_DEPENDENCY_LOCK_EVIDENCE_MAX_BYTES,
        label="dependency-lock evidence",
    )
    if evidence.byte_count <= 0:
        raise ExternalRunnerError("dependency-lock evidence cannot be empty")
    return DependencyLockEvidence(
        evidence_path=str(resolved),
        evidence_sha256=evidence.file_sha256,
        evidence_bytes=evidence.byte_count,
    )


def _dependency_lock_evidence_matches(
    evidence: DependencyLockEvidence,
) -> bool:
    if not evidence.claim_evidence_complete:
        return True
    evidence_path = _native_absolute_path(evidence.evidence_path)
    if evidence_path is None:
        return False
    try:
        observed = _bounded_file_evidence(
            evidence_path,
            max_bytes=_DEPENDENCY_LOCK_EVIDENCE_MAX_BYTES,
            label="dependency-lock evidence",
        )
    except ExternalRunnerError:
        return False
    return (
        observed.file_sha256 == evidence.evidence_sha256
        and observed.byte_count == evidence.evidence_bytes
    )


def capture_adapter_entrypoint_evidence(
    path: Path | str | None = None,
) -> AdapterEntrypointEvidence:
    """Hash the retained adapter file that its command must reference."""

    if path is None:
        return AdapterEntrypointEvidence()
    resolved = Path(path).expanduser().resolve()
    evidence = _bounded_file_evidence(
        resolved,
        max_bytes=_ADAPTER_ENTRYPOINT_EVIDENCE_MAX_BYTES,
        label="adapter entrypoint evidence",
    )
    if evidence.byte_count <= 0:
        raise ExternalRunnerError(
            "adapter entrypoint evidence cannot be empty"
        )
    return AdapterEntrypointEvidence(
        entrypoint_path=str(resolved),
        entrypoint_sha256=evidence.file_sha256,
        entrypoint_bytes=evidence.byte_count,
    )


def _adapter_entrypoint_evidence_matches(
    evidence: AdapterEntrypointEvidence,
) -> bool:
    if not evidence.claim_evidence_complete:
        return True
    entrypoint_path = _native_absolute_path(evidence.entrypoint_path)
    if entrypoint_path is None:
        return False
    try:
        observed = _bounded_file_evidence(
            entrypoint_path,
            max_bytes=_ADAPTER_ENTRYPOINT_EVIDENCE_MAX_BYTES,
            label="adapter entrypoint evidence",
        )
    except ExternalRunnerError:
        return False
    return (
        observed.file_sha256 == evidence.entrypoint_sha256
        and observed.byte_count == evidence.entrypoint_bytes
    )


def _adapter_source_tree_sha256(
    files: Sequence[AdapterSourceFileEvidence],
) -> str:
    return _canonical_sha256(
        {
            "algorithm": _ADAPTER_SOURCE_TREE_ALGORITHM,
            "files": [asdict(item) for item in files],
        }
    )


def _adapter_source_files(
    source_root: Path,
) -> tuple[AdapterSourceFileEvidence, ...]:
    if not source_root.is_dir():
        raise ExternalRunnerError(
            f"adapter source root is not a directory: {source_root}"
        )
    pending = [source_root]
    records: list[AdapterSourceFileEvidence] = []
    total_bytes = 0
    total_path_bytes = 0
    while pending:
        directory = pending.pop()
        try:
            directory_stat = directory.lstat()
        except OSError as exc:
            raise ExternalRunnerError(
                f"adapter source directory cannot be inspected: {directory}"
            ) from exc
        if (
            not stat.S_ISDIR(directory_stat.st_mode)
            or directory.is_symlink()
            or (
                hasattr(os.path, "isjunction")
                and os.path.isjunction(directory)
            )
        ):
            raise ExternalRunnerError(
                "adapter source directory changed into a link or non-directory: "
                f"{directory}"
            )
        try:
            entries = sorted(
                os.scandir(directory),
                key=lambda item: item.name,
            )
        except OSError as exc:
            raise ExternalRunnerError(
                f"adapter source directory cannot be read: {directory}: {exc}"
            ) from exc
        for entry in entries:
            entry_path = Path(entry.path)
            try:
                if entry.is_symlink() or (
                    hasattr(os.path, "isjunction")
                    and os.path.isjunction(entry_path)
                ):
                    raise ExternalRunnerError(
                        "adapter source trees cannot contain links or junctions: "
                        f"{entry_path}"
                    )
                if entry.is_dir(follow_symlinks=False):
                    pending.append(entry_path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    raise ExternalRunnerError(
                        "adapter source trees can contain only regular files "
                        f"and directories: {entry_path}"
                    )
            except OSError as exc:
                raise ExternalRunnerError(
                    f"adapter source entry cannot be inspected: {entry_path}: {exc}"
                ) from exc
            evidence = _bounded_file_evidence(
                entry_path,
                max_bytes=_ADAPTER_SOURCE_MAX_FILE_BYTES,
                label="adapter source file",
            )
            total_bytes += evidence.byte_count
            if total_bytes > _ADAPTER_SOURCE_MAX_TOTAL_BYTES:
                raise ExternalRunnerError(
                    "adapter source tree exceeds the aggregate byte limit"
                )
            relative_path = entry_path.relative_to(source_root).as_posix()
            total_path_bytes += len(relative_path.encode("utf-8"))
            if total_path_bytes > _ADAPTER_SOURCE_MAX_PATH_BYTES:
                raise ExternalRunnerError(
                    "adapter source tree exceeds the path-byte limit"
                )
            records.append(
                AdapterSourceFileEvidence(
                    relative_path=relative_path,
                    file_sha256=evidence.file_sha256,
                    file_bytes=evidence.byte_count,
                )
            )
            if len(records) > _ADAPTER_SOURCE_MAX_FILES:
                raise ExternalRunnerError(
                    "adapter source tree exceeds the file-count limit"
                )
    if not records:
        raise ExternalRunnerError("adapter source tree cannot be empty")
    return tuple(sorted(records, key=lambda item: item.relative_path))


def capture_adapter_source_evidence(
    source_root: Path | str | None = None,
) -> AdapterSourceEvidence:
    """Inventory every regular file below one retained adapter source root."""

    if source_root is None:
        return AdapterSourceEvidence()
    resolved = Path(source_root).expanduser().resolve()
    files = _adapter_source_files(resolved)
    return AdapterSourceEvidence(
        source_root=str(resolved),
        tree_sha256=_adapter_source_tree_sha256(files),
        file_count=len(files),
        total_bytes=sum(item.file_bytes for item in files),
        files=files,
    )


def _adapter_source_evidence_matches(
    evidence: AdapterSourceEvidence,
) -> bool:
    if not evidence.claim_evidence_complete:
        return True
    source_root = _native_absolute_path(evidence.source_root)
    if source_root is None:
        return False
    try:
        observed = capture_adapter_source_evidence(source_root)
    except (ExternalRunnerError, ValueError):
        return False
    return observed == evidence


def _adapter_source_covers_entrypoint(
    source: AdapterSourceEvidence,
    entrypoint: AdapterEntrypointEvidence,
) -> bool:
    if (
        not source.claim_evidence_complete
        and not entrypoint.claim_evidence_complete
    ):
        return True
    if (
        not source.claim_evidence_complete
        or not entrypoint.claim_evidence_complete
    ):
        return False
    source_root = _native_absolute_path(source.source_root)
    entrypoint_path = _native_absolute_path(entrypoint.entrypoint_path)
    if source_root is None or entrypoint_path is None:
        return False
    try:
        relative_path = entrypoint_path.relative_to(source_root).as_posix()
    except ValueError:
        return False
    return any(
        item.relative_path == relative_path
        and item.file_sha256 == entrypoint.entrypoint_sha256
        and item.file_bytes == entrypoint.entrypoint_bytes
        for item in source.files
    )


def capture_adapter_runtime_evidence(
    executable_path: Path | str | None = None,
) -> AdapterRuntimeEvidence:
    """Hash the exact executable used as command argument zero."""

    if executable_path is None:
        return AdapterRuntimeEvidence()
    resolved = Path(executable_path).expanduser().resolve()
    evidence = _bounded_file_evidence(
        resolved,
        max_bytes=_ADAPTER_RUNTIME_EXECUTABLE_MAX_BYTES,
        label="adapter runtime executable",
    )
    if evidence.byte_count <= 0:
        raise ExternalRunnerError(
            "adapter runtime executable cannot be empty"
        )
    return AdapterRuntimeEvidence(
        executable_path=str(resolved),
        executable_sha256=evidence.file_sha256,
        executable_bytes=evidence.byte_count,
    )


def _adapter_runtime_evidence_matches(
    evidence: AdapterRuntimeEvidence,
) -> bool:
    if not evidence.claim_evidence_complete:
        return True
    executable_path = _native_absolute_path(evidence.executable_path)
    if executable_path is None:
        return False
    try:
        observed = _bounded_file_evidence(
            executable_path,
            max_bytes=_ADAPTER_RUNTIME_EXECUTABLE_MAX_BYTES,
            label="adapter runtime executable",
        )
    except ExternalRunnerError:
        return False
    return (
        observed.file_sha256 == evidence.executable_sha256
        and observed.byte_count == evidence.executable_bytes
    )


def _command_uses_adapter_runtime(
    command: Sequence[str],
    evidence: AdapterRuntimeEvidence,
) -> bool:
    if not evidence.claim_evidence_complete:
        return True
    if not command:
        return False
    executable_path = _native_absolute_path(evidence.executable_path)
    if executable_path is None:
        return False
    try:
        return Path(command[0]).expanduser().resolve() == executable_path
    except OSError:
        return False


def capture_network_isolation_evidence(
    mode: str,
    path: Path | str | None = None,
) -> NetworkIsolationEvidence:
    """Hash one retained external-containment artifact for a runner manifest."""

    if not isinstance(mode, str):
        raise ExternalRunnerError("network isolation mode is invalid")
    if mode == "unverified":
        if path is not None:
            raise ExternalRunnerError(
                "unverified network isolation cannot accept an evidence path"
            )
        return NetworkIsolationEvidence()
    if mode not in CLAIM_NETWORK_ISOLATION_MODES:
        raise ExternalRunnerError("network isolation mode is invalid")
    if path is None:
        raise ExternalRunnerError(
            "evidenced network isolation requires a retained evidence file"
        )
    resolved = Path(path).expanduser().resolve()
    evidence = _bounded_file_evidence(
        resolved,
        max_bytes=_NETWORK_ISOLATION_EVIDENCE_MAX_BYTES,
        label="network isolation evidence",
    )
    if evidence.byte_count <= 0:
        raise ExternalRunnerError("network isolation evidence cannot be empty")
    return NetworkIsolationEvidence(
        mode=mode,
        evidence_path=str(resolved),
        evidence_sha256=evidence.file_sha256,
        evidence_bytes=evidence.byte_count,
    )


def _network_isolation_evidence_matches(
    evidence: NetworkIsolationEvidence,
) -> bool:
    if evidence.mode == "unverified":
        return True
    evidence_path = _native_absolute_path(evidence.evidence_path)
    if evidence_path is None:
        return False
    try:
        observed = _bounded_file_evidence(
            evidence_path,
            max_bytes=_NETWORK_ISOLATION_EVIDENCE_MAX_BYTES,
            label="network isolation evidence",
        )
    except ExternalRunnerError:
        return False
    return (
        observed.file_sha256 == evidence.evidence_sha256
        and observed.byte_count == evidence.evidence_bytes
    )


@lru_cache(maxsize=1)
def _windows_inference_process_api():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)

    class FileTime(ctypes.Structure):
        _fields_ = [
            ("low", wintypes.DWORD),
            ("high", wintypes.DWORD),
        ]

    class ProcessMemoryCountersEx(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("page_fault_count", wintypes.DWORD),
            ("peak_working_set_size", ctypes.c_size_t),
            ("working_set_size", ctypes.c_size_t),
            ("quota_peak_paged_pool_usage", ctypes.c_size_t),
            ("quota_paged_pool_usage", ctypes.c_size_t),
            ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
            ("quota_non_paged_pool_usage", ctypes.c_size_t),
            ("pagefile_usage", ctypes.c_size_t),
            ("peak_pagefile_usage", ctypes.c_size_t),
            ("private_usage", ctypes.c_size_t),
        ]

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCountersEx),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    return kernel32, psapi, FileTime, ProcessMemoryCountersEx


def _windows_inference_process_snapshot(
    process_id: int,
) -> _InferenceProcessSnapshot:
    import ctypes
    from ctypes import wintypes

    process_query_information = 0x0400
    process_vm_read = 0x0010
    (
        kernel32,
        psapi,
        file_time_type,
        memory_counters_type,
    ) = _windows_inference_process_api()

    handle = kernel32.OpenProcess(
        process_query_information | process_vm_read,
        False,
        process_id,
    )
    if not handle:
        error = ctypes.get_last_error()
        raise ExternalRunnerError(
            f"could not inspect inference-service process {process_id} "
            f"(Windows error {error})"
        )
    try:
        path_buffer = ctypes.create_unicode_buffer(32_768)
        path_length = wintypes.DWORD(len(path_buffer))
        if not kernel32.QueryFullProcessImageNameW(
            handle,
            0,
            path_buffer,
            ctypes.byref(path_length),
        ):
            error = ctypes.get_last_error()
            raise ExternalRunnerError(
                "could not resolve inference-service executable "
                f"(Windows error {error})"
            )
        creation = file_time_type()
        exit_time = file_time_type()
        kernel_time = file_time_type()
        user_time = file_time_type()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            error = ctypes.get_last_error()
            raise ExternalRunnerError(
                "could not read inference-service start time "
                f"(Windows error {error})"
            )
        counters = memory_counters_type()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(
            handle,
            ctypes.byref(counters),
            counters.cb,
        ):
            error = ctypes.get_last_error()
            raise ExternalRunnerError(
                "could not read inference-service memory "
                f"(Windows error {error})"
            )
    finally:
        kernel32.CloseHandle(handle)
    executable = Path(path_buffer.value).resolve()
    return _InferenceProcessSnapshot(
        process_id=process_id,
        process_start_token=(
            f"windows-filetime:{creation.high:08x}{creation.low:08x}"
        ),
        executable_path=str(executable),
        memory_metric="working-set-bytes",
        memory_bytes=int(counters.working_set_size),
    )


@lru_cache(maxsize=1)
def _darwin_inference_process_api():
    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    except OSError as exc:
        raise ExternalRunnerError(
            "could not load macOS inference-service process accounting"
        ) from exc
    libproc.proc_pidpath.argtypes = [
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    libproc.proc_pidpath.restype = ctypes.c_int
    libproc.proc_pidinfo.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    libproc.proc_pidinfo.restype = ctypes.c_int
    return libproc


def _darwin_process_bsd_info(
    process_id: int,
) -> _DarwinProcBsdInfo:
    information = _DarwinProcBsdInfo()
    expected_bytes = ctypes.sizeof(information)
    observed_bytes = _darwin_inference_process_api().proc_pidinfo(
        process_id,
        _DARWIN_PROC_PIDTBSDINFO,
        0,
        ctypes.byref(information),
        expected_bytes,
    )
    if (
        observed_bytes != expected_bytes
        or information.pbi_pid != process_id
        or information.pbi_start_tvsec <= 0
        or information.pbi_start_tvusec >= 1_000_000
    ):
        raise ExternalRunnerError(
            f"could not inspect inference-service process {process_id} "
            "identity on macOS"
        )
    return information


def _darwin_process_start_token(
    information: _DarwinProcBsdInfo,
) -> str:
    return (
        "darwin-proc-start:"
        f"{information.pbi_start_tvsec}:"
        f"{information.pbi_start_tvusec}"
    )


def _darwin_inference_process_snapshot(
    process_id: int,
) -> _InferenceProcessSnapshot:
    libproc = _darwin_inference_process_api()
    first_identity = _darwin_process_bsd_info(process_id)
    first_start_token = _darwin_process_start_token(first_identity)

    path_buffer = ctypes.create_string_buffer(
        _DARWIN_PROC_PIDPATHINFO_MAXSIZE
    )
    path_bytes = libproc.proc_pidpath(
        process_id,
        path_buffer,
        len(path_buffer),
    )
    if path_bytes <= 0 or path_bytes >= len(path_buffer):
        raise ExternalRunnerError(
            f"could not resolve inference-service process {process_id} "
            "executable on macOS"
    )
    raw_executable = path_buffer.value
    if (
        not raw_executable
        or path_bytes not in {len(raw_executable), len(raw_executable) + 1}
    ):
        raise ExternalRunnerError(
            "inference-service executable path is malformed on macOS"
        )
    try:
        executable = Path(os.fsdecode(raw_executable)).resolve()
    except (OSError, UnicodeError, ValueError) as exc:
        raise ExternalRunnerError(
            "could not canonicalize inference-service executable on macOS"
        ) from exc

    task_information = _DarwinProcTaskInfo()
    expected_task_bytes = ctypes.sizeof(task_information)
    observed_task_bytes = libproc.proc_pidinfo(
        process_id,
        _DARWIN_PROC_PIDTASKINFO,
        0,
        ctypes.byref(task_information),
        expected_task_bytes,
    )
    if (
        observed_task_bytes != expected_task_bytes
        or task_information.pti_resident_size <= 0
    ):
        raise ExternalRunnerError(
            f"could not inspect inference-service process {process_id} "
            "memory on macOS"
        )

    second_identity = _darwin_process_bsd_info(process_id)
    if _darwin_process_start_token(second_identity) != first_start_token:
        raise ExternalRunnerError(
            "inference-service process identity changed during sampling"
        )
    return _InferenceProcessSnapshot(
        process_id=process_id,
        process_start_token=first_start_token,
        executable_path=str(executable),
        memory_metric="resident-set-bytes",
        memory_bytes=int(task_information.pti_resident_size),
    )


def _linux_process_start_token(stat_path: Path) -> str:
    try:
        stat_text = stat_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExternalRunnerError(
            "could not read inference-service process identity"
        ) from exc
    command_end = stat_text.rfind(")")
    if command_end < 0:
        raise ExternalRunnerError(
            "inference-service process identity is malformed"
        )
    fields_after_command = stat_text[command_end + 2 :].split()
    if len(fields_after_command) <= 19:
        raise ExternalRunnerError(
            "inference-service process identity is incomplete"
        )
    return f"linux-proc-start:{fields_after_command[19]}"


def _linux_inference_process_snapshot(
    process_id: int,
) -> _InferenceProcessSnapshot:
    process_directory = Path("/proc") / str(process_id)
    stat_path = process_directory / "stat"
    first_start_token = _linux_process_start_token(stat_path)
    try:
        executable = Path(os.readlink(process_directory / "exe")).resolve()
        statm_fields = (process_directory / "statm").read_text(
            encoding="utf-8"
        ).split()
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError) as exc:
        raise ExternalRunnerError(
            f"could not inspect inference-service process {process_id}"
        ) from exc
    if len(statm_fields) < 2 or not statm_fields[1].isdigit():
        raise ExternalRunnerError(
            "inference-service memory counters are malformed"
        )
    second_start_token = _linux_process_start_token(stat_path)
    if second_start_token != first_start_token:
        raise ExternalRunnerError(
            "inference-service process identity changed during sampling"
        )
    memory_bytes = int(statm_fields[1]) * int(page_size)
    if memory_bytes <= 0:
        raise ExternalRunnerError(
            "inference-service resident memory is unavailable"
        )
    return _InferenceProcessSnapshot(
        process_id=process_id,
        process_start_token=first_start_token,
        executable_path=str(executable),
        memory_metric="resident-set-bytes",
        memory_bytes=memory_bytes,
    )


def _inference_process_snapshot(process_id: int) -> _InferenceProcessSnapshot:
    if os.name == "nt":
        return _windows_inference_process_snapshot(process_id)
    if sys.platform.startswith("linux"):
        return _linux_inference_process_snapshot(process_id)
    if sys.platform == "darwin":
        return _darwin_inference_process_snapshot(process_id)
    raise ExternalRunnerError(
        "inference-service process accounting is supported only on Windows, "
        "Linux, and macOS"
    )


def capture_inference_service_contract(
    process_id: int | None = None,
    *,
    max_memory_mb: int | None = None,
) -> InferenceServiceContract:
    """Capture a stable service identity and ceiling before adapter execution."""

    if process_id is None and max_memory_mb is None:
        return InferenceServiceContract()
    if (
        isinstance(process_id, bool)
        or not isinstance(process_id, int)
        or process_id <= 0
    ):
        raise ExternalRunnerError(
            "inference-service accounting requires a positive process ID"
        )
    if (
        isinstance(max_memory_mb, bool)
        or not isinstance(max_memory_mb, int)
        or max_memory_mb <= 0
    ):
        raise ExternalRunnerError(
            "inference-service accounting requires a positive memory ceiling"
        )
    snapshot = _inference_process_snapshot(process_id)
    executable = Path(snapshot.executable_path)
    evidence = _bounded_file_evidence(
        executable,
        max_bytes=_INFERENCE_SERVICE_EXECUTABLE_MAX_BYTES,
        label="inference-service executable",
    )
    if evidence.byte_count <= 0:
        raise ExternalRunnerError(
            "inference-service executable evidence cannot be empty"
        )
    if snapshot.memory_bytes > max_memory_mb * 1024 * 1024:
        raise ExternalRunnerError(
            "inference-service memory already exceeds the configured ceiling"
        )
    return InferenceServiceContract(
        process_id=snapshot.process_id,
        process_start_token=snapshot.process_start_token,
        executable_path=str(executable),
        executable_sha256=evidence.file_sha256,
        executable_bytes=evidence.byte_count,
        memory_metric=snapshot.memory_metric,
        max_memory_mb=max_memory_mb,
    )


class _InferenceServiceMonitor:
    """Sample a captured service without taking ownership of its process."""

    def __init__(self, contract: InferenceServiceContract) -> None:
        self.contract = contract
        self.sample_count = 0
        self.peak_memory_bytes: int | None = None

    def _executable_matches(self) -> bool:
        if not self.contract.enabled:
            return True
        executable_path = _native_absolute_path(
            self.contract.executable_path
        )
        if executable_path is None:
            return False
        try:
            observed = _bounded_file_evidence(
                executable_path,
                max_bytes=_INFERENCE_SERVICE_EXECUTABLE_MAX_BYTES,
                label="inference-service executable",
            )
        except ExternalRunnerError:
            return False
        return (
            observed.file_sha256 == self.contract.executable_sha256
            and observed.byte_count == self.contract.executable_bytes
        )

    def _snapshot_matches(
        self,
        observed: _InferenceProcessSnapshot,
    ) -> bool:
        return (
            observed.process_start_token
            == self.contract.process_start_token
            and observed.executable_path == self.contract.executable_path
            and observed.memory_metric == self.contract.memory_metric
        )

    def sample(self, *, check_executable: bool = False) -> str | None:
        if not self.contract.enabled:
            return None
        if _native_absolute_path(self.contract.executable_path) is None:
            return "inference_service_unavailable"

        try:
            snapshot = _inference_process_snapshot(
                int(self.contract.process_id or 0)
            )
        except ExternalRunnerError:
            return "inference_service_unavailable"
        if not self._snapshot_matches(snapshot):
            return "inference_service_identity_changed"
        snapshots = [snapshot]
        if check_executable and not self._executable_matches():
            return "inference_service_executable_modified"
        if check_executable:
            try:
                final_snapshot = _inference_process_snapshot(
                    int(self.contract.process_id or 0)
                )
            except ExternalRunnerError:
                return "inference_service_unavailable"
            if not self._snapshot_matches(final_snapshot):
                return "inference_service_identity_changed"
            snapshots.append(final_snapshot)
        observed_peak = max(value.memory_bytes for value in snapshots)
        self.sample_count += len(snapshots)
        self.peak_memory_bytes = max(
            self.peak_memory_bytes or 0,
            observed_peak,
        )
        if (
            self.contract.max_memory_mb is not None
            and observed_peak
            > self.contract.max_memory_mb * 1024 * 1024
        ):
            return "inference_service_memory_limit"
        return None

    def accounting(self) -> InferenceServiceAccounting:
        if not self.contract.enabled:
            return InferenceServiceAccounting()
        return InferenceServiceAccounting(
            process_id=self.contract.process_id,
            process_start_token=self.contract.process_start_token,
            executable_path=self.contract.executable_path,
            executable_sha256=self.contract.executable_sha256,
            executable_bytes=self.contract.executable_bytes,
            memory_metric=self.contract.memory_metric,
            max_memory_mb=self.contract.max_memory_mb,
            sample_count=self.sample_count,
            peak_memory_bytes=self.peak_memory_bytes,
        )


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


_WINDOWS_RESERVED_PATH_NAMES = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
    | {f"com{number}" for number in ("\u00b9", "\u00b2", "\u00b3")}
    | {f"lpt{number}" for number in ("\u00b9", "\u00b2", "\u00b3")}
)
_WINDOWS_INVALID_PATH_CHARACTERS = frozenset('<>:"|?*')


def _windows_path_component_is_safe(component: str) -> bool:
    if (
        not component
        or component in {".", ".."}
        or component.endswith((" ", "."))
        or any(
            character in _WINDOWS_INVALID_PATH_CHARACTERS
            or ord(character) < 32
            or 127 <= ord(character) <= 159
            for character in component
        )
    ):
        return False
    stem = component.split(".", 1)[0].casefold()
    return stem not in _WINDOWS_RESERVED_PATH_NAMES


def _absolute_path_flavor(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or any(
            ord(character) < 32 or 127 <= ord(character) <= 159
            for character in value
        )
    ):
        return None

    if value.startswith("/"):
        if (
            value.startswith("//")
            or "\\" in value
            or (value != "/" and value.endswith("/"))
            or "//" in value[1:]
        ):
            return None
        components = () if value == "/" else tuple(value[1:].split("/"))
        if any(component in {"", ".", ".."} for component in components):
            return None
        path = PurePosixPath(value)
        return "posix" if path.is_absolute() and path.as_posix() == value else None

    if value.startswith("\\\\"):
        if (
            value.startswith(("\\\\?\\", "\\\\.\\"))
            or "/" in value
            or value.endswith("\\")
            or "\\\\" in value[2:]
        ):
            return None
        components = tuple(value[2:].split("\\"))
        if len(components) < 2 or not all(
            _windows_path_component_is_safe(component)
            for component in components
        ):
            return None
        return "windows" if PureWindowsPath(value).is_absolute() else None

    drive_match = re.fullmatch(r"[A-Za-z]:([\\/])(.*)", value)
    if drive_match is None:
        return None
    separator, remainder = drive_match.groups()
    other_separator = "/" if separator == "\\" else "\\"
    if (
        other_separator in remainder
        or remainder.startswith(separator)
        or (remainder and remainder.endswith(separator))
        or separator * 2 in remainder
    ):
        return None
    components = () if not remainder else tuple(remainder.split(separator))
    if not all(
        _windows_path_component_is_safe(component)
        for component in components
    ):
        return None
    path = PureWindowsPath(value)
    if not path.is_absolute():
        return None
    canonical = str(path)
    if separator == "/":
        canonical = canonical.replace("\\", "/")
    return "windows" if canonical == value else None


def _is_portable_absolute_path(value: object) -> bool:
    return _absolute_path_flavor(value) is not None


def _native_absolute_path(value: object) -> Path | None:
    expected_flavor = "windows" if os.name == "nt" else "posix"
    if (
        _absolute_path_flavor(value) != expected_flavor
        or (
            expected_flavor == "windows"
            and isinstance(value, str)
            and value.startswith("\\\\")
        )
    ):
        return None
    path = Path(value)
    return path if path.is_absolute() else None


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def _candidate_json_limits(max_bytes: int) -> StrictJsonLimits:
    return StrictJsonLimits(
        max_bytes=max_bytes,
        max_line_chars=max_bytes,
        max_depth=64,
    )


def _remove_runner_directory(path: Path) -> None:
    deadline = time.monotonic() + 2.0
    while True:
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            if (
                os.name != "nt"
                or time.monotonic() >= deadline
                or getattr(exc, "winerror", None) not in {5, 32, 145}
            ):
                raise
            time.sleep(0.02)


@contextmanager
def _runner_temporary_directory(
    *,
    prefix: str,
    directory: Path,
) -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix=prefix, dir=directory))
    try:
        yield path
    finally:
        _remove_runner_directory(path)


class _WindowsJob:
    """Own a Windows Job Object that bounds an adapter process tree."""

    def __init__(self, handle: object, kernel32: Any) -> None:
        self._handle = handle
        self._kernel32 = kernel32

    @classmethod
    def create(cls, max_memory_mb: int | None) -> _WindowsJob:
        import ctypes
        from ctypes import wintypes

        class JobObjectBasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JobObjectExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JobObjectBasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = (
            wintypes.HANDLE,
            wintypes.HANDLE,
        )
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.IsProcessInJob.argtypes = (
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.BOOL),
        )
        kernel32.IsProcessInJob.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            error = ctypes.get_last_error()
            raise ExternalRunnerError(f"could not create Windows Job Object (error {error})")
        job = cls(handle, kernel32)
        information = JobObjectExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = _WINDOWS_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if max_memory_mb is not None:
            memory_bytes = max_memory_mb * 1024 * 1024
            information.BasicLimitInformation.LimitFlags |= (
                _WINDOWS_JOB_OBJECT_LIMIT_PROCESS_MEMORY | _WINDOWS_JOB_OBJECT_LIMIT_JOB_MEMORY
            )
            information.ProcessMemoryLimit = memory_bytes
            information.JobMemoryLimit = memory_bytes
        if not kernel32.SetInformationJobObject(
            handle,
            _WINDOWS_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            error = ctypes.get_last_error()
            job.close()
            raise ExternalRunnerError(f"could not configure Windows Job Object (error {error})")
        return job

    @property
    def handle(self) -> object:
        return self._handle

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        import ctypes

        process_handle = getattr(process, "_handle", None)
        if process_handle is None:
            raise ExternalRunnerError("adapter process has no Windows process handle")
        if not self._kernel32.AssignProcessToJobObject(
            self._handle,
            process_handle,
        ):
            error = ctypes.get_last_error()
            raise ExternalRunnerError(
                f"could not assign adapter to Windows Job Object (error {error})"
            )

    def contains(self, process: subprocess.Popen[bytes]) -> bool:
        import ctypes
        from ctypes import wintypes

        process_handle = getattr(process, "_handle", None)
        if process_handle is None:
            return False
        result = wintypes.BOOL()
        if not self._kernel32.IsProcessInJob(
            process_handle,
            self._handle,
            ctypes.byref(result),
        ):
            error = ctypes.get_last_error()
            raise ExternalRunnerError(
                f"could not verify Windows Job Object membership (error {error})"
            )
        return bool(result.value)

    def terminate(self) -> None:
        import ctypes

        if self._handle and not self._kernel32.TerminateJobObject(self._handle, 1):
            error = ctypes.get_last_error()
            raise ExternalRunnerError(f"could not terminate Windows Job Object (error {error})")
        deadline = time.monotonic() + 1.0
        while self._active_processes() != 0:
            if time.monotonic() >= deadline:
                raise ExternalRunnerError("Windows Job Object processes did not terminate")
            time.sleep(0.01)

    def _active_processes(self) -> int:
        import ctypes
        from ctypes import wintypes

        class JobObjectBasicAccountingInformation(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", wintypes.LARGE_INTEGER),
                ("TotalKernelTime", wintypes.LARGE_INTEGER),
                ("ThisPeriodTotalUserTime", wintypes.LARGE_INTEGER),
                ("ThisPeriodTotalKernelTime", wintypes.LARGE_INTEGER),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        self._kernel32.QueryInformationJobObject.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        )
        self._kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        information = JobObjectBasicAccountingInformation()
        if not self._kernel32.QueryInformationJobObject(
            self._handle,
            1,
            ctypes.byref(information),
            ctypes.sizeof(information),
            None,
        ):
            error = ctypes.get_last_error()
            raise ExternalRunnerError(f"could not query Windows Job Object (error {error})")
        return int(information.ActiveProcesses)

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def _resume_windows_process(process: subprocess.Popen[bytes]) -> None:
    """Resume the primary thread of a newly created suspended process."""

    import ctypes
    from ctypes import wintypes

    class ThreadEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Thread32First.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(ThreadEntry32),
    )
    kernel32.Thread32First.restype = wintypes.BOOL
    kernel32.Thread32Next.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(ThreadEntry32),
    )
    kernel32.Thread32Next.restype = wintypes.BOOL
    kernel32.OpenThread.argtypes = (
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    )
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.ResumeThread.argtypes = (wintypes.HANDLE,)
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    snapshot = kernel32.CreateToolhelp32Snapshot(_WINDOWS_TH32CS_SNAPTHREAD, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if snapshot == invalid_handle:
        error = ctypes.get_last_error()
        raise ExternalRunnerError(f"could not enumerate suspended adapter threads (error {error})")
    try:
        entry = ThreadEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        found_thread_id: int | None = None
        has_entry = kernel32.Thread32First(snapshot, ctypes.byref(entry))
        while has_entry:
            if entry.th32OwnerProcessID == process.pid:
                found_thread_id = int(entry.th32ThreadID)
                break
            has_entry = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
        if found_thread_id is None:
            raise ExternalRunnerError("could not find the suspended adapter primary thread")
        thread = kernel32.OpenThread(
            _WINDOWS_THREAD_SUSPEND_RESUME,
            False,
            found_thread_id,
        )
        if not thread:
            error = ctypes.get_last_error()
            raise ExternalRunnerError(
                f"could not open the suspended adapter thread (error {error})"
            )
        try:
            previous_count = kernel32.ResumeThread(thread)
            if previous_count == 0xFFFFFFFF:
                error = ctypes.get_last_error()
                raise ExternalRunnerError(
                    f"could not resume the bounded adapter process (error {error})"
                )
            if previous_count == 0:
                raise ExternalRunnerError(
                    "adapter primary thread was not suspended before Job assignment"
                )
        finally:
            kernel32.CloseHandle(thread)
    finally:
        kernel32.CloseHandle(snapshot)


def _resolve_command(command: Sequence[str]) -> tuple[str, ...]:
    if not command or not all(isinstance(part, str) and part for part in command):
        raise ExternalRunnerError("command must contain non-empty string arguments")
    executable = shutil.which(command[0])
    if executable is None:
        explicit = Path(command[0]).expanduser()
        if not explicit.is_file():
            raise ExternalRunnerError(f"adapter executable was not found: {command[0]}")
        executable = str(explicit.resolve())
    return (str(Path(executable).resolve()), *command[1:])


def _portable_command_contract(
    command: Sequence[str],
    *,
    working_directory: Path,
    system: str,
    corpus_path: Path,
    candidate_path: Path,
    case_id: str | None,
    adapter_entrypoint: AdapterEntrypointEvidence,
    adapter_source: AdapterSourceEvidence,
    adapter_runtime: AdapterRuntimeEvidence,
) -> tuple[str, ...]:
    """Normalize bound paths and runner substitutions into stable tokens."""

    source_root = (
        Path(adapter_source.source_root or "")
        if adapter_source.claim_evidence_complete
        else None
    )
    entrypoint_path = (
        Path(adapter_entrypoint.entrypoint_path or "")
        if adapter_entrypoint.claim_evidence_complete
        else None
    )
    runtime_path = (
        Path(adapter_runtime.executable_path or "")
        if adapter_runtime.claim_evidence_complete
        else None
    )
    source_paths: dict[Path, str] = {}
    if source_root is not None:
        source_paths[source_root] = ""
        for record in adapter_source.files:
            relative = PurePosixPath(record.relative_path)
            current = source_root
            for part in relative.parts:
                current = current / part
                source_paths[current] = current.relative_to(
                    source_root
                ).as_posix()

    def resolved_argument_path(argument: str) -> Path | None:
        if not argument or argument.startswith("{"):
            return None
        candidate = Path(argument).expanduser()
        if not candidate.is_absolute():
            candidate = working_directory / candidate
        try:
            return candidate.resolve()
        except OSError:
            return None

    entrypoint_relative = (
        entrypoint_path.relative_to(source_root).as_posix()
        if entrypoint_path is not None and source_root is not None
        else entrypoint_path.name
        if entrypoint_path is not None
        else ""
    )

    def embedded_contract(argument: str) -> str | None:
        replacements: list[tuple[str, str, str]] = [
            ("{corpus}", "corpus", ""),
            ("{candidate}", "candidate", ""),
            ("{system}", "system", ""),
            ("{case_id}", "case-id", ""),
            (str(corpus_path), "corpus", ""),
            (str(candidate_path), "candidate", ""),
        ]
        if case_id is not None:
            replacements.append((case_id, "case-id", ""))
        replacements.append((system, "system", ""))
        if entrypoint_path is not None:
            replacements.append(
                (
                    str(entrypoint_path),
                    "adapter-entrypoint",
                    entrypoint_relative,
                )
            )
        if source_root is not None:
            replacements.append(
                (str(source_root), "adapter-source-root", "")
            )
        unique: dict[str, tuple[str, str]] = {}
        for needle, kind, value in replacements:
            if needle and needle not in unique:
                unique[needle] = (kind, value)
        position = 0
        segments: list[list[str]] = []
        matched = False
        while position < len(argument):
            choices = [
                (argument.find(needle, position), needle, kind, value)
                for needle, (kind, value) in unique.items()
            ]
            choices = [choice for choice in choices if choice[0] >= 0]
            if not choices:
                segments.append(["literal", argument[position:]])
                break
            found_at, needle, kind, value = min(
                choices,
                key=lambda choice: (choice[0], -len(choice[1])),
            )
            if found_at > position:
                segments.append(
                    ["literal", argument[position:found_at]]
                )
            segments.append([kind, value])
            matched = True
            position = found_at + len(needle)
        if not matched:
            return None
        return "template:" + json.dumps(
            segments,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    contract: list[str] = []
    for index, argument in enumerate(command):
        argument_path = resolved_argument_path(argument)
        if index == 0 and runtime_path is not None:
            if argument_path != runtime_path:
                contract.append(f"literal:{argument}")
                continue
            if entrypoint_path is not None and runtime_path == entrypoint_path:
                contract.append(
                    f"runtime-entrypoint:{entrypoint_relative}"
                )
            else:
                contract.append("runtime-executable:")
            continue
        if argument == "{corpus}" or argument_path == corpus_path:
            contract.append("placeholder:corpus")
            continue
        if (
            argument == "{candidate}"
            or argument_path == candidate_path
        ):
            contract.append("placeholder:candidate")
            continue
        if argument == "{system}" or argument == system:
            contract.append("placeholder:system")
            continue
        if argument == "{case_id}" or (
            case_id is not None and argument == case_id
        ):
            contract.append("placeholder:case-id")
            continue
        if (
            entrypoint_path is not None
            and argument_path == entrypoint_path
        ):
            contract.append(
                f"adapter-entrypoint:{entrypoint_relative}"
            )
            continue
        if (
            source_root is not None
            and argument_path is not None
            and argument_path in source_paths
        ):
            contract.append(
                "adapter-source:" + source_paths[argument_path]
            )
            continue
        embedded = embedded_contract(argument)
        if embedded is not None:
            contract.append(embedded)
            continue
        contract.append(f"literal:{argument}")
    return tuple(contract)


def _claim_command_contract_complete(
    contract: Sequence[str],
) -> bool:
    kinds: set[str] = set()
    literals: list[str] = []
    for value in contract:
        if value == "runtime-executable:":
            kinds.add("runtime")
            continue
        if value.startswith("runtime-entrypoint:"):
            kinds.update({"runtime", "adapter-entrypoint"})
            continue
        if value.startswith("adapter-entrypoint:"):
            kinds.add("adapter-entrypoint")
            continue
        if value.startswith("placeholder:"):
            kinds.add(value.removeprefix("placeholder:"))
            continue
        if value.startswith("literal:"):
            literals.append(value.removeprefix("literal:"))
            continue
        if value.startswith("template:"):
            try:
                segments = json.loads(value.removeprefix("template:"))
            except (json.JSONDecodeError, RecursionError, TypeError, ValueError):
                return False
            if not isinstance(segments, list):
                return False
            for segment in segments:
                if (
                    not isinstance(segment, list)
                    or len(segment) != 2
                    or not all(
                        isinstance(item, str) for item in segment
                    )
                ):
                    return False
                kind, segment_value = segment
                if kind == "literal":
                    literals.append(segment_value)
                else:
                    kinds.add(kind)
            continue
    if not {
        "runtime",
        "adapter-entrypoint",
        "corpus",
        "candidate",
        "system",
    }.issubset(kinds):
        return False
    for literal in literals:
        if (
            PurePosixPath(literal).is_absolute()
            or PureWindowsPath(literal).is_absolute()
        ):
            return False
    return True


def _command_references_adapter_entrypoint(
    command: Sequence[str],
    *,
    working_directory: Path,
    evidence: AdapterEntrypointEvidence,
) -> bool:
    if not evidence.claim_evidence_complete:
        return True
    expected = _native_absolute_path(evidence.entrypoint_path)
    if expected is None:
        return False
    for argument in command:
        candidate = Path(argument).expanduser()
        if not candidate.is_absolute():
            candidate = working_directory / candidate
        try:
            if candidate.resolve() == expected:
                return True
        except OSError:
            continue
    return False


def _verify_darwin_process_group_after_sigkill(
    process_group_id: int,
    *,
    process: subprocess.Popen[bytes] | None,
    termination_signal_delivered: bool,
) -> None:
    if process is None:
        raise ExternalRunnerError(
            "could not prove Darwin process-group cleanup without its leader"
        )
    deadline = time.monotonic() + _PROCESS_GROUP_GRACE_SECONDS
    while True:
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            return
        except PermissionError as exc:
            if exc.errno != errno.EPERM:
                raise ExternalRunnerError(
                    "could not verify the adapter process group after SIGKILL"
                ) from exc
            break
        except OSError as exc:
            raise ExternalRunnerError(
                "could not verify the adapter process group after SIGKILL"
            ) from exc
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.01, remaining))
    prove_darwin_process_group_all_zombies(
        process_group_id,
        expected_leader_pid=process.pid,
        error_type=ExternalRunnerError,
        termination_signal_delivered=termination_signal_delivered,
    )


def _terminate_posix_process_group(
    process_group_id: int,
    *,
    process: subprocess.Popen[bytes] | None = None,
) -> None:
    if process is not None and process.returncode is not None:
        raise ExternalRunnerError(
            "adapter process was reaped before process-group cleanup"
        )
    direct_process_exited = (
        process is not None
        and _posix_process_exited_without_reaping(process)
    )
    if direct_process_exited:
        termination_signal_delivered = False
        try:
            os.killpg(process_group_id, _POSIX_SIGKILL)
            termination_signal_delivered = True
        except ProcessLookupError:
            return
        except PermissionError as exc:
            if exc.errno != errno.EPERM or sys.platform != "darwin":
                raise ExternalRunnerError(
                    "could not kill the adapter process group"
                ) from exc
        except OSError as exc:
            raise ExternalRunnerError(
                "could not kill the adapter process group"
            ) from exc
        if sys.platform == "darwin":
            _verify_darwin_process_group_after_sigkill(
                process_group_id,
                process=process,
                termination_signal_delivered=termination_signal_delivered,
            )
        return
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError as exc:
        # A Darwin leader may exit after the WNOWAIT observation but before
        # SIGTERM. Re-observe the still-owned leader and accept EPERM only when
        # a stable inspection proves that every anchored member is a zombie.
        if (
            exc.errno == errno.EPERM
            and sys.platform == "darwin"
            and process is not None
            and _posix_process_exited_without_reaping(process)
        ):
            prove_darwin_process_group_all_zombies(
                process_group_id,
                expected_leader_pid=process.pid,
                error_type=ExternalRunnerError,
            )
            return
        # Otherwise EPERM cannot distinguish a zombie-only group from a live
        # process with another effective identity. Reaping and retrying the
        # numeric PGID would also permit group-ID reuse, so fail closed.
        raise ExternalRunnerError(
            "could not terminate the adapter process group"
        ) from exc
    except OSError as exc:
        raise ExternalRunnerError(
            "could not terminate the adapter process group"
        ) from exc
    deadline = time.monotonic() + _PROCESS_GROUP_GRACE_SECONDS
    while time.monotonic() < deadline:
        direct_process_exited = (
            process is not None
            and _posix_process_exited_without_reaping(process)
        )
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            return
        except PermissionError as exc:
            if (
                exc.errno == errno.EPERM
                and sys.platform == "darwin"
                and process is not None
            ):
                if not direct_process_exited:
                    direct_process_exited = _posix_process_exited_without_reaping(
                        process
                    )
                if direct_process_exited:
                    prove_darwin_process_group_all_zombies(
                        process_group_id,
                        expected_leader_pid=process.pid,
                        error_type=ExternalRunnerError,
                        termination_signal_delivered=True,
                    )
                    return
            raise ExternalRunnerError(
                "could not verify the adapter process group after SIGTERM"
            ) from exc
        except OSError as exc:
            raise ExternalRunnerError(
                "could not verify the adapter process group after SIGTERM"
            ) from exc
        if direct_process_exited:
            # The leader remains waitable (WNOWAIT), anchoring the PGID while
            # SIGKILL removes any descendant that ignored SIGTERM. The caller
            # may reap the leader only after this final group signal.
            break
        time.sleep(0.01)
    termination_signal_delivered = True
    try:
        os.killpg(process_group_id, _POSIX_SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError as exc:
        if exc.errno != errno.EPERM or sys.platform != "darwin":
            raise ExternalRunnerError(
                "could not kill the adapter process group"
            ) from exc
    except OSError as exc:
        raise ExternalRunnerError(
            "could not kill the adapter process group"
        ) from exc
    if sys.platform == "darwin":
        _verify_darwin_process_group_after_sigkill(
            process_group_id,
            process=process,
            termination_signal_delivered=termination_signal_delivered,
        )


def _posix_process_exited_without_reaping(
    process: subprocess.Popen[bytes],
) -> bool:
    if process.returncode is not None:
        raise ExternalRunnerError(
            "adapter process was reaped before process-group cleanup"
        )
    try:
        result = os.waitid(
            os.P_PID,
            process.pid,
            os.WEXITED | os.WNOHANG | os.WNOWAIT,
        )
    except ChildProcessError as exc:
        raise ExternalRunnerError(
            "adapter process was reaped before process-group cleanup"
        ) from exc
    except OSError as exc:
        raise ExternalRunnerError(
            "could not observe adapter exit without reaping"
        ) from exc
    return result is not None


def _terminate_process_tree(
    process: subprocess.Popen[bytes],
    windows_job: _WindowsJob | None = None,
) -> None:
    if os.name != "nt":
        _terminate_posix_process_group(
            process.pid,
            process=process,
        )
        return
    if process.poll() is not None:
        return
    if windows_job is not None:
        try:
            windows_job.terminate()
        except ExternalRunnerError:
            process.kill()
        return
    completed = subprocess.run(
        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,  # type: ignore[attr-defined]
    )
    if completed.returncode != 0 and process.poll() is None:
        process.kill()


def _cleanup_and_reap_posix_process(
    process: subprocess.Popen[bytes],
) -> int:
    try:
        _terminate_posix_process_group(
            process.pid,
            process=process,
        )
    except ExternalRunnerError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExternalRunnerError(
            "could not clean the adapter process group"
        ) from exc
    try:
        return process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        # The group cleanup has already delivered its final signal while the
        # unreaped leader anchored the PGID. Only the still-owned direct child
        # may be touched after that point.
        try:
            process.kill()
        except OSError as exc:
            raise ExternalRunnerError(
                "could not stop the adapter process after "
                "process-group cleanup"
            ) from exc
        try:
            return process.wait(timeout=1)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ExternalRunnerError(
                "could not reap the adapter process after "
                "process-group cleanup"
            ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExternalRunnerError(
            "could not reap the adapter process after process-group cleanup"
        ) from exc


def _best_effort_stop_and_reap_direct_process(
    process: subprocess.Popen[bytes],
) -> None:
    with suppress(OSError):
        process.kill()
    with suppress(OSError, subprocess.SubprocessError):
        process.wait(timeout=1)


def _posix_limit_setup(limits: RunnerLimits):
    if (
        os.name == "nt"
        or sys.platform == "darwin"
        or limits.max_memory_mb is None
    ):
        return None

    def apply_limits() -> None:
        import resource

        memory_bytes = limits.max_memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        file_bytes = max(
            limits.max_stdout_bytes,
            limits.max_stderr_bytes,
            limits.max_candidate_bytes,
        )
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))

    return apply_limits


def _adapter_process_launch(
    command: Sequence[str],
    limits: RunnerLimits,
    *,
    darwin_environment_handoff: tuple[int, int, str] | None = None,
) -> tuple[list[str], Any]:
    if sys.platform != "darwin" or limits.max_memory_mb is None:
        if darwin_environment_handoff is not None:
            raise ExternalRunnerError("Darwin environment handoff is invalid on this launch path")
        return list(command), _posix_limit_setup(limits)
    if not command or not PurePosixPath(command[0]).is_absolute():
        raise ExternalRunnerError("Darwin adapter executable must be absolute")
    if darwin_environment_handoff is None:
        raise ExternalRunnerError("Darwin environment handoff is required")
    environment_fd, environment_size, environment_sha256 = darwin_environment_handoff
    if (
        isinstance(environment_fd, bool)
        or not isinstance(environment_fd, int)
        or environment_fd < 3
        or isinstance(environment_size, bool)
        or not isinstance(environment_size, int)
        or not len(_DARWIN_ENVIRONMENT_HANDOFF_PROTOCOL)
        <= environment_size
        <= _DARWIN_ENVIRONMENT_HANDOFF_MAX_BYTES
        or not _is_sha256(environment_sha256)
    ):
        raise ExternalRunnerError("Darwin environment handoff is invalid")
    memory_bytes = limits.max_memory_mb * 1024 * 1024
    file_bytes = max(
        limits.max_stdout_bytes,
        limits.max_stderr_bytes,
        limits.max_candidate_bytes,
    )
    return (
        [
            "/bin/sh",
            "-p",
            "-c",
            _DARWIN_PRELIMIT_LAUNCHER,
            _DARWIN_PRELIMIT_LAUNCHER_PROTOCOL,
            str(limits.max_memory_mb * 1024),
            "--",
            sys.executable,
            "-I",
            "-S",
            "-c",
            _DARWIN_LIMIT_LAUNCHER,
            _DARWIN_LIMIT_LAUNCHER_PROTOCOL,
            str(memory_bytes),
            str(file_bytes),
            str(environment_fd),
            str(environment_size),
            environment_sha256,
            "--",
            *command,
        ],
        None,
    )


@contextmanager
def _adapter_process_launch_context(
    command: Sequence[str],
    limits: RunnerLimits,
    *,
    environment: Mapping[str, str],
    directory: Path,
) -> Iterator[tuple[list[str], Any, dict[str, str], tuple[int, ...]]]:
    if sys.platform == "darwin" and limits.max_memory_mb is not None:
        with _darwin_environment_handoff(
            environment,
            directory=directory,
        ) as handoff:
            launch_command, preexec_fn = _adapter_process_launch(
                command,
                limits,
                darwin_environment_handoff=handoff,
            )
            yield (
                launch_command,
                preexec_fn,
                {},
                (handoff[0],),
            )
        return
    launch_command, preexec_fn = _adapter_process_launch(command, limits)
    yield launch_command, preexec_fn, dict(environment), ()


def _uses_windows_process_control() -> bool:
    return os.name == "nt"


def _load_corpus(path: Path):
    try:
        document = load_strict_json_file(
            path,
            limits=_CORPUS_JSON_LIMITS,
            label="benchmark corpus",
        )
        payload = document.value
        config, cases, dataset_sha256 = decode_corpus_document(
            payload,
            source_label=str(path),
        )
        return (
            config,
            cases,
            dataset_sha256,
            payload,
            document.file_sha256,
        )
    except (StrictJsonError, ExternalBaselineError) as exc:
        raise ExternalRunnerError(f"invalid corpus: {exc}") from exc


def _single_case_corpus_document(
    corpus_payload: Mapping[str, Any],
    raw_case: Mapping[str, Any],
) -> dict[str, Any]:
    case_config = dict(corpus_payload["config"])
    case_config["histories"] = 1
    document: dict[str, Any] = {
        "schema": corpus_payload["schema"],
        "benchmark": corpus_payload["benchmark"],
        "dataset_sha256": corpus_payload["dataset_sha256"],
        "producer": corpus_payload["producer"],
        "config": case_config,
        "cases": [raw_case],
    }
    document["corpus_sha256"] = _canonical_sha256(document)
    return document


def _serialized_corpus_document(document: Mapping[str, Any]) -> str:
    return (
        json.dumps(
            document,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    )


def _claim_controls_complete(
    identity: RunnerIdentity,
    limits: RunnerLimits,
    isolation_mode: str,
    working_directory: Path,
    command_contract: Sequence[str],
    dependency_lock: DependencyLockEvidence,
    adapter_entrypoint: AdapterEntrypointEvidence,
    adapter_source: AdapterSourceEvidence,
    adapter_runtime: AdapterRuntimeEvidence,
    process_environment: ProcessEnvironmentEvidence,
    network_isolation: NetworkIsolationEvidence,
    inference_service: InferenceServiceAccounting,
    minimum_inference_samples: int,
) -> bool:
    return (
        identity.claim_metadata_complete
        and isolation_mode == "per_case"
        and limits.max_memory_mb is not None
        and dependency_lock.claim_evidence_complete
        and identity.environment_id
        == f"sha256:{dependency_lock.evidence_sha256}"
        and adapter_entrypoint.claim_evidence_complete
        and adapter_source.claim_evidence_complete
        and _adapter_source_covers_entrypoint(
            adapter_source,
            adapter_entrypoint,
        )
        and adapter_runtime.claim_evidence_complete
        and process_environment.claim_evidence_complete
        and _claim_command_contract_complete(command_contract)
        and working_directory
        == Path(adapter_source.source_root or "")
        and network_isolation.claim_evidence_complete
        and inference_service.claim_evidence_complete
        and inference_service.sample_count >= minimum_inference_samples
    )


def _validated_case_runs(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ExternalRunnerError("run manifest case_runs must be an array")
    expected_fields = set(CaseRunRecord.__dataclass_fields__)
    decoded: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()
    for index, raw_record in enumerate(value):
        context = f"run manifest case_runs[{index}]"
        if not isinstance(raw_record, dict) or not all(isinstance(key, str) for key in raw_record):
            raise ExternalRunnerError(f"{context} must be an object")
        if set(raw_record) != expected_fields:
            raise ExternalRunnerError(f"{context} fields do not match the schema")
        case_id = raw_record["case_id"]
        if not isinstance(case_id, str) or not case_id:
            raise ExternalRunnerError(f"{context} case_id is invalid")
        if case_id in seen_case_ids:
            raise ExternalRunnerError(f"run manifest repeats case run {case_id!r}")
        seen_case_ids.add(case_id)
        command = raw_record["command"]
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(part, str) and part for part in command)
        ):
            raise ExternalRunnerError(f"{context} command is invalid")
        for name in ("corpus_path", "candidate_path"):
            path_value = raw_record[name]
            if (
                not isinstance(path_value, str)
                or not path_value
                or not Path(path_value).is_absolute()
            ):
                raise ExternalRunnerError(
                    f"{context} {name} is invalid"
                )
        started_at = raw_record["started_at"]
        if not isinstance(started_at, str) or not started_at:
            raise ExternalRunnerError(f"{context} started_at is invalid")
        try:
            parsed_started_at = datetime.fromisoformat(started_at)
        except ValueError as exc:
            raise ExternalRunnerError(f"{context} started_at is invalid") from exc
        if parsed_started_at.utcoffset() is None:
            raise ExternalRunnerError(f"{context} started_at must include a timezone")
        duration = raw_record["duration_seconds"]
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not 0 <= float(duration) < float("inf")
        ):
            raise ExternalRunnerError(f"{context} duration_seconds is invalid")
        for name in (
            "corpus_sha256",
            "corpus_file_sha256",
            "stdout_sha256",
            "stderr_sha256",
        ):
            if not _is_sha256(raw_record[name]):
                raise ExternalRunnerError(f"{context} {name} is invalid")
        for name in ("stdout_bytes", "stderr_bytes"):
            byte_count = raw_record[name]
            if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count < 0:
                raise ExternalRunnerError(f"{context} {name} is invalid")
        candidate_bytes = raw_record["candidate_bytes"]
        if candidate_bytes is not None and (
            isinstance(candidate_bytes, bool)
            or not isinstance(candidate_bytes, int)
            or candidate_bytes < 0
        ):
            raise ExternalRunnerError(f"{context} candidate_bytes is invalid")
        candidate_sha256 = raw_record["candidate_sha256"]
        if candidate_sha256 is not None and not _is_sha256(candidate_sha256):
            raise ExternalRunnerError(f"{context} candidate_sha256 is invalid")
        candidate_payload_sha256 = raw_record[
            "candidate_payload_sha256"
        ]
        if (
            candidate_payload_sha256 is not None
            and not _is_sha256(candidate_payload_sha256)
        ):
            raise ExternalRunnerError(
                f"{context} candidate_payload_sha256 is invalid"
            )
        exit_code = raw_record["exit_code"]
        if exit_code is not None and (
            isinstance(exit_code, bool) or not isinstance(exit_code, int)
        ):
            raise ExternalRunnerError(f"{context} exit_code is invalid")
        for name in ("termination_reason", "validation_error"):
            detail = raw_record[name]
            if detail is not None and (not isinstance(detail, str) or not detail):
                raise ExternalRunnerError(f"{context} {name} is invalid")
        for name in ("process_succeeded", "candidate_valid"):
            if not isinstance(raw_record[name], bool):
                raise ExternalRunnerError(f"{context} {name} must be boolean")
        if raw_record["process_succeeded"] and raw_record["termination_reason"] is not None:
            raise ExternalRunnerError(f"{context} successful process has a termination reason")
        if raw_record["candidate_valid"] and not raw_record["process_succeeded"]:
            raise ExternalRunnerError(f"{context} valid candidate came from a failed process")
        if raw_record["candidate_valid"] and (
            candidate_bytes is None
            or candidate_sha256 is None
            or candidate_payload_sha256 is None
        ):
            raise ExternalRunnerError(
                f"{context} valid candidate lacks file or payload evidence"
            )
        if (
            not raw_record["candidate_valid"]
            and candidate_payload_sha256 is not None
        ):
            raise ExternalRunnerError(
                f"{context} invalid candidate has payload evidence"
            )
        decoded.append(raw_record)
    return decoded


def load_external_run_manifest(
    path: Path | str,
    *,
    expected_dataset_sha256: str,
) -> ExternalRunReference:
    """Validate a runner manifest and its ready candidate without executing it."""

    manifest_path = Path(path).expanduser().resolve()
    if not manifest_path.is_file():
        raise ExternalRunnerError(f"run manifest was not found: {manifest_path}")
    try:
        document = load_strict_json_file(
            manifest_path,
            limits=_MANIFEST_JSON_LIMITS,
            label="external run manifest",
        )
        payload = document.value
    except StrictJsonError as exc:
        raise ExternalRunnerError(f"invalid run manifest JSON: {exc}") from exc
    if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
        raise ExternalRunnerError("run manifest must be a JSON object")
    expected_keys = {
        "schema",
        "manifest_sha256",
        *ExternalRunManifest.__dataclass_fields__,
    }
    if set(payload) != expected_keys:
        raise ExternalRunnerError("run manifest fields do not match the schema")
    if payload["schema"] != RUNNER_MANIFEST_SCHEMA:
        raise ExternalRunnerError(f"run manifest schema must be {RUNNER_MANIFEST_SCHEMA!r}")
    claimed_manifest_sha = payload["manifest_sha256"]
    if not _is_sha256(claimed_manifest_sha):
        raise ExternalRunnerError("manifest_sha256 must be lowercase SHA-256")
    unsigned = dict(payload)
    unsigned.pop("manifest_sha256")
    try:
        actual_manifest_sha = _canonical_sha256(unsigned)
    except (TypeError, ValueError) as exc:
        raise ExternalRunnerError("run manifest is not canonical finite JSON") from exc
    if actual_manifest_sha != claimed_manifest_sha:
        raise ExternalRunnerError("run manifest SHA-256 mismatch")

    system = payload["system"]
    if not isinstance(system, str) or _SYSTEM_RE.fullmatch(system) is None:
        raise ExternalRunnerError("run manifest system is invalid")
    isolation_mode = payload["isolation_mode"]
    if isolation_mode not in _ISOLATION_MODES:
        raise ExternalRunnerError("run manifest isolation_mode is invalid")
    case_count = payload["case_count"]
    if isinstance(case_count, bool) or not isinstance(case_count, int) or case_count <= 0:
        raise ExternalRunnerError("run manifest case_count is invalid")
    case_runs = _validated_case_runs(payload["case_runs"])
    if isolation_mode == "whole_corpus" and case_runs:
        raise ExternalRunnerError("whole-corpus run manifest cannot contain per-case records")
    if isolation_mode == "per_case" and len(case_runs) > case_count:
        raise ExternalRunnerError("run manifest contains more case runs than the corpus")
    if payload["dataset_sha256"] != expected_dataset_sha256:
        raise ExternalRunnerError("run manifest dataset_sha256 does not match this run")
    if payload["corpus_schema"] != CORPUS_SCHEMA:
        raise ExternalRunnerError("run manifest corpus schema is invalid")
    if payload["candidate_schema"] != CANDIDATE_SCHEMA:
        raise ExternalRunnerError("run manifest candidate schema is invalid")
    for name in (
        "working_directory",
        "started_at",
        "corpus_path",
        "candidate_path",
        "python_version",
        "platform",
    ):
        if not isinstance(payload[name], str) or not payload[name]:
            raise ExternalRunnerError(f"run manifest {name} is invalid")
    try:
        started_at = datetime.fromisoformat(payload["started_at"])
    except ValueError as exc:
        raise ExternalRunnerError("run manifest started_at is invalid") from exc
    if started_at.utcoffset() is None:
        raise ExternalRunnerError("run manifest started_at must include a timezone")
    for name in ("working_directory", "corpus_path", "candidate_path"):
        if not Path(payload[name]).is_absolute():
            raise ExternalRunnerError(f"run manifest {name} must be absolute")
    corpus_evidence_path = Path(payload["corpus_path"]).expanduser().resolve()
    duration = payload["duration_seconds"]
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not 0 <= float(duration) < float("inf")
    ):
        raise ExternalRunnerError("run manifest duration_seconds is invalid")
    for name in (
        "process_succeeded",
        "candidate_valid",
        "ready_for_scoring",
        "claim_metadata_complete",
        "memory_limit_enforced",
    ):
        if not isinstance(payload[name], bool):
            raise ExternalRunnerError(f"run manifest {name} must be boolean")
    ready = payload["ready_for_scoring"]
    if ready != (payload["process_succeeded"] and payload["candidate_valid"]):
        raise ExternalRunnerError("run manifest readiness flags are inconsistent")
    command = payload["command"]
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) and part for part in command)
    ):
        raise ExternalRunnerError("run manifest command is invalid")
    command_contract = payload["command_contract"]
    if (
        not isinstance(command_contract, list)
        or not command_contract
        or not all(
            isinstance(part, str) and part
            for part in command_contract
        )
        or
        not _is_sha256(payload["command_sha256"])
        or payload["command_sha256"]
        != _canonical_sha256(command_contract)
    ):
        raise ExternalRunnerError(
            "run manifest command contract or SHA-256 is invalid"
        )
    try:
        limits_payload = payload["limits"]
        if not isinstance(limits_payload, dict):
            raise TypeError("limits must be an object")
        if set(limits_payload) != set(RunnerLimits.__dataclass_fields__):
            raise TypeError("limits fields do not match the schema")
        decoded_limits = RunnerLimits(**limits_payload)
    except (TypeError, ValueError) as exc:
        raise ExternalRunnerError(f"run manifest limits are invalid: {exc}") from exc
    if (
        payload["memory_limit_enforced"]
        and decoded_limits.max_memory_mb is None
    ):
        raise ExternalRunnerError("run manifest memory-limit flag is inconsistent")
    if (
        payload["process_succeeded"]
        and decoded_limits.max_memory_mb is not None
        and not payload["memory_limit_enforced"]
    ):
        raise ExternalRunnerError("run manifest memory-limit flag is inconsistent")
    try:
        identity_payload = payload["identity"]
        if not isinstance(identity_payload, dict):
            raise TypeError("identity must be an object")
        if set(identity_payload) != set(RunnerIdentity.__dataclass_fields__):
            raise TypeError("identity fields do not match the schema")
        decoded_identity = RunnerIdentity(**identity_payload)
    except (TypeError, ValueError) as exc:
        raise ExternalRunnerError(f"run manifest identity is invalid: {exc}") from exc
    try:
        dependency_payload = payload["dependency_lock"]
        if not isinstance(dependency_payload, dict):
            raise TypeError("dependency_lock must be an object")
        if set(dependency_payload) != set(DependencyLockEvidence.__dataclass_fields__):
            raise TypeError("dependency_lock fields do not match the schema")
        decoded_dependency_lock = DependencyLockEvidence(**dependency_payload)
    except (TypeError, ValueError) as exc:
        raise ExternalRunnerError(
            f"run manifest dependency-lock evidence is invalid: {exc}"
        ) from exc
    if not _dependency_lock_evidence_matches(decoded_dependency_lock):
        raise ExternalRunnerError(
            "run manifest dependency-lock evidence file does not match"
        )
    try:
        entrypoint_payload = payload["adapter_entrypoint"]
        if not isinstance(entrypoint_payload, dict):
            raise TypeError("adapter_entrypoint must be an object")
        if set(entrypoint_payload) != set(
            AdapterEntrypointEvidence.__dataclass_fields__
        ):
            raise TypeError(
                "adapter_entrypoint fields do not match the schema"
            )
        decoded_adapter_entrypoint = AdapterEntrypointEvidence(
            **entrypoint_payload
        )
    except (TypeError, ValueError) as exc:
        raise ExternalRunnerError(
            f"run manifest adapter entrypoint evidence is invalid: {exc}"
        ) from exc
    if not _adapter_entrypoint_evidence_matches(
        decoded_adapter_entrypoint
    ):
        raise ExternalRunnerError(
            "run manifest adapter entrypoint evidence file does not match"
        )
    if not _command_references_adapter_entrypoint(
        command,
        working_directory=Path(payload["working_directory"]),
        evidence=decoded_adapter_entrypoint,
    ):
        raise ExternalRunnerError(
            "run manifest command does not reference its adapter entrypoint"
        )
    try:
        source_payload = payload["adapter_source"]
        if not isinstance(source_payload, dict):
            raise TypeError("adapter_source must be an object")
        if set(source_payload) != set(
            AdapterSourceEvidence.__dataclass_fields__
        ):
            raise TypeError("adapter_source fields do not match the schema")
        source_file_payloads = source_payload["files"]
        if not isinstance(source_file_payloads, list):
            raise TypeError("adapter_source.files must be a list")
        source_files: list[AdapterSourceFileEvidence] = []
        for index, source_file_payload in enumerate(source_file_payloads):
            if (
                not isinstance(source_file_payload, dict)
                or set(source_file_payload)
                != set(AdapterSourceFileEvidence.__dataclass_fields__)
            ):
                raise TypeError(
                    "adapter_source.files"
                    f"[{index}] fields do not match the schema"
                )
            source_files.append(
                AdapterSourceFileEvidence(**source_file_payload)
            )
        decoded_adapter_source = AdapterSourceEvidence(
            **{
                **source_payload,
                "files": tuple(source_files),
            }
        )
    except (TypeError, ValueError) as exc:
        raise ExternalRunnerError(
            f"run manifest adapter source evidence is invalid: {exc}"
        ) from exc
    if not _adapter_source_evidence_matches(decoded_adapter_source):
        raise ExternalRunnerError(
            "run manifest adapter source evidence tree does not match"
        )
    if not _adapter_source_covers_entrypoint(
        decoded_adapter_source,
        decoded_adapter_entrypoint,
    ):
        raise ExternalRunnerError(
            "run manifest adapter source does not cover its entrypoint"
        )
    try:
        runtime_payload = payload["adapter_runtime"]
        if not isinstance(runtime_payload, dict):
            raise TypeError("adapter_runtime must be an object")
        if set(runtime_payload) != set(
            AdapterRuntimeEvidence.__dataclass_fields__
        ):
            raise TypeError(
                "adapter_runtime fields do not match the schema"
            )
        decoded_adapter_runtime = AdapterRuntimeEvidence(
            **runtime_payload
        )
    except (TypeError, ValueError) as exc:
        raise ExternalRunnerError(
            f"run manifest adapter runtime evidence is invalid: {exc}"
        ) from exc
    if not _adapter_runtime_evidence_matches(decoded_adapter_runtime):
        raise ExternalRunnerError(
            "run manifest adapter runtime executable does not match"
        )
    if not _command_uses_adapter_runtime(
        command,
        decoded_adapter_runtime,
    ):
        raise ExternalRunnerError(
            "run manifest command does not use its adapter runtime"
        )
    observed_command_contract = _portable_command_contract(
        command,
        working_directory=Path(payload["working_directory"]),
        system=system,
        corpus_path=Path(payload["corpus_path"]),
        candidate_path=Path(payload["candidate_path"]),
        case_id=None,
        adapter_entrypoint=decoded_adapter_entrypoint,
        adapter_source=decoded_adapter_source,
        adapter_runtime=decoded_adapter_runtime,
    )
    if observed_command_contract != tuple(command_contract):
        raise ExternalRunnerError(
            "run manifest command contract does not match its command"
        )
    if isolation_mode == "per_case":
        for index, record in enumerate(case_runs):
            case_contract = _portable_command_contract(
                record["command"],
                working_directory=Path(payload["working_directory"]),
                system=system,
                corpus_path=Path(record["corpus_path"]),
                candidate_path=Path(record["candidate_path"]),
                case_id=str(record["case_id"]),
                adapter_entrypoint=decoded_adapter_entrypoint,
                adapter_source=decoded_adapter_source,
                adapter_runtime=decoded_adapter_runtime,
            )
            if case_contract != observed_command_contract:
                raise ExternalRunnerError(
                    "run manifest case_runs"
                    f"[{index}] command does not match the command contract"
                )
    try:
        environment_payload = payload["process_environment"]
        if not isinstance(environment_payload, dict):
            raise TypeError("process_environment must be an object")
        if set(environment_payload) != set(
            ProcessEnvironmentEvidence.__dataclass_fields__
        ):
            raise TypeError(
                "process_environment fields do not match the schema"
            )
        variable_names = environment_payload["variable_names"]
        if not isinstance(variable_names, list):
            raise TypeError(
                "process_environment.variable_names must be an array"
            )
        decoded_process_environment = ProcessEnvironmentEvidence(
            **{
                **environment_payload,
                "variable_names": tuple(variable_names),
            }
        )
    except (TypeError, ValueError) as exc:
        raise ExternalRunnerError(
            f"run manifest process environment evidence is invalid: {exc}"
        ) from exc
    try:
        network_payload = payload["network_isolation"]
        if not isinstance(network_payload, dict):
            raise TypeError("network_isolation must be an object")
        if set(network_payload) != set(NetworkIsolationEvidence.__dataclass_fields__):
            raise TypeError("network_isolation fields do not match the schema")
        decoded_network_isolation = NetworkIsolationEvidence(**network_payload)
    except (TypeError, ValueError) as exc:
        raise ExternalRunnerError(
            f"run manifest network-isolation evidence is invalid: {exc}"
        ) from exc
    if not _network_isolation_evidence_matches(decoded_network_isolation):
        raise ExternalRunnerError(
            "run manifest network-isolation evidence file does not match"
        )
    try:
        inference_payload = payload["inference_service"]
        if not isinstance(inference_payload, dict):
            raise TypeError("inference_service must be an object")
        if set(inference_payload) != set(
            InferenceServiceAccounting.__dataclass_fields__
        ):
            raise TypeError("inference_service fields do not match the schema")
        decoded_inference_service = InferenceServiceAccounting(
            **inference_payload
        )
    except (TypeError, ValueError) as exc:
        raise ExternalRunnerError(
            f"run manifest inference-service accounting is invalid: {exc}"
        ) from exc
    expected_claim_controls = _claim_controls_complete(
        decoded_identity,
        decoded_limits,
        isolation_mode,
        Path(payload["working_directory"]),
        command_contract,
        decoded_dependency_lock,
        decoded_adapter_entrypoint,
        decoded_adapter_source,
        decoded_adapter_runtime,
        decoded_process_environment,
        decoded_network_isolation,
        decoded_inference_service,
        2 * case_count if isolation_mode == "per_case" else 2,
    )
    if payload["claim_metadata_complete"] != expected_claim_controls:
        raise ExternalRunnerError("run manifest claim-control completeness flag is inconsistent")
    for name in ("stdout_sha256", "stderr_sha256", "corpus_sha256", "corpus_file_sha256"):
        if not _is_sha256(payload[name]):
            raise ExternalRunnerError(f"run manifest {name} is invalid")
    (
        corpus_config,
        corpus_cases,
        corpus_dataset_sha256,
        corpus_payload,
        corpus_file_sha256,
    ) = _load_corpus(corpus_evidence_path)
    if corpus_dataset_sha256 != expected_dataset_sha256:
        raise ExternalRunnerError("manifest corpus dataset_sha256 does not match this run")
    if corpus_payload["corpus_sha256"] != payload["corpus_sha256"]:
        raise ExternalRunnerError("manifest corpus SHA-256 mismatch")
    if corpus_file_sha256 != payload["corpus_file_sha256"]:
        raise ExternalRunnerError("manifest corpus file SHA-256 mismatch")
    if len(corpus_cases) != case_count:
        raise ExternalRunnerError("run manifest case_count does not match the retained corpus")
    if (
        not _bounded_file_matches(
            corpus_evidence_path,
            corpus_file_sha256,
            max_bytes=_CORPUS_JSON_LIMITS.max_bytes,
            label="manifest corpus",
        )
    ):
        raise ExternalRunnerError("manifest corpus changed while its evidence was validated")
    if isolation_mode == "per_case":
        raw_cases = corpus_payload["cases"]
        expected_case_ids = [
            str(raw_case["case_id"])
            for raw_case in raw_cases[: len(case_runs)]
        ]
        observed_case_ids = [
            str(record["case_id"]) for record in case_runs
        ]
        if observed_case_ids != expected_case_ids:
            raise ExternalRunnerError(
                "run manifest case audit is not the ordered corpus prefix"
            )
        final_candidate_parent = Path(
            payload["candidate_path"]
        ).resolve().parent
        observed_case_directories: set[Path] = set()
        for index, (record, raw_case) in enumerate(
            zip(case_runs, raw_cases, strict=False)
        ):
            case_corpus_path = Path(record["corpus_path"]).resolve()
            case_candidate_path = Path(
                record["candidate_path"]
            ).resolve()
            case_directory = case_corpus_path.parent
            if (
                case_corpus_path.name != "corpus.json"
                or case_candidate_path.name != "candidate.json"
                or case_candidate_path.parent != case_directory
                or case_directory.parent != final_candidate_parent
                or not case_directory.name.startswith(
                    f".lrcbench-case-{index:06d}-"
                )
                or case_directory in observed_case_directories
            ):
                raise ExternalRunnerError(
                    "run manifest case audit paths are inconsistent"
                )
            observed_case_directories.add(case_directory)
            expected_case_document = _single_case_corpus_document(
                corpus_payload,
                raw_case,
            )
            expected_case_bytes = _serialized_corpus_document(
                expected_case_document
            ).encode("utf-8")
            if (
                record["corpus_sha256"]
                != expected_case_document["corpus_sha256"]
                or record["corpus_file_sha256"]
                != hashlib.sha256(expected_case_bytes).hexdigest()
            ):
                raise ExternalRunnerError(
                    "run manifest case audit corpus evidence is inconsistent"
                )
    for name in ("stdout_bytes", "stderr_bytes"):
        value = payload[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ExternalRunnerError(f"run manifest {name} is invalid")
    for name in ("termination_reason", "validation_error"):
        value = payload[name]
        if value is not None and (not isinstance(value, str) or not value):
            raise ExternalRunnerError(f"run manifest {name} is invalid")
    if payload["process_succeeded"] and payload["termination_reason"] is not None:
        raise ExternalRunnerError("successful run has a termination reason")
    candidate_bytes_value = payload["candidate_bytes"]
    if candidate_bytes_value is not None and (
        isinstance(candidate_bytes_value, bool)
        or not isinstance(candidate_bytes_value, int)
        or candidate_bytes_value < 0
    ):
        raise ExternalRunnerError("run manifest candidate_bytes is invalid")
    candidate_sha_value = payload["candidate_sha256"]
    if candidate_sha_value is not None and not _is_sha256(candidate_sha_value):
        raise ExternalRunnerError("run manifest candidate_sha256 is invalid")
    exit_code = payload["exit_code"]
    if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int)):
        raise ExternalRunnerError("run manifest exit_code is invalid")
    if payload["candidate_valid"] and not payload["process_succeeded"]:
        raise ExternalRunnerError("valid candidate came from a failed run")
    if isolation_mode == "per_case":
        all_cases_executed = len(case_runs) == case_count
        expected_process_succeeded = (
            all_cases_executed
            and payload["termination_reason"] is None
            and all(record["process_succeeded"] for record in case_runs)
        )
        if payload["process_succeeded"] != expected_process_succeeded:
            raise ExternalRunnerError("run manifest per-case process status is inconsistent")
        expected_candidate_valid = expected_process_succeeded and all(
            record["candidate_valid"] for record in case_runs
        )
        if payload["candidate_valid"] != expected_candidate_valid:
            raise ExternalRunnerError("run manifest per-case candidate status is inconsistent")
        for stream in ("stdout", "stderr"):
            expected_bytes = sum(int(record[f"{stream}_bytes"]) for record in case_runs)
            if payload[f"{stream}_bytes"] != expected_bytes:
                raise ExternalRunnerError(
                    f"run manifest aggregate {stream} byte count is inconsistent"
                )
            if payload[f"{stream}_sha256"] != _case_stream_sha256(
                case_runs,
                stream,
            ):
                raise ExternalRunnerError(f"run manifest aggregate {stream} digest is inconsistent")

    candidate_path: Path | None = None
    failure_reason: str | None = None
    if ready:
        raw_candidate_path = payload["candidate_path"]
        if not isinstance(raw_candidate_path, str) or not raw_candidate_path:
            raise ExternalRunnerError("run manifest candidate_path is invalid")
        candidate_path = Path(raw_candidate_path).expanduser().resolve()
        candidate_bytes = payload["candidate_bytes"]
        candidate_sha256 = payload["candidate_sha256"]
        if (
            isinstance(candidate_bytes, bool)
            or not isinstance(candidate_bytes, int)
            or candidate_bytes < 0
        ):
            raise ExternalRunnerError("manifest candidate byte count is invalid")
        if not _is_sha256(candidate_sha256):
            raise ExternalRunnerError("manifest candidate SHA-256 mismatch")
        try:
            candidate_file = load_strict_json_file(
                candidate_path,
                limits=_candidate_json_limits(
                    decoded_limits.max_candidate_bytes,
                ),
                label="manifest candidate",
            )
        except StrictJsonError as exc:
            raise ExternalRunnerError(
                f"manifest candidate could not be validated: {candidate_path}"
            ) from exc
        if candidate_file.byte_count != candidate_bytes:
            raise ExternalRunnerError("manifest candidate byte count is invalid")
        if candidate_file.file_sha256 != candidate_sha256:
            raise ExternalRunnerError("manifest candidate SHA-256 mismatch")
        try:
            candidate_system, _candidate_outputs = decode_external_candidate(
                candidate_file.value,
                cases=corpus_cases,
                dataset_sha256=expected_dataset_sha256,
                token_budget=corpus_config.token_budget,
                source_label=str(candidate_path),
                expected_producer=decoded_identity.to_candidate_producer(),
            )
        except ExternalBaselineError as exc:
            raise ExternalRunnerError(
                f"manifest candidate payload is invalid: {exc}"
            ) from exc
        if candidate_system != system:
            raise ExternalRunnerError(
                "manifest candidate system does not match the registered system"
            )
        if isolation_mode == "per_case":
            candidate_payload = candidate_file.value
            candidate_cases = candidate_payload["cases"]
            producer = decoded_identity.to_candidate_producer()
            for index, (record, raw_candidate_case) in enumerate(
                zip(case_runs, candidate_cases, strict=True)
            ):
                expected_case_payload_sha256 = candidate_document(
                    dataset_sha256=expected_dataset_sha256,
                    system=system,
                    cases=[raw_candidate_case],
                    producer=producer,
                )["candidate_payload_sha256"]
                if (
                    record["candidate_payload_sha256"]
                    != expected_case_payload_sha256
                ):
                    raise ExternalRunnerError(
                        "run manifest case_runs"
                        f"[{index}] candidate payload evidence is inconsistent"
                    )
        if not expected_claim_controls:
            failure_reason = (
                "run manifest lacks complete claim controls: exact-Qwen identity, "
                "per-case isolation, an enforced memory limit, retained "
                "dependency-lock, adapter-entrypoint, adapter-source, and "
                "runtime bytes, a portable command contract rooted at the "
                "source directory, a bounded name-audited process environment, "
                "network-isolation evidence, and measured inference-service "
                "accounting are required"
            )
    else:
        failure_reason = (
            payload["termination_reason"]
            or payload["validation_error"]
            or "adapter process did not produce a valid candidate"
        )
    return ExternalRunReference(
        system=system,
        candidate_path=candidate_path,
        candidate_sha256=(
            payload["candidate_sha256"] if candidate_path is not None else None
        ),
        candidate_bytes=(
            payload["candidate_bytes"] if candidate_path is not None else None
        ),
        failure_reason=failure_reason,
        manifest_sha256=claimed_manifest_sha,
        isolation_mode=isolation_mode,
        limits=decoded_limits,
        identity=decoded_identity,
        dependency_lock=decoded_dependency_lock,
        adapter_entrypoint=decoded_adapter_entrypoint,
        adapter_source=decoded_adapter_source,
        adapter_runtime=decoded_adapter_runtime,
        process_environment=decoded_process_environment,
        network_isolation=decoded_network_isolation,
        inference_service=decoded_inference_service,
        command_sha256=payload["command_sha256"],
        adapter_revision=decoded_identity.adapter_revision,
        environment_id=decoded_identity.environment_id,
        model_id=decoded_identity.model_id,
        model_service_cost_usd=decoded_identity.model_service_cost_usd,
    )


def run_external_command(
    command: Sequence[str],
    *,
    system: str,
    corpus_path: Path | str,
    candidate_path: Path | str,
    case_id: str | None = None,
    limits: RunnerLimits | None = None,
    identity: RunnerIdentity | None = None,
    dependency_lock: DependencyLockEvidence | None = None,
    adapter_entrypoint: AdapterEntrypointEvidence | None = None,
    adapter_source: AdapterSourceEvidence | None = None,
    adapter_runtime: AdapterRuntimeEvidence | None = None,
    network_isolation: NetworkIsolationEvidence | None = None,
    inference_service: InferenceServiceContract | None = None,
    working_directory: Path | str | None = None,
    environment: Mapping[str, str] | None = None,
    _inference_monitor: _InferenceServiceMonitor | None = None,
    _check_adapter_entrypoint: bool = True,
    _check_adapter_source: bool = True,
    _check_adapter_runtime: bool = True,
) -> ExternalRunManifest:
    """Run one adapter command and validate its candidate output fail-closed."""

    if not isinstance(system, str) or _SYSTEM_RE.fullmatch(system) is None:
        raise ExternalRunnerError("system must be a valid LRCBench identifier")
    if case_id is not None and (not isinstance(case_id, str) or not case_id):
        raise ExternalRunnerError("case_id must be a non-empty string or None")
    if case_id is None and any(isinstance(part, str) and "{case_id}" in part for part in command):
        raise ExternalRunnerError("{case_id} can only be used by the per-case isolation mode")
    limits = limits or RunnerLimits()
    identity = identity or RunnerIdentity()
    dependency_lock = dependency_lock or DependencyLockEvidence()
    if not _dependency_lock_evidence_matches(dependency_lock):
        raise ExternalRunnerError(
            "dependency-lock evidence file does not match before execution"
        )
    adapter_entrypoint = (
        adapter_entrypoint or AdapterEntrypointEvidence()
    )
    if (
        _check_adapter_entrypoint
        and not _adapter_entrypoint_evidence_matches(adapter_entrypoint)
    ):
        raise ExternalRunnerError(
            "adapter entrypoint evidence file does not match before execution"
        )
    adapter_source = adapter_source or AdapterSourceEvidence()
    if (
        _check_adapter_source
        and not _adapter_source_evidence_matches(adapter_source)
    ):
        raise ExternalRunnerError(
            "adapter source evidence tree does not match before execution"
        )
    if not _adapter_source_covers_entrypoint(
        adapter_source,
        adapter_entrypoint,
    ):
        raise ExternalRunnerError(
            "adapter source evidence does not cover its retained entrypoint"
        )
    network_isolation = network_isolation or NetworkIsolationEvidence()
    if not _network_isolation_evidence_matches(network_isolation):
        raise ExternalRunnerError(
            "network-isolation evidence file does not match before execution"
        )
    inference_service = inference_service or InferenceServiceContract()
    if _inference_monitor is None:
        inference_monitor = _InferenceServiceMonitor(inference_service)
        inference_preflight = inference_monitor.sample(
            check_executable=True
        )
        if inference_preflight is not None:
            raise ExternalRunnerError(
                "inference-service contract failed before execution: "
                f"{inference_preflight}"
            )
        check_executable_after = True
    else:
        if _inference_monitor.contract != inference_service:
            raise ExternalRunnerError(
                "shared inference-service monitor contract does not match"
            )
        inference_monitor = _inference_monitor
        check_executable_after = False
    corpus = Path(corpus_path).expanduser().resolve()
    candidate = Path(candidate_path).expanduser().resolve()
    if candidate.exists():
        raise ExternalRunnerError(
            f"candidate output already exists; refusing to overwrite: {candidate}"
        )
    if not candidate.parent.is_dir():
        raise ExternalRunnerError(f"candidate output directory does not exist: {candidate.parent}")
    (
        config,
        cases,
        dataset_sha256,
        corpus_payload,
        corpus_file_sha256,
    ) = _load_corpus(corpus)
    corpus_sha256 = corpus_payload["corpus_sha256"]

    cwd = (
        Path(working_directory).expanduser().resolve()
        if working_directory is not None
        else Path(adapter_source.source_root or "")
        if adapter_source.claim_evidence_complete
        else Path.cwd().resolve()
    )
    if not cwd.is_dir():
        raise ExternalRunnerError(f"working directory does not exist: {cwd}")
    substituted = tuple(
        part.replace("{corpus}", str(corpus))
        .replace("{candidate}", str(candidate))
        .replace("{system}", system)
        .replace("{case_id}", case_id or "")
        for part in command
    )
    resolved_command = _resolve_command(substituted)
    adapter_runtime = adapter_runtime or capture_adapter_runtime_evidence(
        resolved_command[0]
    )
    if (
        _check_adapter_runtime
        and not _adapter_runtime_evidence_matches(adapter_runtime)
    ):
        raise ExternalRunnerError(
            "adapter runtime executable does not match before execution"
        )
    if not _command_uses_adapter_runtime(
        resolved_command,
        adapter_runtime,
    ):
        raise ExternalRunnerError(
            "adapter command does not use its retained runtime"
        )
    if not _command_references_adapter_entrypoint(
        resolved_command,
        working_directory=cwd,
        evidence=adapter_entrypoint,
    ):
        raise ExternalRunnerError(
            "adapter command does not reference its retained entrypoint"
        )
    command_contract = _portable_command_contract(
        resolved_command,
        working_directory=cwd,
        system=system,
        corpus_path=corpus,
        candidate_path=candidate,
        case_id=case_id,
        adapter_entrypoint=adapter_entrypoint,
        adapter_source=adapter_source,
        adapter_runtime=adapter_runtime,
    )
    command_sha256 = _canonical_sha256(list(command_contract))
    (
        process_environment,
        process_environment_evidence,
    ) = _prepare_process_environment(environment)

    started_at = datetime.now(UTC).isoformat()
    started = time.monotonic()
    exit_code: int | None = None
    termination_reason: str | None = None
    validation_error: str | None = None
    windows_process_control = _uses_windows_process_control()
    creation_flags = (
        subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        | subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        if windows_process_control
        else 0
    )
    windows_job = (
        _WindowsJob.create(limits.max_memory_mb)
        if windows_process_control
        else None
    )
    if windows_job is not None:
        creation_flags |= _WINDOWS_CREATE_SUSPENDED
    process: subprocess.Popen[bytes] | None = None
    posix_group_cleanup_attempted = False
    posix_group_cleanup_failed = False
    try:
        with _runner_temporary_directory(
            prefix=".lrcbench-run-",
            directory=candidate.parent,
        ) as temporary_directory:
            stdout_path = temporary_directory / "stdout.bin"
            stderr_path = temporary_directory / "stderr.bin"
            with (
                stdout_path.open("xb") as stdout,
                stdout_path.open("rb") as stdout_reader,
                stderr_path.open("xb") as stderr,
                stderr_path.open("rb") as stderr_reader,
            ):
                try:
                    try:
                        with _adapter_process_launch_context(
                            resolved_command,
                            limits,
                            environment=process_environment,
                            directory=temporary_directory,
                        ) as (
                            launch_command,
                            preexec_fn,
                            launch_environment,
                            pass_fds,
                        ):
                            popen_options: dict[str, Any] = {}
                            if pass_fds:
                                popen_options["pass_fds"] = pass_fds
                            process = subprocess.Popen(
                                launch_command,
                                cwd=cwd,
                                env=launch_environment,
                                stdin=subprocess.DEVNULL,
                                stdout=stdout,
                                stderr=stderr,
                                shell=False,
                                close_fds=True,
                                start_new_session=not windows_process_control,
                                creationflags=creation_flags,
                                preexec_fn=preexec_fn,
                                **popen_options,
                            )
                    except (OSError, subprocess.SubprocessError) as exc:
                        raise ExternalRunnerError(
                            f"could not start adapter process: {type(exc).__name__}"
                        ) from exc

                    if windows_job is not None:
                        try:
                            windows_job.assign(process)
                            if not windows_job.contains(process):
                                raise ExternalRunnerError(
                                    "adapter was not assigned to its Windows Job Object"
                                )
                            _resume_windows_process(process)
                        except ExternalRunnerError:
                            _terminate_process_tree(process, windows_job)
                            process.wait(timeout=1)
                            raise

                    while True:
                        if windows_process_control:
                            process_exited = process.poll() is not None
                        else:
                            try:
                                process_exited = (
                                    _posix_process_exited_without_reaping(
                                        process
                                    )
                                )
                            except ExternalRunnerError as exc:
                                termination_reason = (
                                    "process_group_cleanup_failed"
                                )
                                validation_error = str(exc)
                                posix_group_cleanup_attempted = True
                                posix_group_cleanup_failed = True
                                _best_effort_stop_and_reap_direct_process(
                                    process
                                )
                                exit_code = process.returncode
                                break
                        if process_exited:
                            break
                        elapsed = time.monotonic() - started
                        service_failure = inference_monitor.sample()
                        if service_failure is not None:
                            termination_reason = service_failure
                        elif elapsed > limits.timeout_seconds:
                            termination_reason = "timeout"
                        elif (
                            _stream_size(stdout, label="stdout")
                            > limits.max_stdout_bytes
                        ):
                            termination_reason = "stdout_limit"
                        elif (
                            _stream_size(stderr, label="stderr")
                            > limits.max_stderr_bytes
                        ):
                            termination_reason = "stderr_limit"
                        elif _size(candidate) > limits.max_candidate_bytes:
                            termination_reason = "candidate_limit"
                        if termination_reason is not None:
                            if windows_process_control:
                                _terminate_process_tree(process, windows_job)
                            break
                        time.sleep(float(limits.poll_interval_seconds))
                    if not windows_process_control:
                        if not posix_group_cleanup_failed:
                            posix_group_cleanup_attempted = True
                            try:
                                exit_code = (
                                    _cleanup_and_reap_posix_process(process)
                                )
                            except ExternalRunnerError as exc:
                                termination_reason = (
                                    "process_group_cleanup_failed"
                                )
                                validation_error = str(exc)
                                posix_group_cleanup_failed = True
                                _best_effort_stop_and_reap_direct_process(
                                    process
                                )
                                exit_code = process.returncode
                    else:
                        try:
                            exit_code = process.wait(timeout=1)
                        except subprocess.TimeoutExpired:
                            _terminate_process_tree(process, windows_job)
                            exit_code = process.wait(timeout=1)
                    service_failure = inference_monitor.sample(
                        check_executable=check_executable_after
                    )
                    if (
                        termination_reason is None
                        and service_failure is not None
                    ):
                        termination_reason = service_failure
                    stdout.flush()
                    stderr.flush()
                finally:
                    if process is not None:
                        if windows_process_control:
                            if process.poll() is None:
                                _terminate_process_tree(process, windows_job)
                                try:
                                    process.wait(timeout=1)
                                except subprocess.TimeoutExpired:
                                    process.kill()
                                    process.wait(timeout=1)
                        elif process.returncode is None:
                            if posix_group_cleanup_failed:
                                _best_effort_stop_and_reap_direct_process(
                                    process
                                )
                            elif not posix_group_cleanup_attempted:
                                posix_group_cleanup_attempted = True
                                _cleanup_and_reap_posix_process(process)
                            else:
                                # A prior fail-closed group cleanup raised
                                # before reaping. Best-effort cleanup of the
                                # direct child neither establishes descendant
                                # cleanup nor masks the original error; never
                                # reuse its numeric PGID.
                                _best_effort_stop_and_reap_direct_process(
                                    process
                                )
                        elif not posix_group_cleanup_attempted:
                            raise ExternalRunnerError(
                                "adapter process was reaped before "
                                "process-group cleanup"
                            )
                    if windows_job is not None:
                        try:
                            windows_job.terminate()
                        finally:
                            windows_job.close()
                            windows_job = None

                (
                    stdout_observed_bytes,
                    stdout_bytes,
                    stdout_sha256,
                ) = _bounded_stream_snapshot(
                    stdout_reader,
                    max_bytes=limits.max_stdout_bytes,
                    label="stdout",
                )
                (
                    stderr_observed_bytes,
                    stderr_bytes,
                    stderr_sha256,
                ) = _bounded_stream_snapshot(
                    stderr_reader,
                    max_bytes=limits.max_stderr_bytes,
                    label="stderr",
                )
    finally:
        if windows_job is not None:
            windows_job.close()

    duration = time.monotonic() - started
    inference_accounting = inference_monitor.accounting()
    candidate_bytes = _size(candidate) if candidate.exists() else None
    if termination_reason is None and (
        not _bounded_file_matches(
            corpus,
            corpus_file_sha256,
            max_bytes=_CORPUS_JSON_LIMITS.max_bytes,
            label="benchmark corpus",
        )
    ):
        termination_reason = "corpus_modified"
    if (
        termination_reason is None
        and not _dependency_lock_evidence_matches(dependency_lock)
    ):
        termination_reason = "dependency_lock_evidence_modified"
    if (
        termination_reason is None
        and _check_adapter_entrypoint
        and not _adapter_entrypoint_evidence_matches(adapter_entrypoint)
    ):
        termination_reason = "adapter_entrypoint_evidence_modified"
    if (
        termination_reason is None
        and _check_adapter_source
        and not _adapter_source_evidence_matches(adapter_source)
    ):
        termination_reason = "adapter_source_evidence_modified"
    if (
        termination_reason is None
        and _check_adapter_runtime
        and not _adapter_runtime_evidence_matches(adapter_runtime)
    ):
        termination_reason = "adapter_runtime_evidence_modified"
    if (
        termination_reason is None
        and not _network_isolation_evidence_matches(network_isolation)
    ):
        termination_reason = "network_isolation_evidence_modified"
    if (
        termination_reason is None
        and stdout_observed_bytes > limits.max_stdout_bytes
    ):
        termination_reason = "stdout_limit"
    if (
        termination_reason is None
        and stderr_observed_bytes > limits.max_stderr_bytes
    ):
        termination_reason = "stderr_limit"
    if (
        termination_reason is None
        and candidate_bytes is not None
        and candidate_bytes > limits.max_candidate_bytes
    ):
        termination_reason = "candidate_limit"
    candidate_sha256: str | None = None
    if (
        candidate.is_file()
        and candidate_bytes is not None
        and candidate_bytes <= limits.max_candidate_bytes
    ):
        try:
            candidate_sha256 = _bounded_file_sha256(
                candidate,
                max_bytes=limits.max_candidate_bytes,
                label="external candidate",
            )
        except ExternalRunnerError:
            termination_reason = termination_reason or "candidate_limit"
    process_succeeded = exit_code == 0 and termination_reason is None
    candidate_valid = False
    if process_succeeded:
        if not candidate.is_file():
            validation_error = "adapter did not create the candidate output"
        else:
            try:
                candidate_file = load_strict_json_file(
                    candidate,
                    limits=_candidate_json_limits(limits.max_candidate_bytes),
                    label="external candidate",
                )
                if (
                    candidate_file.file_sha256 != candidate_sha256
                    or candidate_file.byte_count != candidate_bytes
                ):
                    raise ExternalBaselineError(
                        "candidate changed while it was being validated"
                    )
                candidate_payload = candidate_file.value
                producer = identity.to_candidate_producer()
                candidate_system, _outputs = decode_external_candidate(
                    candidate_payload,
                    cases=cases,
                    dataset_sha256=dataset_sha256,
                    token_budget=config.token_budget,
                    source_label=str(candidate),
                    expected_producer=producer,
                    allow_legacy_adapter=True,
                )
                if candidate_system != system:
                    raise ExternalBaselineError(
                        f"candidate system {candidate_system!r} does not match "
                        f"registered system {system!r}"
                    )
                if candidate_payload["schema"] == LEGACY_ADAPTER_CANDIDATE_SCHEMA:
                    normalized_payload = candidate_document(
                        dataset_sha256=dataset_sha256,
                        system=system,
                        cases=candidate_payload["cases"],
                        producer=producer,
                    )
                    normalized_text = (
                        json.dumps(
                            normalized_payload,
                            indent=2,
                            sort_keys=True,
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    normalized_bytes = normalized_text.encode("utf-8")
                    if len(normalized_bytes) > limits.max_candidate_bytes:
                        raise ExternalBaselineError(
                            "candidate producer envelope exceeds the candidate limit"
                        )
                    atomic_write_text(candidate, normalized_text)
                    candidate_file = load_strict_json_file(
                        candidate,
                        limits=_candidate_json_limits(
                            limits.max_candidate_bytes,
                        ),
                        label="normalized external candidate",
                    )
                    if candidate_file.value != normalized_payload:
                        raise ExternalBaselineError(
                            "normalized candidate changed before validation"
                        )
                    candidate_bytes = candidate_file.byte_count
                    candidate_sha256 = candidate_file.file_sha256
            except (
                OSError,
                StrictJsonError,
                ExternalBaselineError,
            ) as exc:
                validation_error = str(exc)
            else:
                candidate_valid = True
    elif termination_reason is None:
        termination_reason = "nonzero_exit"

    ready_for_scoring = process_succeeded and candidate_valid
    return ExternalRunManifest(
        system=system,
        isolation_mode="whole_corpus",
        case_count=len(cases),
        case_runs=(),
        command=resolved_command,
        command_contract=command_contract,
        command_sha256=command_sha256,
        working_directory=str(cwd),
        started_at=started_at,
        duration_seconds=round(duration, 6),
        corpus_path=str(corpus),
        corpus_schema=CORPUS_SCHEMA,
        corpus_sha256=corpus_sha256,
        corpus_file_sha256=corpus_file_sha256,
        dataset_sha256=dataset_sha256,
        candidate_path=str(candidate),
        candidate_schema=CANDIDATE_SCHEMA,
        candidate_sha256=candidate_sha256,
        candidate_bytes=candidate_bytes,
        exit_code=exit_code,
        termination_reason=termination_reason,
        process_succeeded=process_succeeded,
        candidate_valid=candidate_valid,
        ready_for_scoring=ready_for_scoring,
        validation_error=validation_error,
        stdout_bytes=stdout_bytes,
        stdout_sha256=stdout_sha256,
        stderr_bytes=stderr_bytes,
        stderr_sha256=stderr_sha256,
        limits=limits,
        identity=identity,
        dependency_lock=dependency_lock,
        adapter_entrypoint=adapter_entrypoint,
        adapter_source=adapter_source,
        adapter_runtime=adapter_runtime,
        process_environment=process_environment_evidence,
        network_isolation=network_isolation,
        inference_service=inference_accounting,
        claim_metadata_complete=_claim_controls_complete(
            identity,
            limits,
            "whole_corpus",
            cwd,
            command_contract,
            dependency_lock,
            adapter_entrypoint,
            adapter_source,
            adapter_runtime,
            process_environment_evidence,
            network_isolation,
            inference_accounting,
            2,
        ),
        memory_limit_enforced=_memory_limit_attested(
            limits,
            process_succeeded=process_succeeded,
        ),
        python_version=platform.python_version(),
        platform=platform.platform(),
    )


def run_external_cases(
    command: Sequence[str],
    *,
    system: str,
    corpus_path: Path | str,
    candidate_path: Path | str,
    limits: RunnerLimits | None = None,
    identity: RunnerIdentity | None = None,
    dependency_lock: DependencyLockEvidence | None = None,
    adapter_entrypoint: AdapterEntrypointEvidence | None = None,
    adapter_source: AdapterSourceEvidence | None = None,
    adapter_runtime: AdapterRuntimeEvidence | None = None,
    network_isolation: NetworkIsolationEvidence | None = None,
    inference_service: InferenceServiceContract | None = None,
    working_directory: Path | str | None = None,
    environment: Mapping[str, str] | None = None,
) -> ExternalRunManifest:
    """Run every corpus case in a fresh, sequentially bounded process."""

    if not isinstance(system, str) or _SYSTEM_RE.fullmatch(system) is None:
        raise ExternalRunnerError("system must be a valid LRCBench identifier")
    limits = limits or RunnerLimits()
    identity = identity or RunnerIdentity()
    dependency_lock = dependency_lock or DependencyLockEvidence()
    if not _dependency_lock_evidence_matches(dependency_lock):
        raise ExternalRunnerError(
            "dependency-lock evidence file does not match before execution"
        )
    adapter_entrypoint = (
        adapter_entrypoint or AdapterEntrypointEvidence()
    )
    if not _adapter_entrypoint_evidence_matches(adapter_entrypoint):
        raise ExternalRunnerError(
            "adapter entrypoint evidence file does not match before execution"
        )
    adapter_source = adapter_source or AdapterSourceEvidence()
    if not _adapter_source_evidence_matches(adapter_source):
        raise ExternalRunnerError(
            "adapter source evidence tree does not match before execution"
        )
    if not _adapter_source_covers_entrypoint(
        adapter_source,
        adapter_entrypoint,
    ):
        raise ExternalRunnerError(
            "adapter source evidence does not cover its retained entrypoint"
        )
    network_isolation = network_isolation or NetworkIsolationEvidence()
    if not _network_isolation_evidence_matches(network_isolation):
        raise ExternalRunnerError(
            "network-isolation evidence file does not match before execution"
        )
    inference_service = inference_service or InferenceServiceContract()
    inference_monitor = _InferenceServiceMonitor(inference_service)
    inference_preflight = inference_monitor.sample(check_executable=True)
    if inference_preflight is not None:
        raise ExternalRunnerError(
            "inference-service contract failed before execution: "
            f"{inference_preflight}"
        )
    corpus = Path(corpus_path).expanduser().resolve()
    candidate = Path(candidate_path).expanduser().resolve()
    if candidate.exists():
        raise ExternalRunnerError(
            f"candidate output already exists; refusing to overwrite: {candidate}"
        )
    if not candidate.parent.is_dir():
        raise ExternalRunnerError(f"candidate output directory does not exist: {candidate.parent}")
    (
        config,
        cases,
        dataset_sha256,
        corpus_payload,
        corpus_file_sha256,
    ) = _load_corpus(corpus)
    corpus_sha256 = corpus_payload["corpus_sha256"]
    raw_cases = corpus_payload["cases"]

    cwd = (
        Path(working_directory).expanduser().resolve()
        if working_directory is not None
        else Path(adapter_source.source_root or "")
        if adapter_source.claim_evidence_complete
        else Path.cwd().resolve()
    )
    if not cwd.is_dir():
        raise ExternalRunnerError(f"working directory does not exist: {cwd}")
    (
        process_environment,
        process_environment_evidence,
    ) = _prepare_process_environment(environment)
    resolved_template = _resolve_command(command)
    adapter_runtime = adapter_runtime or capture_adapter_runtime_evidence(
        resolved_template[0]
    )
    if not _adapter_runtime_evidence_matches(adapter_runtime):
        raise ExternalRunnerError(
            "adapter runtime executable does not match before execution"
        )
    if not _command_uses_adapter_runtime(
        resolved_template,
        adapter_runtime,
    ):
        raise ExternalRunnerError(
            "adapter command does not use its retained runtime"
        )
    if not _command_references_adapter_entrypoint(
        resolved_template,
        working_directory=cwd,
        evidence=adapter_entrypoint,
    ):
        raise ExternalRunnerError(
            "adapter command does not reference its retained entrypoint"
        )
    command_contract = _portable_command_contract(
        resolved_template,
        working_directory=cwd,
        system=system,
        corpus_path=corpus,
        candidate_path=candidate,
        case_id=None,
        adapter_entrypoint=adapter_entrypoint,
        adapter_source=adapter_source,
        adapter_runtime=adapter_runtime,
    )
    command_sha256 = _canonical_sha256(list(command_contract))

    started_at = datetime.now(UTC).isoformat()
    started = time.monotonic()
    case_runs: list[CaseRunRecord] = []
    candidate_cases: list[dict[str, Any]] = []
    aggregate_stdout_bytes = 0
    aggregate_stderr_bytes = 0
    termination_reason: str | None = None
    validation_error: str | None = None

    for index, (case, raw_case) in enumerate(zip(cases, raw_cases, strict=True)):
        service_failure = inference_monitor.sample()
        if service_failure is not None:
            termination_reason = (
                f"case_failure:{case.id}:{service_failure}"
            )
            break
        with _runner_temporary_directory(
            prefix=f".lrcbench-case-{index:06d}-",
            directory=candidate.parent,
        ) as case_directory_value:
            case_directory = case_directory_value
            case_corpus_path = case_directory / "corpus.json"
            case_candidate_path = case_directory / "candidate.json"
            case_corpus_document = _single_case_corpus_document(
                corpus_payload,
                raw_case,
            )
            atomic_write_text(
                case_corpus_path,
                _serialized_corpus_document(case_corpus_document),
            )
            case_manifest = run_external_command(
                resolved_template,
                system=system,
                corpus_path=case_corpus_path,
                candidate_path=case_candidate_path,
                case_id=case.id,
                limits=limits,
                identity=identity,
                dependency_lock=dependency_lock,
                adapter_entrypoint=adapter_entrypoint,
                adapter_source=adapter_source,
                adapter_runtime=adapter_runtime,
                network_isolation=network_isolation,
                inference_service=inference_service,
                working_directory=cwd,
                environment=process_environment,
                _inference_monitor=inference_monitor,
                _check_adapter_entrypoint=False,
                _check_adapter_source=False,
                _check_adapter_runtime=False,
            )
            if case_manifest.command_contract != command_contract:
                raise ExternalRunnerError(
                    "per-case adapter command diverged from its contract"
                )
            if (
                case_manifest.process_environment
                != process_environment_evidence
            ):
                raise ExternalRunnerError(
                    "per-case adapter environment diverged from its contract"
                )
            adapter_integrity_failure: str | None = None
            if not _adapter_entrypoint_evidence_matches(
                adapter_entrypoint
            ):
                adapter_integrity_failure = (
                    "adapter_entrypoint_evidence_modified"
                )
            elif not _adapter_source_evidence_matches(adapter_source):
                adapter_integrity_failure = (
                    "adapter_source_evidence_modified"
                )
            elif not _adapter_runtime_evidence_matches(adapter_runtime):
                adapter_integrity_failure = (
                    "adapter_runtime_evidence_modified"
                )
            case_candidate_payload: dict[str, Any] | None = None
            case_candidate_payload_sha256: str | None = None
            if (
                adapter_integrity_failure is None
                and case_manifest.ready_for_scoring
            ):
                try:
                    case_candidate_document = load_strict_json_file(
                        case_candidate_path,
                        limits=_candidate_json_limits(
                            limits.max_candidate_bytes
                        ),
                        label="per-case external candidate",
                    )
                    if (
                        case_candidate_document.file_sha256
                        != case_manifest.candidate_sha256
                        or case_candidate_document.byte_count
                        != case_manifest.candidate_bytes
                    ):
                        raise ExternalRunnerError(
                            "validated case output changed before aggregation"
                        )
                    case_candidate_payload = case_candidate_document.value
                    case_candidate_payload_sha256 = (
                        case_candidate_payload[
                            "candidate_payload_sha256"
                        ]
                    )
                except StrictJsonError as exc:
                    raise ExternalRunnerError(
                        f"validated case output could not be reread: {exc}"
                    ) from exc
            case_run = CaseRunRecord(
                case_id=case.id,
                command=case_manifest.command,
                corpus_path=str(case_corpus_path),
                candidate_path=str(case_candidate_path),
                started_at=case_manifest.started_at,
                duration_seconds=case_manifest.duration_seconds,
                corpus_sha256=case_manifest.corpus_sha256,
                corpus_file_sha256=case_manifest.corpus_file_sha256,
                candidate_sha256=case_manifest.candidate_sha256,
                candidate_payload_sha256=(
                    case_candidate_payload_sha256
                ),
                candidate_bytes=case_manifest.candidate_bytes,
                exit_code=case_manifest.exit_code,
                termination_reason=(
                    case_manifest.termination_reason
                    or adapter_integrity_failure
                ),
                process_succeeded=(
                    case_manifest.process_succeeded
                    and adapter_integrity_failure is None
                ),
                candidate_valid=(
                    case_manifest.candidate_valid
                    and adapter_integrity_failure is None
                ),
                validation_error=(
                    case_manifest.validation_error
                    if adapter_integrity_failure is None
                    else "adapter source integrity changed during the case"
                ),
                stdout_bytes=case_manifest.stdout_bytes,
                stdout_sha256=case_manifest.stdout_sha256,
                stderr_bytes=case_manifest.stderr_bytes,
                stderr_sha256=case_manifest.stderr_sha256,
            )
            case_runs.append(case_run)
            aggregate_stdout_bytes += case_run.stdout_bytes
            aggregate_stderr_bytes += case_run.stderr_bytes
            if case_candidate_payload is not None:
                candidate_cases.append(case_candidate_payload["cases"][0])

        if (
            case_manifest.termination_reason
            == "process_group_cleanup_failed"
        ):
            termination_reason = (
                f"case_failure:{case.id}:"
                "process_group_cleanup_failed"
            )
            validation_error = case_manifest.validation_error
            break
        if adapter_integrity_failure is not None:
            termination_reason = (
                f"case_failure:{case.id}:{adapter_integrity_failure}"
            )
            break
        if aggregate_stdout_bytes > limits.max_stdout_bytes:
            termination_reason = "aggregate_stdout_limit"
            break
        if aggregate_stderr_bytes > limits.max_stderr_bytes:
            termination_reason = "aggregate_stderr_limit"
            break
        if (
            case_manifest.termination_reason is not None
            and case_manifest.termination_reason.startswith(
                "inference_service_"
            )
        ):
            termination_reason = (
                f"case_failure:{case.id}:"
                f"{case_manifest.termination_reason}"
            )
            break

    final_service_failure = inference_monitor.sample(
        check_executable=True
    )
    if termination_reason is None and final_service_failure is not None:
        termination_reason = final_service_failure
    if termination_reason is None and (
        not _bounded_file_matches(
            corpus,
            corpus_file_sha256,
            max_bytes=_CORPUS_JSON_LIMITS.max_bytes,
            label="benchmark corpus",
        )
    ):
        termination_reason = "corpus_modified"
    if (
        termination_reason is None
        and not _dependency_lock_evidence_matches(dependency_lock)
    ):
        termination_reason = "dependency_lock_evidence_modified"
    if (
        termination_reason is None
        and not _adapter_entrypoint_evidence_matches(adapter_entrypoint)
    ):
        termination_reason = "adapter_entrypoint_evidence_modified"
    if (
        termination_reason is None
        and not _adapter_source_evidence_matches(adapter_source)
    ):
        termination_reason = "adapter_source_evidence_modified"
    if (
        termination_reason is None
        and not _adapter_runtime_evidence_matches(adapter_runtime)
    ):
        termination_reason = "adapter_runtime_evidence_modified"
    if (
        termination_reason is None
        and not _network_isolation_evidence_matches(network_isolation)
    ):
        termination_reason = "network_isolation_evidence_modified"
    failed_process = next(
        (record for record in case_runs if not record.process_succeeded),
        None,
    )
    if termination_reason is None and failed_process is not None:
        reason = failed_process.termination_reason or "nonzero_exit"
        termination_reason = f"case_failure:{failed_process.case_id}:{reason}"
    invalid_candidate = next(
        (record for record in case_runs if record.process_succeeded and not record.candidate_valid),
        None,
    )
    if invalid_candidate is not None:
        detail = invalid_candidate.validation_error or "candidate validation failed"
        validation_error = f"case {invalid_candidate.case_id!r}: {detail}"

    all_cases_executed = len(case_runs) == len(cases)
    process_succeeded = (
        all_cases_executed
        and termination_reason is None
        and all(record.process_succeeded for record in case_runs)
    )
    candidate_valid = False
    candidate_sha256: str | None = None
    candidate_bytes: int | None = None
    if process_succeeded and invalid_candidate is None and len(candidate_cases) == len(cases):
        candidate_payload = candidate_document(
            dataset_sha256=dataset_sha256,
            system=system,
            cases=candidate_cases,
            producer=identity.to_candidate_producer(),
        )
        try:
            decoded_system, _decoded = decode_external_candidate(
                candidate_payload,
                cases=cases,
                dataset_sha256=dataset_sha256,
                token_budget=config.token_budget,
                source_label=str(candidate),
                expected_producer=identity.to_candidate_producer(),
            )
            if decoded_system != system:
                raise ExternalBaselineError(
                    f"candidate system {decoded_system!r} does not match "
                    f"registered system {system!r}"
                )
        except ExternalBaselineError as exc:
            validation_error = str(exc)
        else:
            candidate_content = (
                json.dumps(
                    candidate_payload,
                    indent=2,
                    sort_keys=True,
                    ensure_ascii=False,
                )
                + "\n"
            ).encode("utf-8")
            candidate_bytes = len(candidate_content)
            if candidate_bytes > limits.max_candidate_bytes:
                termination_reason = "candidate_limit"
                process_succeeded = False
                candidate_bytes = None
            else:
                try:
                    with candidate.open("xb") as stream:
                        stream.write(candidate_content)
                except FileExistsError as exc:
                    raise ExternalRunnerError(
                        "candidate output appeared during the run; refusing to overwrite"
                    ) from exc
                candidate_sha256 = _bounded_file_sha256(
                    candidate,
                    max_bytes=limits.max_candidate_bytes,
                    label="merged external candidate",
                )
                candidate_valid = True

    if all(record.exit_code == 0 for record in case_runs) and all_cases_executed:
        exit_code: int | None = 0
    else:
        exit_code = next(
            (record.exit_code for record in case_runs if record.exit_code != 0),
            None,
        )
    duration = time.monotonic() - started
    inference_accounting = inference_monitor.accounting()
    ready_for_scoring = process_succeeded and candidate_valid
    return ExternalRunManifest(
        system=system,
        isolation_mode="per_case",
        case_count=len(cases),
        case_runs=tuple(case_runs),
        command=resolved_template,
        command_contract=command_contract,
        command_sha256=command_sha256,
        working_directory=str(cwd),
        started_at=started_at,
        duration_seconds=round(duration, 6),
        corpus_path=str(corpus),
        corpus_schema=CORPUS_SCHEMA,
        corpus_sha256=corpus_sha256,
        corpus_file_sha256=corpus_file_sha256,
        dataset_sha256=dataset_sha256,
        candidate_path=str(candidate),
        candidate_schema=CANDIDATE_SCHEMA,
        candidate_sha256=candidate_sha256,
        candidate_bytes=candidate_bytes,
        exit_code=exit_code,
        termination_reason=termination_reason,
        process_succeeded=process_succeeded,
        candidate_valid=candidate_valid,
        ready_for_scoring=ready_for_scoring,
        validation_error=validation_error,
        stdout_bytes=aggregate_stdout_bytes,
        stdout_sha256=_case_stream_sha256(case_runs, "stdout"),
        stderr_bytes=aggregate_stderr_bytes,
        stderr_sha256=_case_stream_sha256(case_runs, "stderr"),
        limits=limits,
        identity=identity,
        dependency_lock=dependency_lock,
        adapter_entrypoint=adapter_entrypoint,
        adapter_source=adapter_source,
        adapter_runtime=adapter_runtime,
        process_environment=process_environment_evidence,
        network_isolation=network_isolation,
        inference_service=inference_accounting,
        claim_metadata_complete=_claim_controls_complete(
            identity,
            limits,
            "per_case",
            cwd,
            command_contract,
            dependency_lock,
            adapter_entrypoint,
            adapter_source,
            adapter_runtime,
            process_environment_evidence,
            network_isolation,
            inference_accounting,
            2 * len(cases),
        ),
        memory_limit_enforced=_memory_limit_attested(
            limits,
            process_succeeded=process_succeeded,
        ),
        python_version=platform.python_version(),
        platform=platform.platform(),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system", required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--candidate-out", type=Path, required=True)
    parser.add_argument("--manifest-out", type=Path, required=True)
    parser.add_argument("--working-directory", type=Path)
    parser.add_argument(
        "--pass-environment",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "add one named host variable to the bounded default adapter "
            "environment; repeat as needed (values are hashed, not retained)"
        ),
    )
    parser.add_argument(
        "--isolation",
        choices=("per-case", "whole-corpus"),
        default="per-case",
        help=(
            "run one fresh bounded process per case (default) or one process "
            "for the complete corpus; whole-corpus runs are diagnostic-only"
        ),
    )
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--max-stdout-bytes", type=int, default=1_000_000)
    parser.add_argument("--max-stderr-bytes", type=int, default=1_000_000)
    parser.add_argument("--max-candidate-bytes", type=int, default=20_000_000)
    parser.add_argument("--max-memory-mb", type=int)
    parser.add_argument(
        "--dependency-lock-evidence",
        type=Path,
        help=(
            "retained dependency lock whose SHA-256 defines --environment-id"
        ),
    )
    parser.add_argument(
        "--adapter-entrypoint-evidence",
        type=Path,
        help=(
            "retained adapter file that must appear in the command and whose "
            "SHA-256 is bound into the run manifest"
        ),
    )
    parser.add_argument(
        "--adapter-source-root",
        type=Path,
        help=(
            "immutable source directory inventoried recursively before and "
            "after execution; it must contain --adapter-entrypoint-evidence"
        ),
    )
    parser.add_argument(
        "--network-isolation-mode",
        choices=tuple(sorted(NETWORK_ISOLATION_MODES)),
        default="unverified",
        help=(
            "externally enforced offline boundary; claim modes also require "
            "--network-isolation-evidence"
        ),
    )
    parser.add_argument(
        "--network-isolation-evidence",
        type=Path,
        help="retained host/container policy artifact hashed into the run manifest",
    )
    parser.add_argument(
        "--inference-service-pid",
        type=int,
        help=(
            "PID of the pre-existing local inference service to identify and "
            "sample during adapter execution"
        ),
    )
    parser.add_argument(
        "--max-inference-service-memory-mb",
        type=int,
        help=(
            "working-set/RSS ceiling for --inference-service-pid; exceeding "
            "it invalidates the run without terminating the service"
        ),
    )
    parser.add_argument("--adapter-revision", default="unrecorded")
    parser.add_argument("--environment-id", default="unrecorded")
    parser.add_argument("--model-id", default="unrecorded")
    parser.add_argument("--model-context-length", type=int, default=0)
    parser.add_argument("--tokenizer-id", default="unrecorded")
    parser.add_argument("--inference-concurrency", type=int, default=0)
    parser.add_argument("--retry-count", type=int, default=0)
    parser.add_argument("--model-service-cost-usd", type=float, default=0.0)
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help=(
            "adapter command after --; {corpus}, {candidate}, {system}, and "
            "{case_id} are replaced as literal argv fields without shell interpretation"
        ),
    )
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if args.manifest_out.expanduser().resolve() == args.candidate_out.expanduser().resolve():
        parser.error("manifest and candidate outputs must be different paths")
    if args.manifest_out.exists():
        parser.error(f"manifest output already exists; refusing to overwrite: {args.manifest_out}")
    if not args.manifest_out.parent.is_dir():
        parser.error(f"manifest output directory does not exist: {args.manifest_out.parent}")
    try:
        dependency_lock = capture_dependency_lock_evidence(
            args.dependency_lock_evidence
        )
        adapter_entrypoint = capture_adapter_entrypoint_evidence(
            args.adapter_entrypoint_evidence
        )
        adapter_source = capture_adapter_source_evidence(
            args.adapter_source_root
        )
        network_isolation = capture_network_isolation_evidence(
            args.network_isolation_mode,
            args.network_isolation_evidence,
        )
        inference_service = capture_inference_service_contract(
            args.inference_service_pid,
            max_memory_mb=args.max_inference_service_memory_mb,
        )
        process_environment = _process_environment_with_pass_through(
            args.pass_environment
        )
        runner = run_external_cases if args.isolation == "per-case" else run_external_command
        manifest = runner(
            command,
            system=args.system,
            corpus_path=args.corpus,
            candidate_path=args.candidate_out,
            working_directory=args.working_directory,
            limits=RunnerLimits(
                timeout_seconds=args.timeout_seconds,
                max_stdout_bytes=args.max_stdout_bytes,
                max_stderr_bytes=args.max_stderr_bytes,
                max_candidate_bytes=args.max_candidate_bytes,
                max_memory_mb=args.max_memory_mb,
            ),
            identity=RunnerIdentity(
                adapter_revision=args.adapter_revision,
                environment_id=args.environment_id,
                model_id=args.model_id,
                model_context_length=args.model_context_length,
                tokenizer_id=args.tokenizer_id,
                inference_concurrency=args.inference_concurrency,
                retry_count=args.retry_count,
                model_service_cost_usd=args.model_service_cost_usd,
            ),
            dependency_lock=dependency_lock,
            adapter_entrypoint=adapter_entrypoint,
            adapter_source=adapter_source,
            network_isolation=network_isolation,
            inference_service=inference_service,
            environment=process_environment,
        )
    except (ExternalRunnerError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    try:
        atomic_write_text(
            args.manifest_out,
            manifest.to_json() + "\n",
            overwrite=False,
        )
    except FileExistsError:
        parser.error(
            f"manifest output already exists; refusing to overwrite: {args.manifest_out}"
        )
    except OSError as exc:
        parser.error(f"could not commit manifest output: {exc}")
    if manifest.ready_for_scoring and manifest.claim_metadata_complete:
        status = "ready for registered scoring"
    elif manifest.ready_for_scoring:
        status = "ready for diagnostic scoring; claim metadata is incomplete"
    else:
        status = "invalid run"
    print(f"{manifest.system}: {status}")
    return 0 if manifest.ready_for_scoring else 2


if __name__ == "__main__":
    raise SystemExit(main())
