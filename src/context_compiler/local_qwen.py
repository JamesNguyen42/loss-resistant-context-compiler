"""Strict, API-free LM Studio CLI adapter for the approved local Qwen model."""

from __future__ import annotations

import codecs
import json
import math
import os
import re
import subprocess
import threading
import time
import unicodedata
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .process_tree import (
    WINDOWS_CREATE_SUSPENDED,
    WindowsJob,
    resume_windows_process,
    terminate_posix_process_group,
    terminate_process_tree,
)

QWEN_MODEL_KEY = "qwen/qwen3.6-35b-a3b"
QWEN_Q4_VARIANT = "qwen/qwen3.6-35b-a3b@q4_k_m"
QWEN_Q4_QUANTIZATION = "Q4_K_M"
QWEN_Q4_CONTEXT_LENGTH = 8_192
_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_INFERENCE_LOCK = threading.Lock()
_MAX_LOADING_STATUS_PREFIX_CHARS = 4_096
_MAX_LOADING_STATUS_LINES = 64
_MAX_LOADING_STATUS_SUFFIX_CHARS = 32
_MAX_JSON_DEPTH = 64
_MAX_JSON_INTEGER_CHARS = 64
_KNOWN_MOJIBAKE_SPINNER = "\u00e2\u00a0\u00b9"


class LocalQwenError(RuntimeError):
    """The local Qwen CLI could not safely produce a completion."""


def _validate_json_depth(raw: str, *, source: str) -> None:
    depth = 0
    in_string = False
    escaped = False
    for character in raw:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > _MAX_JSON_DEPTH:
                raise LocalQwenError(f"{source} exceeded the supported JSON nesting depth")
        elif character in "]}":
            depth = max(0, depth - 1)


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    decoded: dict[str, Any] = {}
    for key, value in pairs:
        if key in decoded:
            raise ValueError("duplicate JSON object key")
        decoded[key] = value
    return decoded


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-standard JSON constant")


def _finite_json_float(value: str) -> float:
    decoded = float(value)
    if not math.isfinite(decoded):
        raise ValueError("JSON number must be finite")
    return decoded


def _bounded_json_int(value: str) -> int:
    if len(value.removeprefix("-")) > _MAX_JSON_INTEGER_CHARS:
        raise ValueError("JSON integer exceeds the supported length")
    return int(value)


def _strict_json_decoder() -> json.JSONDecoder:
    return json.JSONDecoder(
        object_pairs_hook=_strict_json_object,
        parse_float=_finite_json_float,
        parse_int=_bounded_json_int,
        parse_constant=_reject_json_constant,
    )


def _run_bounded_process(
    command: list[str],
    *,
    timeout: float,
    creationflags: int,
    max_output_chars: int,
) -> subprocess.CompletedProcess[str]:
    """Run one owned CLI process tree while retaining bounded strict UTF-8."""

    started = time.monotonic()
    windows_job: WindowsJob | None = None
    effective_creationflags = creationflags
    if os.name == "nt":
        effective_creationflags |= (
            subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            | WINDOWS_CREATE_SUSPENDED
        )
        windows_job = WindowsJob.create(error_type=LocalQwenError)

    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            close_fds=True,
            start_new_session=os.name != "nt",
            creationflags=effective_creationflags,
        )
        if windows_job is not None:
            windows_job.assign(process)
            if not windows_job.contains(process):
                raise LocalQwenError("LM Studio CLI escaped its Windows Job Object")
            resume_windows_process(process, error_type=LocalQwenError)
    except BaseException:
        if process is not None:
            if windows_job is not None:
                with suppress(RuntimeError):
                    windows_job.terminate()
            elif os.name != "nt":
                with suppress(OSError):
                    terminate_posix_process_group(process.pid)
            with suppress(OSError):
                process.kill()
            with suppress(OSError, subprocess.TimeoutExpired):
                process.wait(timeout=1.0)
        if windows_job is not None:
            windows_job.close()
        raise

    if process.stdout is None:  # pragma: no cover - guaranteed by stdout=PIPE
        if windows_job is not None:
            with suppress(RuntimeError):
                windows_job.terminate()
            windows_job.close()
        elif os.name != "nt":
            with suppress(OSError):
                terminate_posix_process_group(process.pid)
        with suppress(OSError):
            process.kill()
        with suppress(OSError, subprocess.TimeoutExpired):
            process.wait(timeout=1.0)
        raise OSError("LM Studio CLI stdout pipe was not created")

    stdout = process.stdout
    timed_out = threading.Event()
    finished = threading.Event()
    termination_lock = threading.Lock()
    termination_started = False
    termination_failed = False

    def terminate_owned_tree() -> None:
        nonlocal termination_started, termination_failed
        with termination_lock:
            if termination_started:
                return
            termination_started = True
        try:
            if os.name == "nt":
                if windows_job is not None:
                    windows_job.terminate()
                    if process.poll() is None:
                        process.kill()
                else:  # pragma: no cover - Windows always creates a Job Object
                    terminate_process_tree(process)
            else:
                terminate_posix_process_group(process.pid)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            termination_failed = True
            with suppress(OSError):
                process.kill()

    def expire() -> None:
        if finished.is_set():
            return
        timed_out.set()
        terminate_owned_tree()
        with suppress(OSError, ValueError):
            stdout.close()

    deadline = started + timeout
    timer = threading.Timer(max(0.0, deadline - time.monotonic()), expire)
    timer.daemon = True
    timer.start()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    chunks: list[str] = []
    retained_chars = 0
    exceeded = False
    decode_failed = False
    read_failed = False
    returncode: int | None = None
    cleanup_failed = False
    try:
        while True:
            try:
                chunk = stdout.read(64 * 1024)
            except (OSError, ValueError):
                if timed_out.is_set():
                    break
                raise
            if not chunk:
                break
            decoded = decoder.decode(chunk)
            remaining = max_output_chars - retained_chars
            if len(decoded) > remaining:
                exceeded = True
                terminate_owned_tree()
                break
            chunks.append(decoded)
            retained_chars += len(decoded)
        if not exceeded and not timed_out.is_set():
            decoded = decoder.decode(b"", final=True)
            if len(decoded) > max_output_chars - retained_chars:
                exceeded = True
                terminate_owned_tree()
            else:
                chunks.append(decoded)
    except UnicodeDecodeError:
        decode_failed = True
        read_failed = True
        terminate_owned_tree()
    except BaseException:
        read_failed = True
        terminate_owned_tree()
        raise
    finally:
        with suppress(OSError, ValueError):
            stdout.close()
        finished.set()
        timer.cancel()
        timer.join()
        if process.poll() is None and not (read_failed or exceeded or timed_out.is_set()):
            try:
                returncode = process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out.set()
                terminate_owned_tree()
        if process.poll() is None:
            terminate_owned_tree()
            try:
                returncode = process.wait(timeout=1.0)
            except (OSError, subprocess.TimeoutExpired):
                with suppress(OSError):
                    process.kill()
                try:
                    returncode = process.wait(timeout=1.0)
                except (OSError, subprocess.TimeoutExpired):
                    cleanup_failed = True
        else:
            returncode = process.returncode
        terminate_owned_tree()
        if process.poll() is None:
            cleanup_failed = True
        if termination_failed:
            cleanup_failed = True
        if windows_job is not None:
            windows_job.close()
    if cleanup_failed:
        raise LocalQwenError("LM Studio CLI process tree could not be terminated")
    if timed_out.is_set():
        raise subprocess.TimeoutExpired(command, timeout)
    if exceeded:
        raise LocalQwenError("LM Studio CLI output exceeded the configured limit")
    if decode_failed:
        raise LocalQwenError("LM Studio CLI output was not valid UTF-8")
    if returncode is None:  # pragma: no cover - guarded by cleanup checks
        raise LocalQwenError("LM Studio CLI process status was unavailable")
    return subprocess.CompletedProcess(
        command,
        returncode,
        stdout="".join(chunks),
        stderr="",
    )


@dataclass(frozen=True, slots=True)
class LmsQwenCompletion:
    """Call exactly Qwen 3.6-35B-A3B Q4 through the local ``lms`` CLI.

    This adapter never uses an HTTP model API and passes ``--dont-fetch-catalog``.
    It verifies the on-disk and loaded model identities before each completion,
    requires LM Studio to expose one inference slot, and serializes all calls
    through a process-wide lock. Chat stdout must contain one JSON object,
    optionally after a bounded sequence of loading-status lines for that exact
    model; ambiguous prefixes or trailing payloads fail closed.

    LM Studio's CLI accepts the prompt as a command-line argument. On operating
    systems where process arguments are visible to other local users, callers
    must treat that as a confidentiality boundary and redact secrets first.
    """

    lms_executable: str | Path
    model_key: str = QWEN_MODEL_KEY
    expected_variant: str = QWEN_Q4_VARIANT
    timeout_seconds: float = 120.0
    max_output_chars: int = 1_000_000
    require_single_concurrency: bool = True

    def __post_init__(self) -> None:
        executable = Path(self.lms_executable).expanduser().resolve()
        if not executable.is_file():
            raise FileNotFoundError(f"LM Studio CLI not found: {executable}")
        object.__setattr__(self, "lms_executable", executable)
        if self.model_key != QWEN_MODEL_KEY:
            raise ValueError(f"only {QWEN_MODEL_KEY!r} is allowed")
        if self.expected_variant != QWEN_Q4_VARIANT:
            raise ValueError(f"only {QWEN_Q4_VARIANT!r} is allowed")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or self.timeout_seconds <= 0
            or not math.isfinite(float(self.timeout_seconds))
        ):
            raise ValueError("timeout_seconds must be a positive number")
        if (
            isinstance(self.max_output_chars, bool)
            or not isinstance(self.max_output_chars, int)
            or self.max_output_chars <= 0
        ):
            raise ValueError("max_output_chars must be a positive integer")
        if not isinstance(self.require_single_concurrency, bool):
            raise TypeError("require_single_concurrency must be a boolean")
        if self.require_single_concurrency is not True:
            raise ValueError("require_single_concurrency must remain true")

    @property
    def model_id(self) -> str:
        return self.expected_variant

    @staticmethod
    def _split_chat_output(value: str) -> tuple[str, str]:
        prefix_characters: list[str] = []
        index = 0
        while index < len(value):
            character = value[index]
            if character == "{":
                return "".join(prefix_characters), value[index:]
            if index >= _MAX_LOADING_STATUS_PREFIX_CHARS:
                raise LocalQwenError("local Qwen chat output had an invalid status prefix")
            if character == "\x1b":
                escape = _ANSI_ESCAPE.match(value, index)
                if escape is None or escape.end() > _MAX_LOADING_STATUS_PREFIX_CHARS:
                    raise LocalQwenError("local Qwen chat output had an invalid status prefix")
                index = escape.end()
                continue
            if character != "\r":
                prefix_characters.append(character)
            index += 1
        raise LocalQwenError("local Qwen chat output did not contain a JSON object")

    @staticmethod
    def _valid_loading_status_suffix(suffix: str) -> bool:
        if not suffix:
            return True
        if len(suffix) > _MAX_LOADING_STATUS_SUFFIX_CHARS or not suffix.startswith(" "):
            return False
        status = suffix[1:]
        if status == _KNOWN_MOJIBAKE_SPINNER:
            return True
        if not status:
            return False
        return not any(
            character in "{}[]\"'`\\"
            or unicodedata.category(character)[0] in {"C", "L", "M", "N", "Z"}
            for character in status
        )

    def _frame_chat_output(self, value: str) -> str:
        """Return one unchanged strict JSON object after a known CLI prefix."""

        prefix, candidate = self._split_chat_output(value)
        if prefix:
            if not prefix.endswith("\n"):
                raise LocalQwenError("local Qwen chat output had an invalid status prefix")
            lines = prefix.splitlines()
            if not lines or len(lines) > _MAX_LOADING_STATUS_LINES:
                raise LocalQwenError("local Qwen chat output had an invalid status prefix")
            expected = f"Loading {self.model_key}"
            for line in lines:
                if not line.startswith(expected):
                    raise LocalQwenError("local Qwen chat output had an unrecognized status prefix")
                suffix = line[len(expected) :]
                if not self._valid_loading_status_suffix(suffix):
                    raise LocalQwenError("local Qwen chat output had an invalid status prefix")
        _validate_json_depth(candidate, source="local Qwen chat output")
        decoded: Any = None
        end = 0
        invalid_json = False
        try:
            decoded, end = _strict_json_decoder().raw_decode(candidate)
        except (ValueError, RecursionError):
            invalid_json = True
        if invalid_json:
            raise LocalQwenError("local Qwen chat output was not valid JSON")
        if not isinstance(decoded, dict):
            raise LocalQwenError("local Qwen chat output was not a JSON object")
        if any(character not in " \t\r\n" for character in candidate[end:]):
            raise LocalQwenError("local Qwen chat output contained trailing data")
        return candidate[:end]

    def _run(self, arguments: list[str], *, timeout: float = 30.0) -> str:
        creation_flags = (
            subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
            if os.name == "nt"
            else 0
        )
        completed: subprocess.CompletedProcess[str] | None = None
        timed_out = False
        execution_error: str | None = None
        try:
            completed = _run_bounded_process(
                [str(self.lms_executable), *arguments],
                timeout=timeout,
                creationflags=creation_flags,
                max_output_chars=self.max_output_chars,
            )
        except subprocess.TimeoutExpired:
            timed_out = True
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            execution_error = type(exc).__name__
        if timed_out:
            raise TimeoutError("local Qwen CLI timed out")
        if execution_error is not None:
            raise LocalQwenError(f"LM Studio CLI could not be executed ({execution_error})")
        if completed is None:  # pragma: no cover - all branches assign or fail
            raise LocalQwenError("LM Studio CLI process result was unavailable")
        if completed.returncode != 0:
            raise LocalQwenError(f"LM Studio CLI exited with status {completed.returncode}")
        if len(completed.stdout) > self.max_output_chars:
            raise LocalQwenError("LM Studio CLI output exceeded the configured limit")
        return completed.stdout

    @staticmethod
    def _decode_model_list(raw: str, *, source: str) -> list[dict[str, Any]]:
        _validate_json_depth(raw, source=source)
        decoded: Any = None
        invalid_json = False
        try:
            decoded = _strict_json_decoder().decode(raw)
        except (RecursionError, ValueError):
            invalid_json = True
        if invalid_json:
            raise LocalQwenError(f"{source} did not return valid JSON")
        if not isinstance(decoded, list) or not all(isinstance(item, dict) for item in decoded):
            raise LocalQwenError(f"{source} returned an invalid model list")
        return decoded

    def _find_unique_model(
        self,
        models: list[dict[str, Any]],
        *,
        source: str,
    ) -> dict[str, Any] | None:
        matches = [item for item in models if item.get("modelKey") == self.model_key]
        if len(matches) > 1:
            raise LocalQwenError(f"{source} returned multiple entries for the required model")
        return matches[0] if matches else None

    def _is_exact_qwen_build(self, model: dict[str, Any]) -> bool:
        quantization = model.get("quantization")
        return (
            model.get("type") == "llm"
            and model.get("modelKey") == self.model_key
            and model.get("publisher") == "qwen"
            and model.get("selectedVariant") == self.expected_variant
            and isinstance(quantization, dict)
            and quantization.get("name") == QWEN_Q4_QUANTIZATION
            and type(quantization.get("bits")) is int
            and quantization["bits"] == 4
        )

    def preflight(self) -> dict[str, Any]:
        """Verify exact local model, quantization, loaded state, and concurrency."""

        available = self._decode_model_list(
            self._run(["ls", "--llm", "--json"]),
            source="lms ls",
        )
        disk_model = self._find_unique_model(
            available,
            source="lms ls",
        )
        if disk_model is None:
            raise LocalQwenError(f"required local model is missing: {self.model_key}")
        if not self._is_exact_qwen_build(disk_model):
            raise LocalQwenError("on-disk model does not match the required Qwen Q4 build")

        loaded = self._decode_model_list(
            self._run(["ps", "--json"]),
            source="lms ps",
        )
        active = self._find_unique_model(
            loaded,
            source="lms ps",
        )
        if active is None or active.get("status") not in {"idle", "loaded"}:
            raise LocalQwenError("required Qwen model is not loaded and ready")
        if not self._is_exact_qwen_build(active):
            raise LocalQwenError("loaded model does not match the required Qwen Q4 build")
        parallel = active.get("parallel")
        if type(parallel) is not int or parallel != 1:
            raise LocalQwenError("local Qwen must be loaded with exactly one inference slot")
        context_length = active.get("contextLength")
        if type(context_length) is not int or context_length != QWEN_Q4_CONTEXT_LENGTH:
            raise LocalQwenError("local Qwen must be loaded with the required 8192-token context")
        return {
            "model_id": self.expected_variant,
            "quantization": QWEN_Q4_QUANTIZATION,
            "parallel": parallel,
            "context_length": context_length,
            "transport": "lm-studio-cli",
            "network_model_api": False,
            "command_line_prompt_exposure": True,
        }

    def __call__(self, prompt: str) -> str:
        if not isinstance(prompt, str):
            raise TypeError("prompt must be a string")
        if not _INFERENCE_LOCK.acquire(blocking=False):
            raise LocalQwenError("a local Qwen inference is already in progress")
        try:
            self.preflight()
            output = self._run(
                [
                    "chat",
                    self.model_key,
                    "--prompt",
                    prompt,
                    "--system-prompt",
                    "Return only the requested JSON object with no Markdown.",
                    "--reasoning",
                    "off",
                    "--dont-fetch-catalog",
                    "--yes",
                ],
                timeout=float(self.timeout_seconds),
            )
        finally:
            _INFERENCE_LOCK.release()
        if len(output) > self.max_output_chars:
            raise LocalQwenError("local Qwen output exceeded the configured limit")
        return self._frame_chat_output(output)
