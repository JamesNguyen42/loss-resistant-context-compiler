"""Process-isolated whole-compilation deadlines."""

from __future__ import annotations

import hashlib
import json
import math
import os
import pickle
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .atomic import atomic_write_text
from .limits import (
    DEFAULT_ARTIFACT_LIMITS,
    ArtifactLimits,
    SourceLimitError,
    validate_artifact_value,
)
from .models import (
    SCHEMA_VERSION,
    CompiledMemory,
    CompressionStats,
    IssueSeverity,
    MemoryItem,
    SourceRecord,
    VerificationIssue,
    VerificationReport,
)
from .process_tree import (
    WINDOWS_CREATE_SUSPENDED,
    WindowsJob,
    resume_windows_process,
    terminate_posix_process_group,
    terminate_process_tree,
)

_ISOLATED_COMPILE_SCHEMA = "ctxc-isolated-compile-0.1"
_MAX_JOB_BYTES = 256 * 1024 * 1024
_MAX_RESPONSE_BYTES = 256 * 1024 * 1024
_RESPONSE_LIMITS = ArtifactLimits(
    max_input_bytes=_MAX_RESPONSE_BYTES,
    max_line_chars=_MAX_RESPONSE_BYTES,
    max_canonical_bytes=_MAX_RESPONSE_BYTES,
    max_json_depth=DEFAULT_ARTIFACT_LIMITS.max_json_depth + 1,
    max_items=DEFAULT_ARTIFACT_LIMITS.max_items,
    max_selected_items=DEFAULT_ARTIFACT_LIMITS.max_selected_items,
    max_provenance_spans=DEFAULT_ARTIFACT_LIMITS.max_provenance_spans,
    max_verification_issues=DEFAULT_ARTIFACT_LIMITS.max_verification_issues,
)


class CompilationIsolationError(RuntimeError):
    """A process-isolated compilation could not return a valid result."""


def _validate_timeout_seconds(value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value <= 0
    ):
        raise ValueError("compile timeout must be a finite positive number")
    return float(value)


def _write_pickle(path: Path, value: Any) -> None:
    with path.open("wb") as stream:
        pickle.dump(value, stream, protocol=pickle.HIGHEST_PROTOCOL)
        stream.flush()
        os.fsync(stream.fileno())


def _read_bounded_pickle(path: Path, *, max_bytes: int, label: str) -> Any:
    try:
        stat_result = path.stat()
    except FileNotFoundError as exc:
        raise CompilationIsolationError(f"{label} was not produced") from exc
    if not path.is_file():
        raise CompilationIsolationError(f"{label} must be a regular file")
    if stat_result.st_size > max_bytes:
        raise CompilationIsolationError(
            f"{label} exceeds {max_bytes} bytes"
        )
    with path.open("rb") as stream:
        payload = stream.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise CompilationIsolationError(
            f"{label} exceeds {max_bytes} bytes"
        )
    try:
        return pickle.loads(payload)
    except Exception as exc:
        raise CompilationIsolationError(
            f"{label} is not a valid worker payload"
        ) from exc


def _safe_error_message(exc: BaseException) -> str:
    return str(exc).encode("utf-8", errors="backslashreplace").decode("utf-8")


def _decode_worker_artifact(value: Any) -> CompiledMemory:
    if not isinstance(value, dict):
        raise CompilationIsolationError(
            "isolated compilation artifact must be an object"
        )
    validate_artifact_value(value, limits=DEFAULT_ARTIFACT_LIMITS)
    from .io import _validate_artifact_shape

    shape_issues: list[dict[str, Any]] = []
    _validate_artifact_shape(value, shape_issues)
    for name in (
        "items",
        "selected_item_ids",
    ):
        if not isinstance(value.get(name), list):
            shape_issues.append(
                {
                    "code": "invalid_artifact_shape",
                    "message": f"Artifact {name} must be an array.",
                }
            )
    for name in ("verification", "compression"):
        if not isinstance(value.get(name), dict):
            shape_issues.append(
                {
                    "code": "invalid_artifact_shape",
                    "message": f"Artifact {name} must be an object.",
                }
            )
    if shape_issues:
        raise CompilationIsolationError(
            "isolated compilation returned an invalid artifact: "
            + str(shape_issues[0].get("message", "invalid shape"))
        )
    if value.get("schema_version") != SCHEMA_VERSION:
        raise CompilationIsolationError(
            "isolated compilation returned an unsupported artifact schema"
        )
    if value.get("ledger_complete") is not True:
        raise CompilationIsolationError(
            "isolated compilation must return a complete ledger"
        )
    claimed_digest = value["artifact_sha256"]
    unsigned = {
        key: entry
        for key, entry in value.items()
        if key != "artifact_sha256"
    }
    canonical_unsigned = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    actual_digest = hashlib.sha256(
        canonical_unsigned.encode("utf-8")
    ).hexdigest()
    if claimed_digest != actual_digest:
        raise CompilationIsolationError(
            "isolated compilation artifact digest does not match its payload"
        )

    items = [MemoryItem.from_dict(item) for item in value["items"]]
    item_ids = [item.id for item in items]
    if len(item_ids) != len(set(item_ids)):
        raise CompilationIsolationError(
            "isolated compilation returned duplicate memory item ids"
        )
    selected_item_ids = value["selected_item_ids"]
    if len(selected_item_ids) != len(set(selected_item_ids)):
        raise CompilationIsolationError(
            "isolated compilation returned duplicate selected item ids"
        )
    if not set(selected_item_ids) <= set(item_ids):
        raise CompilationIsolationError(
            "isolated compilation selected an absent memory item"
        )

    raw_verification = value["verification"]
    verification = VerificationReport(
        passed=raw_verification["passed"],
        issues=[
            VerificationIssue(
                code=issue["code"],
                severity=IssueSeverity(issue["severity"]),
                message=issue["message"],
                item_id=issue["item_id"],
                source_id=issue["source_id"],
            )
            for issue in raw_verification["issues"]
        ],
        protected_candidates=raw_verification["protected_candidates"],
        protected_retained=raw_verification["protected_retained"],
        provenance_valid=raw_verification["provenance_valid"],
        provenance_total=raw_verification["provenance_total"],
        recovered_items=raw_verification["recovered_items"],
    )
    raw_compression = value["compression"]
    compression = CompressionStats(
        source_chars=raw_compression["source_chars"],
        active_chars=raw_compression["active_chars"],
        source_tokens_estimate=raw_compression["source_tokens_estimate"],
        active_tokens_estimate=raw_compression["active_tokens_estimate"],
        compression_ratio=raw_compression["compression_ratio"],
        token_budget=raw_compression["token_budget"],
        budget_overflow=raw_compression["budget_overflow"],
        target_met=raw_compression["target_met"],
    )
    result = CompiledMemory(
        schema_version=value["schema_version"],
        compiled_at=value["compiled_at"],
        source_digest=value["source_digest"],
        source_count=value["source_count"],
        items=items,
        selected_item_ids=list(selected_item_ids),
        verification=verification,
        compression=compression,
        compiler_metadata=value["compiler_metadata"],
    ).seal()
    canonical_rebuilt = json.dumps(
        result.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    canonical_received = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if canonical_rebuilt != canonical_received:
        raise CompilationIsolationError(
            "isolated compilation artifact changed during reconstruction"
        )
    return result


def _remaining_seconds(started: float, timeout_seconds: float) -> float:
    remaining = timeout_seconds - (time.monotonic() - started)
    if remaining <= 0:
        raise TimeoutError(
            f"compilation exceeded its {timeout_seconds:g}-second deadline"
        )
    return remaining


def _raise_worker_error(response: dict[str, Any]) -> None:
    if set(response) != {
        "schema",
        "status",
        "exception_module",
        "exception_type",
        "message",
    }:
        raise CompilationIsolationError(
            "isolated compilation returned an invalid error record"
        )
    module = response.get("exception_module")
    name = response.get("exception_type")
    message = response.get("message")
    if not all(isinstance(value, str) for value in (module, name, message)):
        raise CompilationIsolationError(
            "isolated compilation returned an invalid error record"
        )
    known: dict[tuple[str, str], type[Exception]] = {
        ("builtins", "FileNotFoundError"): FileNotFoundError,
        ("builtins", "OSError"): OSError,
        ("builtins", "PermissionError"): PermissionError,
        ("builtins", "RuntimeError"): RuntimeError,
        ("builtins", "TimeoutError"): TimeoutError,
        ("builtins", "TypeError"): TypeError,
        ("builtins", "ValueError"): ValueError,
        ("context_compiler.limits", "SourceLimitError"): SourceLimitError,
    }
    exception_type = known.get((module, name))
    if exception_type is None:
        raise CompilationIsolationError(
            f"isolated compilation failed with {module}.{name}: {message}"
        )
    raise exception_type(message)


def compile_isolated(
    compiler: Any,
    sources: Iterable[SourceRecord | dict[str, Any]],
    *,
    timeout_seconds: float,
) -> CompiledMemory:
    """Compile in a killable subprocess and enforce one whole-run deadline.

    Deadline isolation accepts a materialized list or tuple. This avoids
    pretending that an arbitrary blocking generator can be interrupted safely
    before it has crossed the subprocess boundary.
    """

    from .compiler import ContextCompiler

    if not isinstance(compiler, ContextCompiler):
        raise TypeError("compiler must be a ContextCompiler")
    timeout = _validate_timeout_seconds(timeout_seconds)
    if not isinstance(sources, (list, tuple)):
        raise TypeError(
            "deadline compilation requires sources as a materialized list or tuple"
        )
    materialized_sources = list(sources)
    started = time.monotonic()

    with tempfile.TemporaryDirectory(prefix="ctxc-compile-") as directory:
        temporary_directory = Path(directory)
        job_path = temporary_directory / "job.pickle"
        response_path = temporary_directory / "response.json"
        try:
            _write_pickle(
                job_path,
                {
                    "schema": _ISOLATED_COMPILE_SCHEMA,
                    "compiler": compiler,
                    "sources": materialized_sources,
                },
            )
        except Exception as exc:
            raise TypeError(
                "deadline compilation requires a serializable compiler "
                "configuration and source list"
            ) from exc
        if job_path.stat().st_size > _MAX_JOB_BYTES:
            raise SourceLimitError(
                f"isolated compilation job exceeds {_MAX_JOB_BYTES} bytes"
            )
        _remaining_seconds(started, timeout)

        command = (
            sys.executable,
            "-m",
            "context_compiler.isolation_worker",
            str(job_path),
            str(response_path),
        )
        creation_flags = 0
        windows_job: WindowsJob | None = None
        if os.name == "nt":
            creation_flags = (
                subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
                | subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
                | WINDOWS_CREATE_SUSPENDED
            )
            windows_job = WindowsJob.create(
                error_type=CompilationIsolationError
            )
        process: subprocess.Popen[bytes] | None = None
        timed_out = False
        try:
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    close_fds=True,
                    start_new_session=os.name != "nt",
                    creationflags=creation_flags,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise CompilationIsolationError(
                    "could not start isolated compilation worker"
                ) from exc
            if windows_job is not None:
                try:
                    windows_job.assign(process)
                    if not windows_job.contains(process):
                        raise CompilationIsolationError(
                            "compilation worker escaped its Windows Job Object"
                        )
                    resume_windows_process(
                        process,
                        error_type=CompilationIsolationError,
                    )
                except Exception:
                    terminate_process_tree(process, windows_job)
                    process.wait(timeout=1)
                    raise
            try:
                remaining = _remaining_seconds(started, timeout)
            except TimeoutError:
                timed_out = True
                terminate_process_tree(process, windows_job)
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            else:
                try:
                    process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    terminate_process_tree(process, windows_job)
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1)
        finally:
            if process is not None and process.poll() is None:
                terminate_process_tree(process, windows_job)
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
            elif process is not None:
                terminate_posix_process_group(process.pid)

        if timed_out:
            raise TimeoutError(
                f"compilation exceeded its {timeout:g}-second deadline; "
                "the isolated process tree was terminated"
            )
        if process is None:
            raise CompilationIsolationError(
                "isolated compilation worker was not started"
            )
        from .io import load_artifact_path

        try:
            response = load_artifact_path(
                response_path,
                limits=_RESPONSE_LIMITS,
            )
        except (OSError, TypeError, ValueError) as exc:
            raise CompilationIsolationError(
                "isolated compilation response is not valid bounded JSON"
            ) from exc
        if not isinstance(response, dict):
            raise CompilationIsolationError(
                "isolated compilation response must be an object"
            )
        if response.get("schema") != _ISOLATED_COMPILE_SCHEMA:
            raise CompilationIsolationError(
                "isolated compilation response has an unsupported schema"
            )
        status = response.get("status")
        if status == "error":
            _raise_worker_error(response)
        if status != "ok" or set(response) != {"schema", "status", "artifact"}:
            raise CompilationIsolationError(
                "isolated compilation returned an invalid success record"
            )
        if process.returncode != 0:
            raise CompilationIsolationError(
                f"isolated compilation worker exited with status {process.returncode}"
            )
        return _decode_worker_artifact(response["artifact"])


def run_worker(job_path: str, response_path: str) -> int:
    """Execute one trusted parent-created job inside the isolated worker."""

    response_file = Path(response_path)
    try:
        job = _read_bounded_pickle(
            Path(job_path),
            max_bytes=_MAX_JOB_BYTES,
            label="isolated compilation job",
        )
        if (
            not isinstance(job, dict)
            or set(job) != {"schema", "compiler", "sources"}
            or job.get("schema") != _ISOLATED_COMPILE_SCHEMA
        ):
            raise CompilationIsolationError(
                "isolated compilation job has an invalid shape"
            )
        from .compiler import ContextCompiler

        compiler = job["compiler"]
        sources = job["sources"]
        if not isinstance(compiler, ContextCompiler):
            raise TypeError("isolated job compiler must be ContextCompiler")
        if not isinstance(sources, list):
            raise TypeError("isolated job sources must be a list")
        result = compiler._compile_impl(sources)
        atomic_write_text(
            response_file,
            json.dumps(
                {
                    "schema": _ISOLATED_COMPILE_SCHEMA,
                    "status": "ok",
                    "artifact": result.to_dict(),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ),
        )
        return 0
    except BaseException as exc:
        try:
            atomic_write_text(
                response_file,
                json.dumps(
                    {
                        "schema": _ISOLATED_COMPILE_SCHEMA,
                        "status": "error",
                        "exception_module": type(exc).__module__,
                        "exception_type": type(exc).__name__,
                        "message": _safe_error_message(exc),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ),
            )
        except BaseException:
            return 2
        return 1
