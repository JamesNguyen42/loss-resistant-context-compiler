"""Strict, API-free LM Studio CLI adapter for the approved local Qwen model."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

QWEN_MODEL_KEY = "qwen/qwen3.6-35b-a3b"
QWEN_Q4_VARIANT = "qwen/qwen3.6-35b-a3b@q4_k_m"
QWEN_Q4_QUANTIZATION = "Q4_K_M"
QWEN_Q4_CONTEXT_LENGTH = 8_192
_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_INFERENCE_LOCK = threading.Lock()
_MAX_LOADING_STATUS_PREFIX_CHARS = 4_096
_MAX_LOADING_STATUS_LINES = 64
_MAX_LOADING_STATUS_SUFFIX_CHARS = 32


class LocalQwenError(RuntimeError):
    """The local Qwen CLI could not safely produce a completion."""


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

    @property
    def model_id(self) -> str:
        return self.expected_variant

    @staticmethod
    def _clean_output(value: str) -> str:
        return _ANSI_ESCAPE.sub("", value).replace("\r", "").strip()

    def _frame_chat_output(self, value: str) -> str:
        """Return one JSON object after a narrowly recognized CLI status prefix."""

        object_start = value.find("{")
        if object_start < 0:
            raise LocalQwenError(
                "local Qwen chat output did not contain a JSON object"
            )
        prefix = value[:object_start]
        candidate = value[object_start:]
        if prefix:
            if (
                len(prefix) > _MAX_LOADING_STATUS_PREFIX_CHARS
                or not prefix.endswith("\n")
            ):
                raise LocalQwenError(
                    "local Qwen chat output had an invalid status prefix"
                )
            lines = prefix.splitlines()
            if not lines or len(lines) > _MAX_LOADING_STATUS_LINES:
                raise LocalQwenError(
                    "local Qwen chat output had an invalid status prefix"
                )
            expected = f"Loading {self.model_key}"
            for line in lines:
                if not line.startswith(expected):
                    raise LocalQwenError(
                        "local Qwen chat output had an unrecognized status prefix"
                    )
                suffix = line[len(expected) :]
                if (
                    len(suffix) > _MAX_LOADING_STATUS_SUFFIX_CHARS
                    or (suffix and not suffix.startswith(" "))
                    or any(
                        character in '{}[]"\'`\\'
                        or ord(character) < 32
                        or (
                            character.isascii()
                            and character.isalnum()
                        )
                        for character in suffix
                    )
                ):
                    raise LocalQwenError(
                        "local Qwen chat output had an invalid status prefix"
                    )
        invalid_json = False
        try:
            decoded, end = json.JSONDecoder().raw_decode(candidate)
        except (ValueError, RecursionError):
            invalid_json = True
        if invalid_json:
            raise LocalQwenError(
                "local Qwen chat output was not valid JSON"
            )
        if not isinstance(decoded, dict):
            raise LocalQwenError(
                "local Qwen chat output was not a JSON object"
            )
        if candidate[end:].strip():
            raise LocalQwenError(
                "local Qwen chat output contained trailing data"
            )
        return candidate[:end]

    def _run(self, arguments: list[str], *, timeout: float = 30.0) -> str:
        creation_flags = (
            subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
            if os.name == "nt"
            else 0
        )
        try:
            completed = subprocess.run(
                [str(self.lms_executable), *arguments],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=creation_flags,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("local Qwen CLI timed out") from exc
        except OSError as exc:
            raise LocalQwenError(
                f"LM Studio CLI could not be executed ({type(exc).__name__})"
            ) from exc
        if completed.returncode != 0:
            raise LocalQwenError(
                f"LM Studio CLI exited with status {completed.returncode}"
            )
        if len(completed.stdout) > self.max_output_chars:
            raise LocalQwenError("LM Studio CLI output exceeded the configured limit")
        return self._clean_output(completed.stdout)

    @staticmethod
    def _decode_model_list(raw: str, *, source: str) -> list[dict[str, Any]]:
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LocalQwenError(f"{source} did not return valid JSON") from exc
        if not isinstance(decoded, list) or not all(
            isinstance(item, dict) for item in decoded
        ):
            raise LocalQwenError(f"{source} returned an invalid model list")
        return decoded

    def _is_exact_qwen_build(self, model: dict[str, Any]) -> bool:
        quantization = model.get("quantization")
        return (
            model.get("modelKey") == self.model_key
            and model.get("publisher") == "qwen"
            and model.get("selectedVariant") == self.expected_variant
            and isinstance(quantization, dict)
            and quantization.get("name") == QWEN_Q4_QUANTIZATION
            and quantization.get("bits") == 4
        )

    def preflight(self) -> dict[str, Any]:
        """Verify exact local model, quantization, loaded state, and concurrency."""

        available = self._decode_model_list(
            self._run(["ls", "--llm", "--json"]),
            source="lms ls",
        )
        disk_model = next(
            (item for item in available if item.get("modelKey") == self.model_key),
            None,
        )
        if disk_model is None:
            raise LocalQwenError(f"required local model is missing: {self.model_key}")
        if not self._is_exact_qwen_build(disk_model):
            raise LocalQwenError("on-disk model does not match the required Qwen Q4 build")

        loaded = self._decode_model_list(
            self._run(["ps", "--json"]),
            source="lms ps",
        )
        active = next(
            (item for item in loaded if item.get("modelKey") == self.model_key),
            None,
        )
        if active is None or active.get("status") not in {"idle", "loaded"}:
            raise LocalQwenError("required Qwen model is not loaded and ready")
        if not self._is_exact_qwen_build(active):
            raise LocalQwenError("loaded model does not match the required Qwen Q4 build")
        if self.require_single_concurrency and active.get("parallel") != 1:
            raise LocalQwenError("local Qwen must be loaded with exactly one inference slot")
        if active.get("contextLength") != QWEN_Q4_CONTEXT_LENGTH:
            raise LocalQwenError(
                "local Qwen must be loaded with the required 8192-token context"
            )
        return {
            "model_id": self.expected_variant,
            "quantization": QWEN_Q4_QUANTIZATION,
            "parallel": active.get("parallel"),
            "context_length": active.get("contextLength"),
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
