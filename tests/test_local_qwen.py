from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

import context_compiler.local_qwen as local_qwen
from context_compiler import LmsQwenCompletion, LocalQwenError
from context_compiler.local_qwen import QWEN_MODEL_KEY, QWEN_Q4_VARIANT


def model_payload(*, parallel: int = 1, quantization: str = "Q4_K_M") -> dict:
    return {
        "type": "llm",
        "modelKey": QWEN_MODEL_KEY,
        "publisher": "qwen",
        "selectedVariant": QWEN_Q4_VARIANT,
        "quantization": {"name": quantization, "bits": 4},
        "status": "idle",
        "parallel": parallel,
        "contextLength": 8192,
    }


def completed(arguments: list[str], stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(arguments, 0, stdout=stdout, stderr="")


def test_cli_adapter_verifies_exact_qwen_q4_and_returns_clean_output(tmp_path) -> None:
    executable = tmp_path / "lms.exe"
    executable.write_bytes(b"placeholder")
    disk = json.dumps([model_payload()])
    loaded = json.dumps([model_payload()])
    response = '\r\x1b[K\x1b[?25h{"items":[]}\n'

    with patch(
        "context_compiler.local_qwen.subprocess.run",
        side_effect=[
            completed([], disk),
            completed([], loaded),
            completed([], response),
        ],
    ) as run:
        adapter = LmsQwenCompletion(executable)
        assert adapter("extract this") == '{"items":[]}'

    assert adapter.model_id == QWEN_Q4_VARIANT
    chat_command = run.call_args_list[-1].args[0]
    assert chat_command[1:4] == ["chat", QWEN_MODEL_KEY, "--prompt"]
    assert "--dont-fetch-catalog" in chat_command
    assert run.call_args_list[-1].kwargs["timeout"] == 120.0


def test_cli_adapter_frames_bounded_known_loading_status_prefix(tmp_path) -> None:
    executable = tmp_path / "lms.exe"
    executable.write_bytes(b"placeholder")
    adapter = LmsQwenCompletion(executable)
    response = (
        f"Loading {QWEN_MODEL_KEY} ⠙\n"
        f"Loading {QWEN_MODEL_KEY} â ¹\n"
        '{"items":[{"text":"brace } inside a string"}]}'
    )

    with patch.object(
        LmsQwenCompletion,
        "preflight",
        return_value={},
    ), patch.object(
        LmsQwenCompletion,
        "_run",
        return_value=response,
    ):
        assert adapter("extract this") == (
            '{"items":[{"text":"brace } inside a string"}]}'
        )


def test_cli_adapter_rejects_ambiguous_chat_framing_without_leaking_it(
    tmp_path,
) -> None:
    executable = tmp_path / "lms.exe"
    executable.write_bytes(b"placeholder")
    adapter = LmsQwenCompletion(executable)
    secret = "secret-status-value"
    invalid_outputs = (
        f"{secret}\n{{\"items\":[]}}",
        'Loading another/model ⠙\n{"items":[]}',
        f"Loading {QWEN_MODEL_KEY} {secret}\n{{\"items\":[]}}",
        f"Loading {QWEN_MODEL_KEY} {{{secret}}}\n{{\"items\":[]}}",
        '{"items":[]} trailing',
        '{"items":[]}{"items":[]}',
        '{"items":] }',
        '{"value":' + "9" * 5_000 + "}",
        "[]",
    )

    with patch.object(
        LmsQwenCompletion,
        "preflight",
        return_value={},
    ):
        for output in invalid_outputs:
            with patch.object(
                LmsQwenCompletion,
                "_run",
                return_value=output,
            ), pytest.raises(LocalQwenError) as raised:
                adapter("extract this")
            assert secret not in str(raised.value)
            assert raised.value.__cause__ is None
            assert raised.value.__context__ is None


def test_cli_adapter_bounds_loading_status_prefix_shape(tmp_path) -> None:
    executable = tmp_path / "lms.exe"
    executable.write_bytes(b"placeholder")
    adapter = LmsQwenCompletion(executable)
    too_many_lines = (
        "\n".join(f"Loading {QWEN_MODEL_KEY}" for _ in range(65))
        + '\n{"items":[]}'
    )
    too_long = (
        "\n".join(
            f"Loading {QWEN_MODEL_KEY} {'x' * 32}"
            for _ in range(64)
        )
        + '\n{"items":[]}'
    )

    with patch.object(
        LmsQwenCompletion,
        "preflight",
        return_value={},
    ):
        for output in (too_many_lines, too_long):
            with patch.object(
                LmsQwenCompletion,
                "_run",
                return_value=output,
            ), pytest.raises(LocalQwenError, match="invalid status prefix"):
                adapter("extract this")


def test_cli_adapter_rejects_wrong_model_identity_and_quantization(tmp_path) -> None:
    executable = tmp_path / "lms.exe"
    executable.write_bytes(b"placeholder")

    with pytest.raises(ValueError, match="only"):
        LmsQwenCompletion(executable, model_key="another/model")

    wrong_quantization = json.dumps([model_payload(quantization="Q8_0")])
    with patch(
        "context_compiler.local_qwen.subprocess.run",
        return_value=completed([], wrong_quantization),
    ), pytest.raises(LocalQwenError, match="Qwen Q4"):
        LmsQwenCompletion(executable).preflight()

    disk = json.dumps([model_payload()])
    wrong_loaded = json.dumps([model_payload(quantization="Q8_0")])
    with patch(
        "context_compiler.local_qwen.subprocess.run",
        side_effect=[completed([], disk), completed([], wrong_loaded)],
    ), pytest.raises(LocalQwenError, match="loaded model.*Qwen Q4"):
        LmsQwenCompletion(executable).preflight()


def test_cli_adapter_enforces_one_loaded_inference_slot(tmp_path) -> None:
    executable = tmp_path / "lms.exe"
    executable.write_bytes(b"placeholder")
    disk = json.dumps([model_payload()])
    loaded = json.dumps([model_payload(parallel=2)])

    with patch(
        "context_compiler.local_qwen.subprocess.run",
        side_effect=[completed([], disk), completed([], loaded)],
    ), pytest.raises(LocalQwenError, match="one inference slot"):
        LmsQwenCompletion(executable).preflight()


def test_cli_adapter_converts_chat_timeout_without_leaking_prompt(tmp_path) -> None:
    executable = tmp_path / "lms.exe"
    executable.write_bytes(b"placeholder")
    adapter = LmsQwenCompletion(executable)
    disk = json.dumps([model_payload()])
    loaded = json.dumps([model_payload()])
    secret = "secret-prompt-value"

    with patch(
        "context_compiler.local_qwen.subprocess.run",
        side_effect=[
            completed([], disk),
            completed([], loaded),
            subprocess.TimeoutExpired(["lms", "chat", secret], 1),
        ],
    ), pytest.raises(TimeoutError, match="timed out") as raised:
        adapter(secret)

    assert secret not in str(raised.value)


def test_cli_adapter_refuses_a_second_process_local_inference(tmp_path) -> None:
    executable = tmp_path / "lms.exe"
    executable.write_bytes(b"placeholder")
    adapter = LmsQwenCompletion(executable)

    with patch.object(LmsQwenCompletion, "preflight", return_value={}):
        assert local_qwen._INFERENCE_LOCK.acquire(blocking=False)
        try:
            with pytest.raises(LocalQwenError, match="already in progress"):
                adapter("second call")
        finally:
            local_qwen._INFERENCE_LOCK.release()


def test_cli_adapter_bounds_cli_output_before_returning_it(tmp_path) -> None:
    executable = tmp_path / "lms.exe"
    executable.write_bytes(b"placeholder")
    disk = json.dumps([model_payload()])
    loaded = json.dumps([model_payload()])
    adapter = LmsQwenCompletion(executable, max_output_chars=512)

    with patch(
        "context_compiler.local_qwen.subprocess.run",
        side_effect=[
            completed([], disk),
            completed([], loaded),
            completed([], "x" * 513),
        ],
    ), pytest.raises(LocalQwenError, match="output exceeded"):
        adapter("extract this")


def test_cli_adapter_normalizes_execution_failure_without_prompt_data(tmp_path) -> None:
    executable = tmp_path / "lms.exe"
    executable.write_bytes(b"placeholder")
    adapter = LmsQwenCompletion(executable)

    with patch(
        "context_compiler.local_qwen.subprocess.run",
        side_effect=OSError("secret operating-system detail"),
    ), pytest.raises(LocalQwenError, match="could not be executed") as raised:
        adapter.preflight()

    assert "secret operating-system detail" not in str(raised.value)
