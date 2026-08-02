"""Bounded literal-argv subprocess lifecycle for benchmark coordinators.

This module owns process launch, concurrent stream draining, deadlines, and
anchored cleanup. It deliberately does not provide a filesystem, mount, user,
PID, or network sandbox. POSIX process groups are escapable, so a successful
result is never sufficient SWE-bench containment evidence by itself.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from context_compiler.process_tree import (
    WINDOWS_CREATE_SUSPENDED,
    WindowsJob,
    posix_process_exited_without_reaping,
    resume_windows_process,
    terminate_anchored_posix_process_group,
)

_MAX_ARGUMENTS = 256
_MAX_ARGUMENT_BYTES = 64 * 1024
_MAX_ARGV_BYTES = 1024 * 1024
_MAX_ENVIRONMENT_NAMES = 1024
_MAX_ENVIRONMENT_NAME_BYTES = 1024
_MAX_ENVIRONMENT_VALUE_BYTES = 128 * 1024
_MAX_ENVIRONMENT_BYTES = 2 * 1024 * 1024
_MAX_STREAM_BYTES = 64 * 1024 * 1024
_MAX_MEMORY_MB = 1024 * 1024
_MAX_TIMEOUT_SECONDS = 7 * 24 * 60 * 60
_MAX_POLL_INTERVAL_SECONDS = 60.0
_PIPE_READ_BYTES = 64 * 1024
_PIPE_JOIN_SECONDS = 1.0
_ENVIRONMENT_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class LiteralProcessError(RuntimeError):
    """A literal process request or lifecycle boundary failed."""


def _positive_finite_number(
    value: object,
    *,
    label: str,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LiteralProcessError(f"{label} must be a positive finite number")
    try:
        finite = math.isfinite(value)
        converted = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise LiteralProcessError(
            f"{label} must be a positive finite number"
        ) from exc
    if not finite or not 0 < converted <= maximum:
        raise LiteralProcessError(
            f"{label} must be a positive finite number no greater than {maximum}"
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
        raise LiteralProcessError(
            f"{label} must be an integer from {minimum} through {maximum}"
        )
    return value


@dataclass(frozen=True, slots=True)
class LiteralProcessLimits:
    """Finite process, stream, optional Windows memory, and polling bounds."""

    timeout_seconds: float = 300.0
    poll_interval_seconds: float = 0.02
    max_stdout_bytes: int = 1_000_000
    max_stderr_bytes: int = 1_000_000
    max_memory_mb: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "timeout_seconds",
            _positive_finite_number(
                self.timeout_seconds,
                label="timeout_seconds",
                maximum=float(_MAX_TIMEOUT_SECONDS),
            ),
        )
        object.__setattr__(
            self,
            "poll_interval_seconds",
            _positive_finite_number(
                self.poll_interval_seconds,
                label="poll_interval_seconds",
                maximum=_MAX_POLL_INTERVAL_SECONDS,
            ),
        )
        for name in ("max_stdout_bytes", "max_stderr_bytes"):
            object.__setattr__(
                self,
                name,
                _bounded_integer(
                    getattr(self, name),
                    label=name,
                    minimum=0,
                    maximum=_MAX_STREAM_BYTES,
                ),
            )
        if self.max_memory_mb is not None:
            object.__setattr__(
                self,
                "max_memory_mb",
                _bounded_integer(
                    self.max_memory_mb,
                    label="max_memory_mb",
                    minimum=1,
                    maximum=_MAX_MEMORY_MB,
                ),
            )


@dataclass(frozen=True, slots=True)
class StreamEvidence:
    """One bounded prefix and the total concurrently drained byte count."""

    observed_bytes: int
    captured_bytes: int
    captured_sha256: str
    prefix: bytes
    complete: bool


@dataclass(frozen=True, slots=True)
class LiteralProcessResult:
    """One completed literal process lifecycle and its non-sandbox claims."""

    argv: tuple[str, ...]
    cwd: str
    limits: LiteralProcessLimits
    exit_code: int | None
    duration_seconds: float
    setup_duration_seconds: float
    process_duration_seconds: float
    cleanup_duration_seconds: float
    termination_trigger: str | None
    execution_error: str | None
    cleanup_error: str | None
    cleanup_detail: str | None
    stdout: StreamEvidence
    stderr: StreamEvidence
    memory_limit_scope: str
    containment_scope: str
    environment_names: tuple[str, ...]
    environment_sha256: str

    @property
    def process_succeeded(self) -> bool:
        return (
            self.exit_code == 0
            and self.termination_trigger is None
            and self.execution_error is None
            and self.cleanup_error is None
            and self.stdout.complete
            and self.stderr.complete
        )

    @property
    def swebench_containment_claim_ready(self) -> bool:
        """This lifecycle alone never proves a SWE-bench sandbox."""

        return False


def _utf8_size(value: str, *, label: str, maximum: int) -> int:
    try:
        size = len(value.encode("utf-8", errors="strict"))
    except UnicodeError as exc:
        raise LiteralProcessError(f"{label} must be valid UTF-8 text") from exc
    if size > maximum:
        raise LiteralProcessError(f"{label} exceeds its byte limit")
    return size


def _validate_argv(value: object) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(
        value,
        Sequence,
    ):
        raise LiteralProcessError("argv must be a non-string sequence")
    if not 1 <= len(value) <= _MAX_ARGUMENTS:
        raise LiteralProcessError(
            f"argv must contain from 1 through {_MAX_ARGUMENTS} arguments"
        )
    arguments: list[str] = []
    total_bytes = 0
    for index, argument in enumerate(value):
        if not isinstance(argument, str) or not argument:
            raise LiteralProcessError(
                f"argv[{index}] must be a non-empty string"
            )
        if "\0" in argument:
            raise LiteralProcessError(f"argv[{index}] contains a NUL character")
        total_bytes += _utf8_size(
            argument,
            label=f"argv[{index}]",
            maximum=_MAX_ARGUMENT_BYTES,
        )
        if total_bytes > _MAX_ARGV_BYTES:
            raise LiteralProcessError("argv exceeds its aggregate byte limit")
        arguments.append(argument)
    executable = Path(arguments[0])
    if not executable.is_absolute():
        raise LiteralProcessError("argv[0] must be an absolute executable path")
    try:
        if not executable.is_file():
            raise LiteralProcessError("argv[0] must name an existing regular file")
    except OSError as exc:
        raise LiteralProcessError("argv[0] could not be inspected") from exc
    return tuple(arguments)


def _validate_directory(value: object, *, label: str) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise LiteralProcessError(f"{label} must be an absolute directory path")
    path = Path(value)
    if not path.is_absolute():
        raise LiteralProcessError(f"{label} must be an absolute directory path")
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_dir():
            raise LiteralProcessError(f"{label} must be an existing directory")
    except LiteralProcessError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise LiteralProcessError(f"{label} could not be inspected") from exc
    return resolved


def _validate_environment(
    value: object,
) -> tuple[dict[str, str], tuple[str, ...], str]:
    if not isinstance(value, Mapping):
        raise LiteralProcessError("environment must be a string mapping")
    if len(value) > _MAX_ENVIRONMENT_NAMES:
        raise LiteralProcessError("environment contains too many variables")
    environment: dict[str, str] = {}
    folded_names: set[str] = set()
    total_bytes = 0
    for name, raw_value in value.items():
        if not isinstance(name, str) or _ENVIRONMENT_NAME_RE.fullmatch(name) is None:
            raise LiteralProcessError("environment contains an invalid variable name")
        if not isinstance(raw_value, str) or "\0" in raw_value:
            raise LiteralProcessError(
                f"environment variable {name!r} must be a NUL-free string"
            )
        folded = name.casefold()
        if os.name == "nt" and folded in folded_names:
            raise LiteralProcessError(
                "environment contains case-insensitive duplicate names"
            )
        folded_names.add(folded)
        total_bytes += _utf8_size(
            name,
            label=f"environment name {name!r}",
            maximum=_MAX_ENVIRONMENT_NAME_BYTES,
        )
        total_bytes += _utf8_size(
            raw_value,
            label=f"environment variable {name!r}",
            maximum=_MAX_ENVIRONMENT_VALUE_BYTES,
        )
        if total_bytes > _MAX_ENVIRONMENT_BYTES:
            raise LiteralProcessError("environment exceeds its aggregate byte limit")
        environment[name] = raw_value
    encoded = json.dumps(
        environment,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8", errors="strict")
    return environment, tuple(sorted(environment)), hashlib.sha256(encoded).hexdigest()


class _PipeCapture:
    """Drain one child pipe while retaining at most ``limit + 1`` bytes."""

    def __init__(
        self,
        stream: BinaryIO,
        *,
        limit: int,
        label: str,
        activity: threading.Event,
    ) -> None:
        self.stream = stream
        self.limit = limit
        self.label = label
        self.activity = activity
        self._lock = threading.Lock()
        self._prefix = bytearray()
        self._observed = 0
        self._error: str | None = None
        self._complete = False
        self._closing = False
        self._started = False
        self._thread = threading.Thread(
            target=self._drain,
            name=f"ctxc-{label}-drain",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()
        self._started = True

    def _drain(self) -> None:
        try:
            while True:
                chunk = os.read(self.stream.fileno(), _PIPE_READ_BYTES)
                if not chunk:
                    with self._lock:
                        self._complete = True
                    return
                with self._lock:
                    self._observed += len(chunk)
                    remaining = self.limit + 1 - len(self._prefix)
                    if remaining > 0:
                        self._prefix.extend(chunk[:remaining])
                    overflow = self._observed > self.limit
                if overflow:
                    self.activity.set()
        except OSError as exc:
            with self._lock:
                if not self._closing:
                    self._error = type(exc).__name__
        finally:
            self.activity.set()

    @property
    def overflowed(self) -> bool:
        with self._lock:
            return self._observed > self.limit

    @property
    def error(self) -> str | None:
        with self._lock:
            return self._error

    def finish(self) -> bool:
        if not self._started:
            with suppress(OSError):
                self.stream.close()
            return True
        self._thread.join(timeout=_PIPE_JOIN_SECONDS)
        if self._thread.is_alive():
            with self._lock:
                self._closing = True
            with suppress(OSError):
                self.stream.close()
            self._thread.join(timeout=0.1)
        else:
            with suppress(OSError):
                self.stream.close()
        return not self._thread.is_alive()

    def evidence(self) -> StreamEvidence:
        with self._lock:
            prefix = bytes(self._prefix)
            observed = self._observed
            complete = self._complete and self._error is None
        return StreamEvidence(
            observed_bytes=observed,
            captured_bytes=len(prefix),
            captured_sha256=hashlib.sha256(prefix).hexdigest(),
            prefix=prefix,
            complete=complete,
        )


def _memory_scope(limits: LiteralProcessLimits) -> str:
    if limits.max_memory_mb is None:
        return "none"
    if os.name == "nt":
        return "windows-job-process-and-aggregate"
    raise LiteralProcessError(
        "POSIX memory limits require an external controller; preexec_fn is forbidden"
    )


def _containment_scope() -> str:
    if os.name == "nt":
        return "windows-job-process-and-descendants"
    return "posix-session-process-group-escapable"


def _best_effort_direct_cleanup(process: subprocess.Popen[bytes]) -> None:
    with suppress(OSError):
        process.kill()
    with suppress(OSError, subprocess.SubprocessError):
        process.wait(timeout=1)


def _record_cleanup_failure(
    current_code: str | None,
    current_detail: str | None,
    *,
    code: str,
    detail: str,
) -> tuple[str, str]:
    if current_code is None:
        return code, detail
    return current_code, f"{current_detail}; {code}: {detail}"


class _EmergencyLifecycle:
    """Own post-launch resources until the normal path explicitly disarms."""

    def __init__(self) -> None:
        self.process: subprocess.Popen[bytes] | None = None
        self.windows_job: WindowsJob | None = None
        self.stdout_capture: _PipeCapture | None = None
        self.stderr_capture: _PipeCapture | None = None
        self.disarmed = False

    def cleanup(self) -> tuple[str, ...]:
        if self.disarmed:
            return ()
        errors: list[str] = []
        process = self.process
        job = self.windows_job
        if process is not None:
            if os.name == "nt" and job is not None:
                try:
                    job.terminate()
                except Exception as exc:  # noqa: BLE001 - emergency boundary
                    errors.append(f"windows_job:{type(exc).__name__}")
            elif os.name != "nt" and process.returncode is None:
                try:
                    terminate_anchored_posix_process_group(
                        process,
                        error_type=LiteralProcessError,
                    )
                except Exception as exc:  # noqa: BLE001 - emergency boundary
                    errors.append(f"process_group:{type(exc).__name__}")
            if process.returncode is None:
                _best_effort_direct_cleanup(process)
                if process.returncode is None:
                    errors.append("process_reap:incomplete")
        for capture, label in (
            (self.stdout_capture, "stdout_capture"),
            (self.stderr_capture, "stderr_capture"),
        ):
            if capture is not None:
                try:
                    if not capture.finish():
                        errors.append(f"{label}:incomplete")
                except Exception as exc:  # noqa: BLE001 - emergency boundary
                    errors.append(f"{label}:{type(exc).__name__}")
        if job is not None:
            try:
                job.close()
            except Exception as exc:  # noqa: BLE001 - emergency boundary
                errors.append(f"windows_job_close:{type(exc).__name__}")
        self.disarmed = True
        return tuple(errors)


def _run_literal_argv(
    argv: Sequence[str],
    *,
    cwd: Path | str,
    environment: Mapping[str, str],
    limits: LiteralProcessLimits | None = None,
    guard: _EmergencyLifecycle,
) -> LiteralProcessResult:
    """Run one bounded command without shell parsing or placeholder expansion.

    The lifecycle timeout begins after input validation but before containment
    setup. On Windows the suspended child is not resumed after that deadline.
    POSIX ``Popen`` cannot provide the same pre-exec pause, so elapsed launch
    time is checked immediately when it returns and any late completion fails
    closed as a timeout.
    """

    arguments = _validate_argv(argv)
    working_directory = _validate_directory(cwd, label="cwd")
    process_environment, environment_names, environment_sha256 = (
        _validate_environment(environment)
    )
    if limits is None:
        limits = LiteralProcessLimits()
    elif not isinstance(limits, LiteralProcessLimits):
        raise LiteralProcessError("limits must be LiteralProcessLimits")
    if os.name != "nt" and limits.max_memory_mb is not None:
        raise LiteralProcessError(
            "POSIX memory limits require an external controller; preexec_fn is forbidden"
        )

    lifecycle_started = time.monotonic()
    deadline = lifecycle_started + limits.timeout_seconds
    setup_finished = lifecycle_started
    process_started: float | None = None
    process_finished = lifecycle_started
    cleanup_started = lifecycle_started
    cleanup_finished = lifecycle_started
    process: subprocess.Popen[bytes] | None = None
    windows_job: WindowsJob | None = None
    stdout_capture: _PipeCapture | None = None
    stderr_capture: _PipeCapture | None = None
    exit_code: int | None = None
    termination_trigger: str | None = None
    execution_error: str | None = None
    cleanup_error: str | None = None
    cleanup_detail: str | None = None
    fatal_error: LiteralProcessError | None = None
    empty_evidence = StreamEvidence(
        observed_bytes=0,
        captured_bytes=0,
        captured_sha256=hashlib.sha256(b"").hexdigest(),
        prefix=b"",
        complete=True,
    )
    stdout_evidence = empty_evidence
    stderr_evidence = empty_evidence
    activity = threading.Event()

    creation_flags = 0
    if os.name == "nt":
        creation_flags = (
            subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            | subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
            | WINDOWS_CREATE_SUSPENDED
        )
        try:
            windows_job = WindowsJob.create(
                limits.max_memory_mb,
                error_type=LiteralProcessError,
            )
            guard.windows_job = windows_job
        except LiteralProcessError as exc:
            fatal_error = exc

    if fatal_error is None and time.monotonic() >= deadline:
        termination_trigger = "timeout"
        setup_finished = time.monotonic()

    if fatal_error is None and termination_trigger is None:
        popen_started = time.monotonic()
        try:
            process = subprocess.Popen(
                list(arguments),
                cwd=working_directory,
                env=process_environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                close_fds=True,
                start_new_session=os.name != "nt",
                creationflags=creation_flags,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            fatal_error = LiteralProcessError(
                f"could not start literal process: {type(exc).__name__}"
            )
        else:
            guard.process = process
            process_started = popen_started if os.name != "nt" else None
            if process.stdout is None or process.stderr is None:
                fatal_error = LiteralProcessError(
                    "literal process pipes were not created"
                )
            else:
                stdout_capture = _PipeCapture(
                    process.stdout,
                    limit=limits.max_stdout_bytes,
                    label="stdout",
                    activity=activity,
                )
                stderr_capture = _PipeCapture(
                    process.stderr,
                    limit=limits.max_stderr_bytes,
                    label="stderr",
                    activity=activity,
                )
                guard.stdout_capture = stdout_capture
                guard.stderr_capture = stderr_capture
                try:
                    stdout_capture.start()
                    stderr_capture.start()
                except RuntimeError:
                    fatal_error = LiteralProcessError(
                        "could not start literal process pipe capture"
                    )

    if process is not None and windows_job is not None and fatal_error is None:
        try:
            windows_job.assign(process)
            if not windows_job.contains(process):
                raise LiteralProcessError(
                    "literal process was not assigned to its Windows Job Object"
                )
            if time.monotonic() >= deadline:
                termination_trigger = "timeout"
            else:
                resume_windows_process(
                    process,
                    error_type=LiteralProcessError,
                )
                process_started = time.monotonic()
        except LiteralProcessError as exc:
            fatal_error = exc

    setup_finished = time.monotonic()

    if process is not None and fatal_error is None and termination_trigger is None:
        while True:
            now = time.monotonic()
            if now >= deadline:
                termination_trigger = "timeout"
                break
            if stdout_capture is None or stderr_capture is None:
                fatal_error = LiteralProcessError("literal process capture is unavailable")
                break
            if stdout_capture.error is not None or stderr_capture.error is not None:
                termination_trigger = "stream_observation_failed"
                execution_error = (
                    stdout_capture.error or stderr_capture.error
                )
                break
            if stdout_capture.overflowed:
                termination_trigger = "stdout_limit"
                break
            if stderr_capture.overflowed:
                termination_trigger = "stderr_limit"
                break
            try:
                if os.name == "nt":
                    process_exited = process.poll() is not None
                else:
                    process_exited = posix_process_exited_without_reaping(
                        process,
                        error_type=LiteralProcessError,
                    )
            except (OSError, subprocess.SubprocessError) as exc:
                termination_trigger = "process_observation_failed"
                execution_error = type(exc).__name__
                break
            except LiteralProcessError as exc:
                termination_trigger = "process_observation_failed"
                execution_error = str(exc)
                break
            if process_exited:
                break
            activity.clear()
            activity.wait(
                timeout=min(
                    limits.poll_interval_seconds,
                    max(0.0, deadline - time.monotonic()),
                )
            )

    process_finished = time.monotonic()
    cleanup_started = process_finished

    if process is not None:
        if os.name == "nt":
            if windows_job is not None:
                try:
                    windows_job.terminate()
                except LiteralProcessError as exc:
                    cleanup_error, cleanup_detail = _record_cleanup_failure(
                        cleanup_error,
                        cleanup_detail,
                        code="windows_job_cleanup_failed",
                        detail=str(exc),
                    )
                    _best_effort_direct_cleanup(process)
            try:
                exit_code = process.wait(timeout=1)
            except (OSError, subprocess.SubprocessError) as exc:
                cleanup_error, cleanup_detail = _record_cleanup_failure(
                    cleanup_error,
                    cleanup_detail,
                    code="process_reap_failed",
                    detail=type(exc).__name__,
                )
                _best_effort_direct_cleanup(process)
                exit_code = process.returncode
        else:
            try:
                terminate_anchored_posix_process_group(
                    process,
                    error_type=LiteralProcessError,
                )
            except LiteralProcessError as exc:
                cleanup_error, cleanup_detail = _record_cleanup_failure(
                    cleanup_error,
                    cleanup_detail,
                    code="process_group_cleanup_failed",
                    detail=str(exc),
                )
                _best_effort_direct_cleanup(process)
                exit_code = process.returncode
            else:
                try:
                    exit_code = process.wait(timeout=1)
                except (OSError, subprocess.SubprocessError) as exc:
                    cleanup_error, cleanup_detail = _record_cleanup_failure(
                        cleanup_error,
                        cleanup_detail,
                        code="process_reap_failed",
                        detail=type(exc).__name__,
                    )
                    _best_effort_direct_cleanup(process)
                    exit_code = process.returncode

    if process is not None and process.returncode is not None:
        guard.process = None
    for capture, label in (
        (stdout_capture, "stdout"),
        (stderr_capture, "stderr"),
    ):
        if capture is not None:
            if capture.finish():
                if label == "stdout":
                    guard.stdout_capture = None
                else:
                    guard.stderr_capture = None
            else:
                cleanup_error, cleanup_detail = _record_cleanup_failure(
                    cleanup_error,
                    cleanup_detail,
                    code=f"{label}_capture_cleanup_failed",
                    detail="pipe reader did not terminate",
                )
    if stdout_capture is not None:
        stdout_evidence = stdout_capture.evidence()
    if stderr_capture is not None:
        stderr_evidence = stderr_capture.evidence()
    if termination_trigger is None:
        if stdout_evidence.observed_bytes > limits.max_stdout_bytes:
            termination_trigger = "stdout_limit"
        elif stderr_evidence.observed_bytes > limits.max_stderr_bytes:
            termination_trigger = "stderr_limit"
        elif not stdout_evidence.complete or not stderr_evidence.complete:
            termination_trigger = "stream_observation_failed"
            execution_error = "pipe capture is incomplete"

    if process is not None and process.returncode is None:
        _best_effort_direct_cleanup(process)
    if windows_job is not None:
        try:
            windows_job.close()
        except (OSError, LiteralProcessError) as exc:
            cleanup_error, cleanup_detail = _record_cleanup_failure(
                cleanup_error,
                cleanup_detail,
                code="windows_job_close_failed",
                detail=type(exc).__name__,
            )
        else:
            guard.windows_job = None
    cleanup_finished = time.monotonic()

    if fatal_error is not None:
        if cleanup_error is not None:
            raise LiteralProcessError(
                f"{fatal_error}; cleanup failed ({cleanup_error}): "
                f"{cleanup_detail}"
            ) from fatal_error
        raise fatal_error

    process_duration = (
        max(0.0, process_finished - process_started)
        if process_started is not None
        else 0.0
    )
    result = LiteralProcessResult(
        argv=arguments,
        cwd=str(working_directory),
        limits=limits,
        exit_code=exit_code,
        duration_seconds=max(0.0, cleanup_finished - lifecycle_started),
        setup_duration_seconds=max(0.0, setup_finished - lifecycle_started),
        process_duration_seconds=process_duration,
        cleanup_duration_seconds=max(0.0, cleanup_finished - cleanup_started),
        termination_trigger=termination_trigger,
        execution_error=execution_error,
        cleanup_error=cleanup_error,
        cleanup_detail=cleanup_detail,
        stdout=stdout_evidence,
        stderr=stderr_evidence,
        memory_limit_scope=_memory_scope(limits),
        containment_scope=_containment_scope(),
        environment_names=environment_names,
        environment_sha256=environment_sha256,
    )
    guard.disarmed = True
    return result


def run_literal_argv(
    argv: Sequence[str],
    *,
    cwd: Path | str,
    environment: Mapping[str, str],
    limits: LiteralProcessLimits | None = None,
) -> LiteralProcessResult:
    """Run a literal process and clean every armed resource on any exception."""

    guard = _EmergencyLifecycle()
    try:
        return _run_literal_argv(
            argv,
            cwd=cwd,
            environment=environment,
            limits=limits,
            guard=guard,
        )
    except BaseException as exc:
        cleanup_errors = guard.cleanup()
        if cleanup_errors:
            raise LiteralProcessError(
                "literal process failed and emergency cleanup was incomplete: "
                + ", ".join(cleanup_errors)
            ) from exc
        raise
