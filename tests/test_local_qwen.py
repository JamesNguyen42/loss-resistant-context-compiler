from __future__ import annotations

import json
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

import context_compiler.local_qwen as local_qwen
from context_compiler import LmsQwenCompletion, LocalQwenError
from context_compiler.local_qwen import QWEN_MODEL_KEY, QWEN_Q4_VARIANT


def model_payload(
    *,
    model_type: str = "llm",
    parallel: object = 1,
    quantization: str = "Q4_K_M",
    quantization_bits: object = 4,
    context_length: object = 8192,
) -> dict:
    return {
        "type": model_type,
        "modelKey": QWEN_MODEL_KEY,
        "publisher": "qwen",
        "selectedVariant": QWEN_Q4_VARIANT,
        "quantization": {"name": quantization, "bits": quantization_bits},
        "status": "idle",
        "parallel": parallel,
        "contextLength": context_length,
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
        "context_compiler.local_qwen._run_bounded_process",
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

    with (
        patch.object(
            LmsQwenCompletion,
            "preflight",
            return_value={},
        ),
        patch.object(
            LmsQwenCompletion,
            "_run",
            return_value=response,
        ),
    ):
        assert adapter("extract this") == ('{"items":[{"text":"brace } inside a string"}]}')


def test_cli_adapter_rejects_ambiguous_chat_framing_without_leaking_it(
    tmp_path,
) -> None:
    executable = tmp_path / "lms.exe"
    executable.write_bytes(b"placeholder")
    adapter = LmsQwenCompletion(executable)
    secret = "secret-status-value"
    invalid_outputs = (
        f'{secret}\n{{"items":[]}}',
        'Loading another/model ⠙\n{"items":[]}',
        f'Loading {QWEN_MODEL_KEY} {secret}\n{{"items":[]}}',
        f'Loading {QWEN_MODEL_KEY} \u0441\u0435\u043a\u0440\u0435\u0442\n{{"items":[]}}',
        f'Loading {QWEN_MODEL_KEY} {{{secret}}}\n{{"items":[]}}',
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
            with (
                patch.object(
                    LmsQwenCompletion,
                    "_run",
                    return_value=output,
                ),
                pytest.raises(LocalQwenError) as raised,
            ):
                adapter("extract this")
            assert secret not in str(raised.value)
            assert raised.value.__cause__ is None
            assert raised.value.__context__ is None


@pytest.mark.parametrize(
    "invalid",
    [
        '{"value":NaN}',
        '{"value":1e999}',
        '{"value":1,"value":2}',
        '{"value":' + "9" * 65 + "}",
        '{"value":' + ("[" * 65) + "0" + ("]" * 65) + "}",
    ],
)
def test_cli_adapter_strictly_decodes_chat_json(tmp_path, invalid: str) -> None:
    executable = tmp_path / "lms.exe"
    executable.write_bytes(b"placeholder")
    adapter = LmsQwenCompletion(executable)

    with (
        patch.object(LmsQwenCompletion, "preflight", return_value={}),
        patch.object(LmsQwenCompletion, "_run", return_value=invalid),
        pytest.raises(LocalQwenError),
    ):
        adapter("extract this")


@pytest.mark.parametrize(
    "invalid",
    [
        '{"value":"before\x1b[Kafter"}',
        '{"value":"before\rafter"}',
    ],
)
def test_cli_adapter_never_normalizes_json_candidate_bytes(
    tmp_path,
    invalid: str,
) -> None:
    executable = tmp_path / "lms.exe"
    executable.write_bytes(b"placeholder")
    adapter = LmsQwenCompletion(executable)

    with (
        patch.object(LmsQwenCompletion, "preflight", return_value={}),
        patch.object(LmsQwenCompletion, "_run", return_value=invalid),
        pytest.raises(LocalQwenError, match="not valid JSON"),
    ):
        adapter("extract this")


def test_cli_adapter_bounds_loading_status_prefix_shape(tmp_path) -> None:
    executable = tmp_path / "lms.exe"
    executable.write_bytes(b"placeholder")
    adapter = LmsQwenCompletion(executable)
    too_many_lines = "\n".join(f"Loading {QWEN_MODEL_KEY}" for _ in range(65)) + '\n{"items":[]}'
    too_long = (
        "\n".join(f"Loading {QWEN_MODEL_KEY} {'x' * 32}" for _ in range(64)) + '\n{"items":[]}'
    )

    with patch.object(
        LmsQwenCompletion,
        "preflight",
        return_value={},
    ):
        for output in (too_many_lines, too_long):
            with (
                patch.object(
                    LmsQwenCompletion,
                    "_run",
                    return_value=output,
                ),
                pytest.raises(LocalQwenError, match="invalid status prefix"),
            ):
                adapter("extract this")


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), float("-inf")])
def test_cli_adapter_rejects_non_finite_timeout(tmp_path, timeout: float) -> None:
    executable = tmp_path / "lms.exe"
    executable.write_bytes(b"placeholder")

    with pytest.raises(ValueError, match="positive number"):
        LmsQwenCompletion(executable, timeout_seconds=timeout)


def test_cli_adapter_rejects_wrong_model_identity_and_quantization(tmp_path) -> None:
    executable = tmp_path / "lms.exe"
    executable.write_bytes(b"placeholder")

    with pytest.raises(ValueError, match="only"):
        LmsQwenCompletion(executable, model_key="another/model")

    wrong_quantization = json.dumps([model_payload(quantization="Q8_0")])
    with (
        patch(
            "context_compiler.local_qwen._run_bounded_process",
            return_value=completed([], wrong_quantization),
        ),
        pytest.raises(LocalQwenError, match="Qwen Q4"),
    ):
        LmsQwenCompletion(executable).preflight()

    wrong_type = json.dumps([model_payload(model_type="embedding")])
    with (
        patch(
            "context_compiler.local_qwen._run_bounded_process",
            return_value=completed([], wrong_type),
        ),
        pytest.raises(LocalQwenError, match="Qwen Q4"),
    ):
        LmsQwenCompletion(executable).preflight()

    disk = json.dumps([model_payload()])
    wrong_loaded = json.dumps([model_payload(quantization="Q8_0")])
    with (
        patch(
            "context_compiler.local_qwen._run_bounded_process",
            side_effect=[completed([], disk), completed([], wrong_loaded)],
        ),
        pytest.raises(LocalQwenError, match="loaded model.*Qwen Q4"),
    ):
        LmsQwenCompletion(executable).preflight()


def test_cli_adapter_enforces_one_loaded_inference_slot(tmp_path) -> None:
    executable = tmp_path / "lms.exe"
    executable.write_bytes(b"placeholder")
    disk = json.dumps([model_payload()])

    with pytest.raises(ValueError, match="must remain true"):
        LmsQwenCompletion(executable, require_single_concurrency=False)

    for invalid_parallel in (2, True, 1.0):
        loaded = json.dumps([model_payload(parallel=invalid_parallel)])

        with (
            patch(
                "context_compiler.local_qwen._run_bounded_process",
                side_effect=[completed([], disk), completed([], loaded)],
            ),
            pytest.raises(LocalQwenError, match="one inference slot"),
        ):
            LmsQwenCompletion(executable).preflight()

    for invalid_context in (4096, True, 8192.0):
        wrong_context = json.dumps([model_payload(context_length=invalid_context)])
        with (
            patch(
                "context_compiler.local_qwen._run_bounded_process",
                side_effect=[completed([], disk), completed([], wrong_context)],
            ),
            pytest.raises(LocalQwenError, match="8192-token context"),
        ):
            LmsQwenCompletion(executable).preflight()


@pytest.mark.parametrize("invalid_bits", [True, 4.0])
def test_cli_adapter_rejects_numeric_aliases_for_quantization_bits(
    tmp_path,
    invalid_bits: object,
) -> None:
    executable = tmp_path / "lms.exe"
    executable.write_bytes(b"placeholder")
    invalid = json.dumps([model_payload(quantization_bits=invalid_bits)])

    with (
        patch(
            "context_compiler.local_qwen._run_bounded_process",
            return_value=completed([], invalid),
        ),
        pytest.raises(LocalQwenError, match="Qwen Q4"),
    ):
        LmsQwenCompletion(executable).preflight()


def test_cli_adapter_rejects_multiple_matching_model_entries(tmp_path) -> None:
    executable = tmp_path / "lms.exe"
    executable.write_bytes(b"placeholder")
    disk = json.dumps([model_payload()])
    duplicated = json.dumps([model_payload(), model_payload()])

    with (
        patch(
            "context_compiler.local_qwen._run_bounded_process",
            return_value=completed([], duplicated),
        ),
        pytest.raises(LocalQwenError, match="lms ls returned multiple entries"),
    ):
        LmsQwenCompletion(executable).preflight()

    with (
        patch(
            "context_compiler.local_qwen._run_bounded_process",
            side_effect=[completed([], disk), completed([], duplicated)],
        ),
        pytest.raises(LocalQwenError, match="lms ps returned multiple entries"),
    ):
        LmsQwenCompletion(executable).preflight()


@pytest.mark.parametrize(
    ("invalid", "message"),
    [
        (
            ('[{"modelKey":"qwen/qwen3.6-35b-a3b","modelKey":"qwen/qwen3.6-35b-a3b"}]'),
            "lms ls did not return valid JSON",
        ),
        (
            '[{"modelKey":"qwen/qwen3.6-35b-a3b","parallel":NaN}]',
            "lms ls did not return valid JSON",
        ),
        (
            '[{"modelKey":"qwen/qwen3.6-35b-a3b","parallel":1e999}]',
            "lms ls did not return valid JSON",
        ),
        (
            '[{"modelKey":"qwen/qwen3.6-35b-a3b","contextLength":' + "9" * 65 + "}]",
            "lms ls did not return valid JSON",
        ),
        (
            ("[" * 2_000) + ("]" * 2_000),
            "lms ls exceeded the supported JSON nesting depth",
        ),
    ],
)
def test_cli_adapter_strictly_decodes_model_lists(
    tmp_path,
    invalid: str,
    message: str,
) -> None:
    executable = tmp_path / "lms.exe"
    executable.write_bytes(b"placeholder")

    with (
        patch(
            "context_compiler.local_qwen._run_bounded_process",
            return_value=completed([], invalid),
        ),
        pytest.raises(LocalQwenError, match=message),
    ):
        LmsQwenCompletion(executable).preflight()


def test_cli_adapter_converts_chat_timeout_without_leaking_prompt(tmp_path) -> None:
    executable = tmp_path / "lms.exe"
    executable.write_bytes(b"placeholder")
    adapter = LmsQwenCompletion(executable)
    disk = json.dumps([model_payload()])
    loaded = json.dumps([model_payload()])
    secret = "secret-prompt-value"

    with (
        patch(
            "context_compiler.local_qwen._run_bounded_process",
            side_effect=[
                completed([], disk),
                completed([], loaded),
                subprocess.TimeoutExpired(["lms", "chat", secret], 1),
            ],
        ),
        pytest.raises(TimeoutError, match="timed out") as raised,
    ):
        adapter(secret)

    assert secret not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


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

    with (
        patch(
            "context_compiler.local_qwen._run_bounded_process",
            side_effect=[
                completed([], disk),
                completed([], loaded),
                completed([], "x" * 513),
            ],
        ),
        pytest.raises(LocalQwenError, match="output exceeded"),
    ):
        adapter("extract this")


def test_cli_adapter_streams_and_stops_oversized_process_output() -> None:
    adapter = LmsQwenCompletion(sys.executable, max_output_chars=512)

    with pytest.raises(LocalQwenError, match="output exceeded"):
        adapter._run(
            [
                "-c",
                ("import sys,time;sys.stdout.write('x'*100000);sys.stdout.flush();time.sleep(10)"),
            ],
            timeout=10,
        )


def test_cli_adapter_streaming_timeout_drops_prompt_bearing_cause() -> None:
    adapter = LmsQwenCompletion(sys.executable)
    secret = "secret-timeout-script"

    with pytest.raises(TimeoutError, match="timed out") as raised:
        adapter._run(["-c", f"import time;time.sleep(10)#{secret}"], timeout=0.2)

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert secret not in str(raised.value)


def test_cli_adapter_timeout_still_applies_after_stdout_closes() -> None:
    adapter = LmsQwenCompletion(sys.executable)

    with pytest.raises(TimeoutError, match="timed out"):
        adapter._run(
            ["-c", "import os,time;os.close(1);time.sleep(10)"],
            timeout=0.2,
        )


def test_cli_adapter_rejects_invalid_utf8_without_replacement() -> None:
    adapter = LmsQwenCompletion(sys.executable)

    with pytest.raises(LocalQwenError, match="not valid UTF-8") as raised:
        adapter._run(
            ["-c", "import os;os.write(1,bytes([255]))"],
            timeout=2,
        )

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_cli_adapter_timeout_terminates_descendant_tree(tmp_path) -> None:
    adapter = LmsQwenCompletion(sys.executable)
    parent_started = tmp_path / "parent-started"
    late_marker = tmp_path / "late-marker"
    child_code = (
        "import pathlib,sys,time;"
        "time.sleep(2);"
        "pathlib.Path(sys.argv[1]).write_text('late', encoding='utf-8')"
    )
    parent_code = (
        "import pathlib,subprocess,sys,time;"
        "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]);"
        "pathlib.Path(sys.argv[3]).write_text('started', encoding='utf-8');"
        "time.sleep(10)"
    )

    with pytest.raises(TimeoutError, match="timed out"):
        adapter._run(
            [
                "-c",
                parent_code,
                child_code,
                str(late_marker),
                str(parent_started),
            ],
            timeout=1.0,
        )

    assert parent_started.is_file()
    time.sleep(1.5)
    assert not late_marker.exists()


def test_cli_adapter_normalizes_execution_failure_without_prompt_data(tmp_path) -> None:
    executable = tmp_path / "lms.exe"
    executable.write_bytes(b"placeholder")
    adapter = LmsQwenCompletion(executable)

    with (
        patch(
            "context_compiler.local_qwen._run_bounded_process",
            side_effect=OSError("secret operating-system detail"),
        ),
        pytest.raises(LocalQwenError, match="could not be executed") as raised,
    ):
        adapter.preflight()

    assert "secret operating-system detail" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
