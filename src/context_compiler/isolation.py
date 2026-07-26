"""Process-isolated whole-compilation deadlines."""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import pickle
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable
from contextlib import suppress
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
    prove_darwin_process_group_all_zombies,
    resume_windows_process,
    terminate_process_tree,
)

_ISOLATED_COMPILE_SCHEMA = "ctxc-isolated-compile-0.1"
_MAX_JOB_BYTES = 256 * 1024 * 1024
_MAX_RESPONSE_BYTES = 256 * 1024 * 1024
_POSIX_SIGKILL = getattr(signal, "SIGKILL", 9)
_POSIX_SIGTERM = getattr(signal, "SIGTERM", 15)
_PROCESS_GROUP_GRACE_SECONDS = 0.5
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


def _resolved_temporary_root() -> Path:
    """Return the physical default temp root used by guarded worker outputs."""

    try:
        temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CompilationIsolationError(
            "could not resolve the isolated compilation temporary root"
        ) from exc
    if not temporary_root.is_dir():
        raise CompilationIsolationError(
            "isolated compilation temporary root must be a directory"
        )
    return temporary_root


def _force_compile_process_group(
    process: subprocess.Popen[Any],
) -> None:
    """Escalate one still-owned POSIX group without hiding permission failures."""

    try:
        os.killpg(process.pid, _POSIX_SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError as exc:
        if exc.errno != errno.EPERM:
            raise CompilationIsolationError(
                "could not force-terminate the isolated compilation process group"
            ) from exc
        if sys.platform != "darwin":
            raise CompilationIsolationError(
                "could not force-terminate the isolated compilation process group"
            ) from exc
    except OSError as exc:
        raise CompilationIsolationError(
            "could not force-terminate the isolated compilation process group"
        ) from exc
    if sys.platform != "darwin":
        return

    deadline = time.monotonic() + _PROCESS_GROUP_GRACE_SECONDS
    while True:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        except PermissionError as exc:
            if exc.errno != errno.EPERM:
                raise CompilationIsolationError(
                    "could not verify force-termination of the isolated "
                    "compilation process group"
                ) from exc
            break
        except OSError as exc:
            raise CompilationIsolationError(
                "could not verify force-termination of the isolated "
                "compilation process group"
            ) from exc
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.01, remaining))
    prove_darwin_process_group_all_zombies(
        process.pid,
        expected_leader_pid=process.pid,
        error_type=CompilationIsolationError,
    )


def _posix_worker_exited_without_reaping(
    process: subprocess.Popen[Any],
) -> bool:
    """Observe one worker exit while preserving its PID/PGID anchor."""

    options = os.WEXITED | os.WNOHANG | os.WNOWAIT
    try:
        result = os.waitid(os.P_PID, process.pid, options)
    except InterruptedError:
        return False
    except (ChildProcessError, OSError) as exc:
        raise CompilationIsolationError(
            "could not observe the isolated compilation worker without reaping"
        ) from exc
    return result is not None


def _wait_posix_worker_without_reaping(
    process: subprocess.Popen[Any],
    *,
    timeout: float,
) -> None:
    """Observe worker exit while its PID continues to anchor the owned group."""

    deadline = time.monotonic() + timeout
    while True:
        if time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired(process.args, timeout)
        leader_exited = _posix_worker_exited_without_reaping(process)
        observed_at = time.monotonic()
        if observed_at >= deadline:
            raise subprocess.TimeoutExpired(process.args, timeout)
        if leader_exited:
            return
        remaining = deadline - observed_at
        time.sleep(min(0.01, remaining))


def _terminate_compile_process_tree(
    process: subprocess.Popen[Any],
    windows_job: WindowsJob | None,
) -> None:
    """Terminate the owned worker tree with a Darwin-aware POSIX recovery."""

    if os.name == "nt":
        terminate_process_tree(process, windows_job)
        return
    _terminate_compile_posix_process_tree(process, leader_exited=False)


def _terminate_compile_posix_process_tree(
    process: subprocess.Popen[Any],
    *,
    leader_exited: bool,
) -> None:
    """Terminate one anchored compile group before its leader is reaped."""

    if leader_exited:
        _force_compile_process_group(process)
        return

    try:
        os.killpg(process.pid, _POSIX_SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError as exc:
        if exc.errno != errno.EPERM:
            raise CompilationIsolationError(
                "could not terminate the isolated compilation process group"
            ) from exc
        if (
            sys.platform == "darwin"
            and _posix_worker_exited_without_reaping(process)
        ):
            prove_darwin_process_group_all_zombies(
                process.pid,
                expected_leader_pid=process.pid,
                error_type=CompilationIsolationError,
            )
            return
        raise CompilationIsolationError(
            "could not terminate the isolated compilation process group"
        ) from exc
    except OSError as exc:
        raise CompilationIsolationError(
            "could not terminate the isolated compilation process group"
        ) from exc

    deadline = time.monotonic() + _PROCESS_GROUP_GRACE_SECONDS
    while True:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        except PermissionError as exc:
            if exc.errno != errno.EPERM:
                raise CompilationIsolationError(
                    "could not verify termination of the isolated "
                    "compilation process group"
                ) from exc
            if sys.platform != "darwin":
                raise CompilationIsolationError(
                    "could not verify termination of the isolated compilation process group"
                ) from exc
            prove_darwin_process_group_all_zombies(
                process.pid,
                expected_leader_pid=process.pid,
                error_type=CompilationIsolationError,
            )
            return
        except OSError as exc:
            raise CompilationIsolationError(
                "could not verify termination of the isolated compilation "
                "process group"
            ) from exc
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.01, remaining))
    _force_compile_process_group(process)


def _reap_compile_process(process: subprocess.Popen[Any]) -> None:
    """Reap one worker after its platform tree boundary has been closed."""

    try:
        process.wait(timeout=1)
        return
    except subprocess.TimeoutExpired:
        pass
    except OSError as exc:
        raise CompilationIsolationError(
            "could not reap the isolated compilation worker"
        ) from exc

    kill_error: OSError | None = None
    try:
        process.kill()
    except OSError as exc:
        kill_error = exc
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired as exc:
        if kill_error is not None:
            raise CompilationIsolationError(
                "could not kill the isolated compilation worker after "
                "process-group cleanup"
            ) from kill_error
        raise CompilationIsolationError(
            "isolated compilation worker did not exit after direct kill"
        ) from exc
    except OSError as exc:
        if kill_error is not None:
            raise CompilationIsolationError(
                "could not kill the isolated compilation worker after "
                "process-group cleanup"
            ) from kill_error
        raise CompilationIsolationError(
            "could not reap the isolated compilation worker after direct kill"
        ) from exc
    if kill_error is not None:
        raise CompilationIsolationError(
            "could not kill the isolated compilation worker after "
            "process-group cleanup"
        ) from kill_error


def _finalize_posix_compile_process(
    process: subprocess.Popen[Any],
    *,
    leader_exited: bool,
) -> None:
    """Close the anchored POSIX group, then and only then reap its leader."""

    if process.returncode is not None:
        raise CompilationIsolationError(
            "isolated compilation worker was reaped before process-group cleanup"
        )
    _terminate_compile_posix_process_tree(
        process,
        leader_exited=leader_exited,
    )
    _reap_compile_process(process)


def _kill_and_reap_direct_compile_process(
    process: subprocess.Popen[Any],
) -> None:
    """Best-effort cleanup using only the still-owned child PID."""

    with suppress(OSError, subprocess.SubprocessError):
        if process.returncode is None:
            process.kill()
    with suppress(OSError, subprocess.SubprocessError):
        process.wait(timeout=1)


def _finalize_posix_compile_process_fail_closed(
    process: subprocess.Popen[Any],
    *,
    leader_exited: bool,
) -> None:
    """Preserve group-cleanup errors after direct-child fallback cleanup."""

    try:
        _finalize_posix_compile_process(
            process,
            leader_exited=leader_exited,
        )
    except Exception:
        _kill_and_reap_direct_compile_process(process)
        raise


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

    with tempfile.TemporaryDirectory(
        prefix="ctxc-compile-",
        dir=_resolved_temporary_root(),
    ) as directory:
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
        posix_leader_exited = False
        posix_group_finalization_started = False
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
                    _terminate_compile_process_tree(process, windows_job)
                    _reap_compile_process(process)
                    raise
            try:
                remaining = _remaining_seconds(started, timeout)
            except TimeoutError:
                timed_out = True
            else:
                try:
                    if os.name == "nt":
                        process.wait(timeout=remaining)
                    else:
                        _wait_posix_worker_without_reaping(
                            process,
                            timeout=remaining,
                        )
                        posix_leader_exited = True
                except subprocess.TimeoutExpired:
                    timed_out = True
            if os.name == "nt":
                if timed_out:
                    _terminate_compile_process_tree(process, windows_job)
                    _reap_compile_process(process)
            else:
                posix_group_finalization_started = True
                _finalize_posix_compile_process_fail_closed(
                    process,
                    leader_exited=posix_leader_exited,
                )
        finally:
            if os.name == "nt":
                if process is not None and process.poll() is None:
                    _terminate_compile_process_tree(process, windows_job)
                    _reap_compile_process(process)
                if windows_job is not None:
                    try:
                        windows_job.terminate()
                    finally:
                        windows_job.close()
            elif process is not None and not posix_group_finalization_started:
                posix_group_finalization_started = True
                _finalize_posix_compile_process_fail_closed(
                    process,
                    leader_exited=posix_leader_exited,
                )

        if timed_out:
            raise TimeoutError(
                f"compilation exceeded its {timeout:g}-second deadline; "
                "owned process-boundary cleanup completed before worker reap"
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
