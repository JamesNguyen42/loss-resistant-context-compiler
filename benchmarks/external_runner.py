"""Resource-bounded, shell-free runner for LRCBench external adapters."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from context_compiler.atomic import atomic_write_text
from context_compiler.local_qwen import (
    QWEN_Q4_CONTEXT_LENGTH,
    QWEN_Q4_VARIANT,
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

RUNNER_MANIFEST_SCHEMA = "lrcbench-external-run-manifest-0.5"
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
    network_isolation: NetworkIsolationEvidence
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
            or not Path(self.evidence_path).is_absolute()
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
class CaseRunRecord:
    case_id: str
    command: tuple[str, ...]
    started_at: str
    duration_seconds: float
    corpus_sha256: str
    corpus_file_sha256: str
    candidate_sha256: str | None
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
    network_isolation: NetworkIsolationEvidence
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


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


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
    try:
        observed = _bounded_file_evidence(
            Path(evidence.evidence_path or ""),
            max_bytes=_NETWORK_ISOLATION_EVIDENCE_MAX_BYTES,
            label="network isolation evidence",
        )
    except ExternalRunnerError:
        return False
    return (
        observed.file_sha256 == evidence.evidence_sha256
        and observed.byte_count == evidence.evidence_bytes
    )


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


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


def _terminate_posix_process_group(process_group_id: int) -> None:
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            return
        time.sleep(0.01)
    with suppress(ProcessLookupError):
        os.killpg(process_group_id, signal.SIGKILL)


def _terminate_process_tree(
    process: subprocess.Popen[bytes],
    windows_job: _WindowsJob | None = None,
) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
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
    else:
        _terminate_posix_process_group(process.pid)


def _posix_limit_setup(limits: RunnerLimits):
    if os.name == "nt" or limits.max_memory_mb is None:
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


def _claim_controls_complete(
    identity: RunnerIdentity,
    limits: RunnerLimits,
    isolation_mode: str,
    network_isolation: NetworkIsolationEvidence,
) -> bool:
    return (
        identity.claim_metadata_complete
        and isolation_mode == "per_case"
        and limits.max_memory_mb is not None
        and network_isolation.claim_evidence_complete
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
        if raw_record["candidate_valid"] and (candidate_bytes is None or candidate_sha256 is None):
            raise ExternalRunnerError(f"{context} valid candidate lacks file evidence")
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
    if not isinstance(command, list) or not all(isinstance(part, str) and part for part in command):
        raise ExternalRunnerError("run manifest command is invalid")
    try:
        limits_payload = payload["limits"]
        if not isinstance(limits_payload, dict):
            raise TypeError("limits must be an object")
        if set(limits_payload) != set(RunnerLimits.__dataclass_fields__):
            raise TypeError("limits fields do not match the schema")
        decoded_limits = RunnerLimits(**limits_payload)
    except (TypeError, ValueError) as exc:
        raise ExternalRunnerError(f"run manifest limits are invalid: {exc}") from exc
    if payload["memory_limit_enforced"] != (decoded_limits.max_memory_mb is not None):
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
    expected_claim_controls = _claim_controls_complete(
        decoded_identity,
        decoded_limits,
        isolation_mode,
        decoded_network_isolation,
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
        if not expected_claim_controls:
            failure_reason = (
                "run manifest lacks complete claim controls: exact-Qwen identity, "
                "per-case isolation, an enforced memory limit, and retained "
                "network-isolation evidence are required"
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
        network_isolation=decoded_network_isolation,
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
    network_isolation: NetworkIsolationEvidence | None = None,
    working_directory: Path | str | None = None,
    environment: Mapping[str, str] | None = None,
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
    network_isolation = network_isolation or NetworkIsolationEvidence()
    if not _network_isolation_evidence_matches(network_isolation):
        raise ExternalRunnerError(
            "network-isolation evidence file does not match before execution"
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

    cwd = (
        Path(working_directory).expanduser().resolve()
        if working_directory is not None
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
    process_environment = dict(environment) if environment is not None else os.environ.copy()
    if not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in process_environment.items()
    ):
        raise ExternalRunnerError("environment must map strings to strings")

    started_at = datetime.now(UTC).isoformat()
    started = time.monotonic()
    exit_code: int | None = None
    termination_reason: str | None = None
    creation_flags = (
        subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        | subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        if os.name == "nt"
        else 0
    )
    windows_job = _WindowsJob.create(limits.max_memory_mb) if os.name == "nt" else None
    if windows_job is not None:
        creation_flags |= _WINDOWS_CREATE_SUSPENDED
    process: subprocess.Popen[bytes] | None = None
    try:
        with _runner_temporary_directory(
            prefix=".lrcbench-run-",
            directory=candidate.parent,
        ) as temporary_directory:
            stdout_path = temporary_directory / "stdout.bin"
            stderr_path = temporary_directory / "stderr.bin"
            try:
                with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                    try:
                        process = subprocess.Popen(
                            resolved_command,
                            cwd=cwd,
                            env=process_environment,
                            stdin=subprocess.DEVNULL,
                            stdout=stdout,
                            stderr=stderr,
                            shell=False,
                            close_fds=True,
                            start_new_session=os.name != "nt",
                            creationflags=creation_flags,
                            preexec_fn=_posix_limit_setup(limits),
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

                    while process.poll() is None:
                        elapsed = time.monotonic() - started
                        if elapsed > limits.timeout_seconds:
                            termination_reason = "timeout"
                        elif _size(stdout_path) > limits.max_stdout_bytes:
                            termination_reason = "stdout_limit"
                        elif _size(stderr_path) > limits.max_stderr_bytes:
                            termination_reason = "stderr_limit"
                        elif _size(candidate) > limits.max_candidate_bytes:
                            termination_reason = "candidate_limit"
                        if termination_reason is not None:
                            _terminate_process_tree(process, windows_job)
                            break
                        time.sleep(float(limits.poll_interval_seconds))
                    try:
                        exit_code = process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        _terminate_process_tree(process, windows_job)
                        exit_code = process.wait(timeout=1)
                    stdout.flush()
                    stderr.flush()
            finally:
                if process is not None and process.poll() is None:
                    _terminate_process_tree(process, windows_job)
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1)
                if windows_job is not None:
                    try:
                        windows_job.terminate()
                    finally:
                        windows_job.close()
                        windows_job = None
                elif process is not None:
                    _terminate_posix_process_group(process.pid)

            stdout_bytes = _size(stdout_path)
            stderr_bytes = _size(stderr_path)
            stdout_sha256 = _file_sha256(stdout_path)
            stderr_sha256 = _file_sha256(stderr_path)
    finally:
        if windows_job is not None:
            windows_job.close()

    duration = time.monotonic() - started
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
        and not _network_isolation_evidence_matches(network_isolation)
    ):
        termination_reason = "network_isolation_evidence_modified"
    if termination_reason is None and stdout_bytes > limits.max_stdout_bytes:
        termination_reason = "stdout_limit"
    if termination_reason is None and stderr_bytes > limits.max_stderr_bytes:
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
    validation_error: str | None = None
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
        network_isolation=network_isolation,
        claim_metadata_complete=_claim_controls_complete(
            identity,
            limits,
            "whole_corpus",
            network_isolation,
        ),
        memory_limit_enforced=limits.max_memory_mb is not None,
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
    network_isolation: NetworkIsolationEvidence | None = None,
    working_directory: Path | str | None = None,
    environment: Mapping[str, str] | None = None,
) -> ExternalRunManifest:
    """Run every corpus case in a fresh, sequentially bounded process."""

    if not isinstance(system, str) or _SYSTEM_RE.fullmatch(system) is None:
        raise ExternalRunnerError("system must be a valid LRCBench identifier")
    limits = limits or RunnerLimits()
    identity = identity or RunnerIdentity()
    network_isolation = network_isolation or NetworkIsolationEvidence()
    if not _network_isolation_evidence_matches(network_isolation):
        raise ExternalRunnerError(
            "network-isolation evidence file does not match before execution"
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
        else Path.cwd().resolve()
    )
    if not cwd.is_dir():
        raise ExternalRunnerError(f"working directory does not exist: {cwd}")
    process_environment = dict(environment) if environment is not None else os.environ.copy()
    if not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in process_environment.items()
    ):
        raise ExternalRunnerError("environment must map strings to strings")
    resolved_template = _resolve_command(command)

    started_at = datetime.now(UTC).isoformat()
    started = time.monotonic()
    case_runs: list[CaseRunRecord] = []
    candidate_cases: list[dict[str, Any]] = []
    aggregate_stdout_bytes = 0
    aggregate_stderr_bytes = 0
    termination_reason: str | None = None
    validation_error: str | None = None

    for index, (case, raw_case) in enumerate(zip(cases, raw_cases, strict=True)):
        with _runner_temporary_directory(
            prefix=f".lrcbench-case-{index:06d}-",
            directory=candidate.parent,
        ) as case_directory_value:
            case_directory = case_directory_value
            case_corpus_path = case_directory / "corpus.json"
            case_candidate_path = case_directory / "candidate.json"
            case_config = dict(corpus_payload["config"])
            case_config["histories"] = 1
            case_corpus_document: dict[str, Any] = {
                "schema": corpus_payload["schema"],
                "benchmark": corpus_payload["benchmark"],
                "dataset_sha256": dataset_sha256,
                "producer": corpus_payload["producer"],
                "config": case_config,
                "cases": [raw_case],
            }
            case_corpus_document["corpus_sha256"] = _canonical_sha256(case_corpus_document)
            atomic_write_text(
                case_corpus_path,
                json.dumps(
                    case_corpus_document,
                    indent=2,
                    sort_keys=True,
                    ensure_ascii=False,
                )
                + "\n",
            )
            case_manifest = run_external_command(
                resolved_template,
                system=system,
                corpus_path=case_corpus_path,
                candidate_path=case_candidate_path,
                case_id=case.id,
                limits=limits,
                identity=identity,
                network_isolation=network_isolation,
                working_directory=cwd,
                environment=process_environment,
            )
            case_run = CaseRunRecord(
                case_id=case.id,
                command=case_manifest.command,
                started_at=case_manifest.started_at,
                duration_seconds=case_manifest.duration_seconds,
                corpus_sha256=case_manifest.corpus_sha256,
                corpus_file_sha256=case_manifest.corpus_file_sha256,
                candidate_sha256=case_manifest.candidate_sha256,
                candidate_bytes=case_manifest.candidate_bytes,
                exit_code=case_manifest.exit_code,
                termination_reason=case_manifest.termination_reason,
                process_succeeded=case_manifest.process_succeeded,
                candidate_valid=case_manifest.candidate_valid,
                validation_error=case_manifest.validation_error,
                stdout_bytes=case_manifest.stdout_bytes,
                stdout_sha256=case_manifest.stdout_sha256,
                stderr_bytes=case_manifest.stderr_bytes,
                stderr_sha256=case_manifest.stderr_sha256,
            )
            case_runs.append(case_run)
            aggregate_stdout_bytes += case_run.stdout_bytes
            aggregate_stderr_bytes += case_run.stderr_bytes
            if case_manifest.ready_for_scoring:
                try:
                    case_candidate_document = load_strict_json_file(
                        case_candidate_path,
                        limits=_candidate_json_limits(limits.max_candidate_bytes),
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
                except StrictJsonError as exc:
                    raise ExternalRunnerError(
                        f"validated case output could not be reread: {exc}"
                    ) from exc
                candidate_cases.append(case_candidate_payload["cases"][0])

        if aggregate_stdout_bytes > limits.max_stdout_bytes:
            termination_reason = "aggregate_stdout_limit"
            break
        if aggregate_stderr_bytes > limits.max_stderr_bytes:
            termination_reason = "aggregate_stderr_limit"
            break

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
    ready_for_scoring = process_succeeded and candidate_valid
    return ExternalRunManifest(
        system=system,
        isolation_mode="per_case",
        case_count=len(cases),
        case_runs=tuple(case_runs),
        command=resolved_template,
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
        network_isolation=network_isolation,
        claim_metadata_complete=_claim_controls_complete(
            identity,
            limits,
            "per_case",
            network_isolation,
        ),
        memory_limit_enforced=limits.max_memory_mb is not None,
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
            "{case_id} are replaced without invoking a shell"
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
        network_isolation = capture_network_isolation_evidence(
            args.network_isolation_mode,
            args.network_isolation_evidence,
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
            network_isolation=network_isolation,
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
