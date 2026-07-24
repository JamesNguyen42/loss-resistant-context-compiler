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
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from context_compiler.local_qwen import QWEN_Q4_VARIANT

from .lrcbench import (
    CANDIDATE_SCHEMA,
    CORPUS_SCHEMA,
    ExternalBaselineError,
    decode_corpus_document,
    decode_external_candidate,
)

RUNNER_MANIFEST_SCHEMA = "lrcbench-external-run-manifest-0.1"
_SYSTEM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


class ExternalRunnerError(RuntimeError):
    """The adapter runner could not establish a safe execution boundary."""


@dataclass(frozen=True, slots=True)
class ExternalRunReference:
    system: str
    candidate_path: Path | None
    failure_reason: str | None
    manifest_sha256: str


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
            self.adapter_revision != "unrecorded"
            and self.environment_id != "unrecorded"
            and self.model_id == QWEN_Q4_VARIANT
            and self.model_context_length > 0
            and self.tokenizer_id != "unrecorded"
            and self.inference_concurrency == 1
            and self.model_service_cost_usd == 0.0
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
            if isinstance(self.max_memory_mb, bool) or not isinstance(
                self.max_memory_mb, int
            ):
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
class ExternalRunManifest:
    system: str
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


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{64}", value) is not None
    )


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


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


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
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
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


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
    if not path.is_file():
        raise ExternalRunnerError(f"corpus file was not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return decode_corpus_document(payload, source_label=str(path))
    except (OSError, UnicodeError, json.JSONDecodeError, ExternalBaselineError) as exc:
        raise ExternalRunnerError(f"invalid corpus: {exc}") from exc


def load_external_run_manifest(
    path: Path | str,
    *,
    expected_dataset_sha256: str,
) -> ExternalRunReference:
    """Validate a runner manifest and its ready candidate without executing it."""

    manifest_path = Path(path).expanduser().resolve()
    if not manifest_path.is_file():
        raise ExternalRunnerError(f"run manifest was not found: {manifest_path}")
    if manifest_path.stat().st_size > 5_000_000:
        raise ExternalRunnerError("run manifest exceeded the 5 MB limit")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExternalRunnerError(f"invalid run manifest JSON: {exc}") from exc
    if not isinstance(payload, dict) or not all(
        isinstance(key, str) for key in payload
    ):
        raise ExternalRunnerError("run manifest must be a JSON object")
    expected_keys = {
        "schema",
        "manifest_sha256",
        *ExternalRunManifest.__dataclass_fields__,
    }
    if set(payload) != expected_keys:
        raise ExternalRunnerError("run manifest fields do not match the schema")
    if payload["schema"] != RUNNER_MANIFEST_SCHEMA:
        raise ExternalRunnerError(
            f"run manifest schema must be {RUNNER_MANIFEST_SCHEMA!r}"
        )
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
    if not isinstance(command, list) or not all(
        isinstance(part, str) and part for part in command
    ):
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
    if (
        payload["claim_metadata_complete"]
        != decoded_identity.claim_metadata_complete
    ):
        raise ExternalRunnerError("run manifest identity-completeness flag is inconsistent")
    for name in ("stdout_sha256", "stderr_sha256", "corpus_sha256", "corpus_file_sha256"):
        if not _is_sha256(payload[name]):
            raise ExternalRunnerError(f"run manifest {name} is invalid")
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
    if exit_code is not None and (
        isinstance(exit_code, bool) or not isinstance(exit_code, int)
    ):
        raise ExternalRunnerError("run manifest exit_code is invalid")

    candidate_path: Path | None = None
    failure_reason: str | None = None
    if ready:
        raw_candidate_path = payload["candidate_path"]
        if not isinstance(raw_candidate_path, str) or not raw_candidate_path:
            raise ExternalRunnerError("run manifest candidate_path is invalid")
        candidate_path = Path(raw_candidate_path).expanduser().resolve()
        if not candidate_path.is_file():
            raise ExternalRunnerError(
                f"manifest candidate was not found: {candidate_path}"
            )
        candidate_bytes = payload["candidate_bytes"]
        candidate_sha256 = payload["candidate_sha256"]
        if (
            isinstance(candidate_bytes, bool)
            or not isinstance(candidate_bytes, int)
            or candidate_bytes < 0
            or candidate_path.stat().st_size != candidate_bytes
        ):
            raise ExternalRunnerError("manifest candidate byte count is invalid")
        if (
            not _is_sha256(candidate_sha256)
            or _file_sha256(candidate_path) != candidate_sha256
        ):
            raise ExternalRunnerError("manifest candidate SHA-256 mismatch")
        if not decoded_identity.claim_metadata_complete:
            failure_reason = (
                "run manifest lacks complete zero-cost exact-Qwen identity metadata"
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
        failure_reason=failure_reason,
        manifest_sha256=claimed_manifest_sha,
    )


def run_external_command(
    command: Sequence[str],
    *,
    system: str,
    corpus_path: Path | str,
    candidate_path: Path | str,
    limits: RunnerLimits | None = None,
    identity: RunnerIdentity | None = None,
    working_directory: Path | str | None = None,
    environment: Mapping[str, str] | None = None,
) -> ExternalRunManifest:
    """Run one adapter command and validate its candidate output fail-closed."""

    if not isinstance(system, str) or _SYSTEM_RE.fullmatch(system) is None:
        raise ExternalRunnerError("system must be a valid LRCBench identifier")
    limits = limits or RunnerLimits()
    identity = identity or RunnerIdentity()
    if limits.max_memory_mb is not None and os.name == "nt":
        raise ExternalRunnerError(
            "max_memory_mb is unavailable on Windows; refusing to claim enforcement"
        )
    corpus = Path(corpus_path).expanduser().resolve()
    candidate = Path(candidate_path).expanduser().resolve()
    if candidate.exists():
        raise ExternalRunnerError(
            f"candidate output already exists; refusing to overwrite: {candidate}"
        )
    if not candidate.parent.is_dir():
        raise ExternalRunnerError(
            f"candidate output directory does not exist: {candidate.parent}"
        )
    config, cases, dataset_sha256 = _load_corpus(corpus)
    corpus_payload = json.loads(corpus.read_text(encoding="utf-8"))
    corpus_sha256 = corpus_payload["corpus_sha256"]
    corpus_file_sha256 = _file_sha256(corpus)

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
        for part in command
    )
    resolved_command = _resolve_command(substituted)
    process_environment = (
        dict(environment) if environment is not None else os.environ.copy()
    )
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
    with tempfile.TemporaryDirectory(
        prefix=".lrcbench-run-",
        dir=candidate.parent,
    ) as temporary_directory:
        stdout_path = Path(temporary_directory) / "stdout.bin"
        stderr_path = Path(temporary_directory) / "stderr.bin"
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
            except OSError as exc:
                raise ExternalRunnerError(
                    f"could not start adapter process: {type(exc).__name__}"
                ) from exc

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
                    _terminate_process_tree(process)
                    break
                time.sleep(float(limits.poll_interval_seconds))
            try:
                exit_code = process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                _terminate_process_tree(process)
                exit_code = process.wait(timeout=1)
            stdout.flush()
            stderr.flush()

        stdout_bytes = _size(stdout_path)
        stderr_bytes = _size(stderr_path)
        stdout_sha256 = _file_sha256(stdout_path)
        stderr_sha256 = _file_sha256(stderr_path)

    duration = time.monotonic() - started
    candidate_bytes = _size(candidate) if candidate.exists() else None
    if (
        termination_reason is None
        and (
            not corpus.is_file()
            or _file_sha256(corpus) != corpus_file_sha256
        )
    ):
        termination_reason = "corpus_modified"
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
    candidate_sha256 = (
        _file_sha256(candidate)
        if candidate.is_file()
        and candidate_bytes is not None
        and candidate_bytes <= limits.max_candidate_bytes
        else None
    )
    process_succeeded = exit_code == 0 and termination_reason is None
    candidate_valid = False
    validation_error: str | None = None
    if process_succeeded:
        if not candidate.is_file():
            validation_error = "adapter did not create the candidate output"
        else:
            try:
                candidate_payload = json.loads(candidate.read_text(encoding="utf-8"))
                candidate_system, _outputs = decode_external_candidate(
                    candidate_payload,
                    cases=cases,
                    dataset_sha256=dataset_sha256,
                    token_budget=config.token_budget,
                    source_label=str(candidate),
                )
                if candidate_system != system:
                    raise ExternalBaselineError(
                        f"candidate system {candidate_system!r} does not match "
                        f"registered system {system!r}"
                    )
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
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
        claim_metadata_complete=identity.claim_metadata_complete,
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
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--max-stdout-bytes", type=int, default=1_000_000)
    parser.add_argument("--max-stderr-bytes", type=int, default=1_000_000)
    parser.add_argument("--max-candidate-bytes", type=int, default=20_000_000)
    parser.add_argument("--max-memory-mb", type=int)
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
            "adapter command after --; {corpus}, {candidate}, and {system} "
            "are replaced without invoking a shell"
        ),
    )
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if args.manifest_out.expanduser().resolve() == args.candidate_out.expanduser().resolve():
        parser.error("manifest and candidate outputs must be different paths")
    if args.manifest_out.exists():
        parser.error(
            f"manifest output already exists; refusing to overwrite: "
            f"{args.manifest_out}"
        )
    if not args.manifest_out.parent.is_dir():
        parser.error(
            f"manifest output directory does not exist: "
            f"{args.manifest_out.parent}"
        )
    try:
        manifest = run_external_command(
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
        )
    except (ExternalRunnerError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    args.manifest_out.write_text(
        manifest.to_json() + "\n",
        encoding="utf-8",
    )
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
