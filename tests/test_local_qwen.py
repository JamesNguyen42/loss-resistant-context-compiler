from __future__ import annotations

import errno
import io
import json
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import suppress
from unittest.mock import patch

import pytest

import context_compiler.local_qwen as local_qwen
import context_compiler.process_tree as process_tree
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


def install_bytesio_stdout_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_qwen, "_prepare_process_stdout", lambda _stdout: None)
    monkeypatch.setattr(
        local_qwen,
        "_read_process_stdout_chunk",
        lambda stdout, *, max_bytes: stdout.read(max_bytes),
    )


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


def test_posix_wait_rejects_zero_remaining_time_before_observing_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        args = ("lms", "chat")

    def reject_late_observation(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError("an exit observed at or after the deadline is too late")

    monkeypatch.setattr(local_qwen.time, "monotonic", lambda: 12.0)
    monkeypatch.setattr(
        local_qwen,
        "posix_process_exited_without_reaping",
        reject_late_observation,
    )

    with pytest.raises(subprocess.TimeoutExpired) as raised:
        local_qwen._wait_posix_process_without_reaping(  # type: ignore[arg-type]
            Process(),
            deadline=12.0,
            timeout=0.25,
        )

    assert raised.value.timeout == 0.25


@pytest.mark.skipif(os.name == "nt", reason="POSIX nonblocking pipe regression")
def test_posix_timeout_returns_while_an_unowned_writer_holds_stdout_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_fd, write_fd = os.pipe()
    stdout = os.fdopen(read_fd, "rb", buffering=0)
    events: list[str] = []
    failures: list[BaseException] = []

    class HeldOpenLeader:
        pid = 4309
        args = ("lms", "chat")
        returncode: int | None = None

        def __init__(self) -> None:
            self.stdout = stdout

        def poll(self) -> int | None:
            raise AssertionError("POSIX cleanup must not poll or reap the leader")

        def kill(self) -> None:
            raise AssertionError("successful group cleanup must not need PID fallback")

        def wait(self, *, timeout: float) -> int:
            assert timeout == 1.0
            events.append("wait")
            self.returncode = -signal.SIGKILL
            return self.returncode

    process = HeldOpenLeader()

    def popen(command: list[str], **kwargs: object) -> HeldOpenLeader:
        assert command == ["lms", "chat"]
        assert kwargs["start_new_session"] is True
        return process

    def terminate_group(
        candidate: object,
        *,
        error_type: type[RuntimeError],
    ) -> None:
        assert candidate is process
        assert error_type is LocalQwenError
        assert process.returncode is None
        events.append("group-cleanup")

    monkeypatch.setattr(local_qwen.subprocess, "Popen", popen)
    monkeypatch.setattr(
        local_qwen,
        "terminate_anchored_posix_process_group",
        terminate_group,
    )

    def invoke() -> None:
        try:
            local_qwen._run_bounded_process(
                ["lms", "chat"],
                timeout=0.05,
                creationflags=0,
                max_output_chars=512,
            )
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=invoke, daemon=True)
    worker.start()
    worker.join(timeout=1.0)
    was_stuck = worker.is_alive()
    with suppress(OSError):
        os.close(write_fd)
    if was_stuck:
        worker.join(timeout=1.0)
        pytest.fail("held-open stdout writer defeated the subprocess deadline")

    assert len(failures) == 1
    assert isinstance(failures[0], subprocess.TimeoutExpired)
    assert events == ["group-cleanup", "wait"]


def test_windows_timeout_polls_stdout_on_the_calling_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    reader_threads: list[int] = []

    class Stdout:
        def close(self) -> None:
            events.append("stdout-close")

    class Process:
        pid = 4308
        args = ("lms", "chat")
        stdout = Stdout()
        returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

        def kill(self) -> None:
            events.append("direct-kill")
            self.returncode = -getattr(signal, "SIGKILL", 9)

        def wait(self, *, timeout: float) -> int:
            raise AssertionError(f"terminated Windows process was not reaped: {timeout}")

    process = Process()

    class Job:
        def assign(self, candidate: object) -> None:
            assert candidate is process
            events.append("assign")

        def contains(self, candidate: object) -> bool:
            assert candidate is process
            events.append("contains")
            return True

        def terminate(self) -> None:
            events.append("job-terminate")
            process.returncode = -getattr(signal, "SIGKILL", 9)

        def close(self) -> None:
            events.append("job-close")

    job = Job()

    def read_no_data(_stdout: object, *, max_bytes: int) -> None:
        assert max_bytes == 64 * 1024
        reader_threads.append(threading.get_ident())
        return None

    monkeypatch.setattr(local_qwen.os, "name", "nt")
    monkeypatch.setattr(
        local_qwen.subprocess,
        "CREATE_NEW_PROCESS_GROUP",
        0x00000200,
        raising=False,
    )
    monkeypatch.setattr(
        local_qwen.WindowsJob,
        "create",
        lambda *, error_type: job,
    )
    monkeypatch.setattr(
        local_qwen.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )
    monkeypatch.setattr(
        local_qwen,
        "resume_windows_process",
        lambda candidate, *, error_type: events.append("resume"),
    )
    monkeypatch.setattr(
        local_qwen,
        "_read_process_stdout_chunk",
        read_no_data,
    )

    calling_thread = threading.get_ident()
    with pytest.raises(subprocess.TimeoutExpired):
        local_qwen._run_bounded_process(
            ["lms", "chat"],
            timeout=0.03,
            creationflags=0,
            max_output_chars=512,
        )

    assert reader_threads
    assert set(reader_threads) == {calling_thread}
    assert events[:3] == ["assign", "contains", "resume"]
    assert events.count("job-terminate") == 1
    assert events[-2:] == ["stdout-close", "job-close"]
    assert "direct-kill" not in events


@pytest.mark.skipif(os.name != "nt", reason="Windows PeekNamedPipe regression")
def test_windows_pipe_reader_is_nonblocking_bounded_and_observes_eof() -> None:
    read_fd, write_fd = os.pipe()
    stdout = os.fdopen(read_fd, "rb", buffering=0)
    try:
        assert (
            local_qwen._read_windows_pipe_chunk(stdout, max_bytes=3) is None
        )
        assert os.write(write_fd, b"abcdef") == 6
        assert local_qwen._read_windows_pipe_chunk(stdout, max_bytes=3) == b"abc"
        assert local_qwen._read_windows_pipe_chunk(stdout, max_bytes=8) == b"def"
        os.close(write_fd)
        write_fd = -1
        deadline = time.monotonic() + 1.0
        while True:
            chunk = local_qwen._read_windows_pipe_chunk(stdout, max_bytes=8)
            if chunk == b"":
                break
            assert chunk is None
            if time.monotonic() >= deadline:
                pytest.fail("PeekNamedPipe did not report closed stdout")
            time.sleep(0.01)
    finally:
        stdout.close()
        if write_fd >= 0:
            os.close(write_fd)


def test_posix_normal_completion_cleans_group_before_reaping_without_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int]] = []
    install_bytesio_stdout_reader(monkeypatch)

    class ExitedLeader:
        pid = 4312
        args = ("lms", "chat")
        returncode: int | None = None
        stdout = io.BytesIO(b'{"items":[]}')

        def poll(self) -> int | None:
            raise AssertionError("POSIX leader must not be polled before cleanup")

        def kill(self) -> None:
            raise AssertionError("successful group cleanup must not need PID fallback")

        def wait(self, *, timeout: float) -> int:
            assert timeout == 1.0
            assert self.returncode is None
            events.append(("wait", self.pid))
            self.returncode = 0
            return 0

    process = ExitedLeader()

    def popen(command: list[str], **kwargs: object) -> ExitedLeader:
        assert command == ["lms", "chat"]
        assert kwargs["start_new_session"] is True
        return process

    def waitid(id_type: int, process_id: int, options: int) -> object:
        assert process.returncode is None, "leader was reaped before WNOWAIT"
        assert id_type == 1
        assert options == 2 | 4 | 8
        events.append(("waitid", process_id))
        return object()

    def killpg(process_group_id: int, requested_signal: int) -> None:
        assert process.returncode is None, "numeric PGID used after leader reap"
        events.append(("signal", requested_signal))

    monkeypatch.setattr(local_qwen.os, "name", "posix")
    monkeypatch.setattr(process_tree.sys, "platform", "linux")
    monkeypatch.setattr(local_qwen.subprocess, "Popen", popen)
    monkeypatch.setattr(process_tree.os, "P_PID", 1, raising=False)
    monkeypatch.setattr(process_tree.os, "WEXITED", 2, raising=False)
    monkeypatch.setattr(process_tree.os, "WNOHANG", 4, raising=False)
    monkeypatch.setattr(process_tree.os, "WNOWAIT", 8, raising=False)
    monkeypatch.setattr(process_tree.os, "waitid", waitid, raising=False)
    monkeypatch.setattr(process_tree.os, "killpg", killpg, raising=False)

    completed_process = local_qwen._run_bounded_process(
        ["lms", "chat"],
        timeout=5.0,
        creationflags=0,
        max_output_chars=512,
    )

    assert completed_process.returncode == 0
    assert completed_process.stdout == '{"items":[]}'
    assert events == [
        ("waitid", process.pid),
        ("waitid", process.pid),
        ("signal", getattr(signal, "SIGKILL", 9)),
        ("wait", process.pid),
    ]


def test_anchored_posix_cleanup_rejects_an_already_reaped_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ReapedLeader:
        pid = 4313
        returncode = 0

    def reject_signal(_process_group_id: int, _requested_signal: int) -> None:
        raise AssertionError("an already-reaped numeric PGID must not be signaled")

    monkeypatch.setattr(process_tree.os, "killpg", reject_signal, raising=False)

    with pytest.raises(LocalQwenError, match="reaped before process-group cleanup"):
        process_tree.terminate_anchored_posix_process_group(  # type: ignore[arg-type]
            ReapedLeader(),
            error_type=LocalQwenError,
        )


@pytest.mark.parametrize("proof_rejects", [False, True])
def test_darwin_initial_sigterm_eperm_reobserves_and_proves_exited_leader(
    monkeypatch: pytest.MonkeyPatch,
    proof_rejects: bool,
) -> None:
    events: list[tuple[str, int]] = []
    observations = iter((None, object()))

    class RacingLeader:
        pid = 4318
        returncode = None

    def waitid(id_type: int, process_id: int, options: int) -> object | None:
        assert id_type == 1
        assert process_id == RacingLeader.pid
        assert options == 2 | 4 | 8
        events.append(("waitid", process_id))
        return next(observations)

    def deny_initial_signal(
        process_group_id: int,
        requested_signal: int,
    ) -> None:
        assert process_group_id == RacingLeader.pid
        assert requested_signal == signal.SIGTERM
        events.append(("signal", requested_signal))
        raise PermissionError(errno.EPERM, "Darwin zombie-only group")

    def prove_group(
        process_group_id: int,
        *,
        expected_leader_pid: int,
        error_type: type[RuntimeError],
    ) -> None:
        assert process_group_id == expected_leader_pid == RacingLeader.pid
        events.append(("proof", process_group_id))
        if proof_rejects:
            raise error_type("Darwin proof rejected a live survivor")

    monkeypatch.setattr(process_tree.sys, "platform", "darwin")
    monkeypatch.setattr(process_tree.os, "P_PID", 1, raising=False)
    monkeypatch.setattr(process_tree.os, "WEXITED", 2, raising=False)
    monkeypatch.setattr(process_tree.os, "WNOHANG", 4, raising=False)
    monkeypatch.setattr(process_tree.os, "WNOWAIT", 8, raising=False)
    monkeypatch.setattr(process_tree.os, "waitid", waitid, raising=False)
    monkeypatch.setattr(process_tree.os, "killpg", deny_initial_signal, raising=False)
    monkeypatch.setattr(
        process_tree,
        "prove_darwin_process_group_all_zombies",
        prove_group,
    )

    if proof_rejects:
        with pytest.raises(LocalQwenError, match="live survivor"):
            process_tree.terminate_anchored_posix_process_group(  # type: ignore[arg-type]
                RacingLeader(),
                error_type=LocalQwenError,
            )
    else:
        process_tree.terminate_anchored_posix_process_group(  # type: ignore[arg-type]
            RacingLeader(),
            error_type=LocalQwenError,
        )

    assert events == [
        ("waitid", RacingLeader.pid),
        ("signal", signal.SIGTERM),
        ("waitid", RacingLeader.pid),
        ("proof", RacingLeader.pid),
    ]


@pytest.mark.parametrize("proof_rejects", [False, True])
def test_darwin_post_sigterm_probe_eperm_reobserves_before_proof(
    monkeypatch: pytest.MonkeyPatch,
    proof_rejects: bool,
) -> None:
    events: list[tuple[str, int]] = []
    observations = iter((False, False, False, True))
    sleeps: list[float] = []

    class RacingLeader:
        pid = 4331
        returncode = None

    def observe(
        process: object,
        *,
        error_type: type[RuntimeError],
    ) -> bool:
        assert process is leader
        assert error_type is LocalQwenError
        events.append(("observe", RacingLeader.pid))
        return next(observations)

    def signal_group(process_group_id: int, requested_signal: int) -> None:
        assert process_group_id == RacingLeader.pid
        events.append(("signal", requested_signal))
        if requested_signal == 0:
            raise PermissionError(errno.EPERM, "Darwin zombie-only group")
        assert requested_signal == signal.SIGTERM

    def prove_group(
        process_group_id: int,
        *,
        expected_leader_pid: int,
        error_type: type[RuntimeError],
        termination_signal_delivered: bool,
    ) -> None:
        assert process_group_id == expected_leader_pid == RacingLeader.pid
        assert error_type is LocalQwenError
        assert termination_signal_delivered is True
        events.append(("proof", process_group_id))
        if proof_rejects:
            raise error_type("Darwin proof rejected a live group member")

    leader = RacingLeader()
    monkeypatch.setattr(process_tree.sys, "platform", "darwin")
    monkeypatch.setattr(
        process_tree,
        "posix_process_exited_without_reaping",
        observe,
    )
    monkeypatch.setattr(process_tree.os, "killpg", signal_group, raising=False)
    monkeypatch.setattr(
        process_tree,
        "prove_darwin_process_group_all_zombies",
        prove_group,
    )
    monkeypatch.setattr(process_tree.time, "sleep", sleeps.append)

    if proof_rejects:
        with pytest.raises(LocalQwenError, match="proof rejected"):
            process_tree.terminate_anchored_posix_process_group(  # type: ignore[arg-type]
                leader,
                error_type=LocalQwenError,
            )
    else:
        process_tree.terminate_anchored_posix_process_group(  # type: ignore[arg-type]
            leader,
            error_type=LocalQwenError,
        )

    assert events == [
        ("observe", RacingLeader.pid),
        ("signal", signal.SIGTERM),
        ("observe", RacingLeader.pid),
        ("signal", 0),
        ("observe", RacingLeader.pid),
        ("observe", RacingLeader.pid),
        ("proof", RacingLeader.pid),
    ]
    assert sleeps == [0.01]


def test_darwin_post_sigterm_probe_eperm_bounds_live_leader_reobservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int]] = []
    monotonic_values = iter((10.0, 10.1, 10.2, 10.5))
    sleeps: list[float] = []

    class LiveLeader:
        pid = 4332
        returncode = None

    leader = LiveLeader()

    def observe(
        process: object,
        *,
        error_type: type[RuntimeError],
    ) -> bool:
        assert process is leader
        assert error_type is LocalQwenError
        events.append(("observe", LiveLeader.pid))
        return False

    def signal_group(process_group_id: int, requested_signal: int) -> None:
        assert process_group_id == LiveLeader.pid
        events.append(("signal", requested_signal))
        if requested_signal == 0:
            raise PermissionError(errno.EPERM, "Darwin unsignalable live group")
        assert requested_signal == signal.SIGTERM

    def reject_proof(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(
            "EPERM with a persistently live leader must not prove cleanup"
        )

    monkeypatch.setattr(process_tree.sys, "platform", "darwin")
    monkeypatch.setattr(
        process_tree,
        "posix_process_exited_without_reaping",
        observe,
    )
    monkeypatch.setattr(process_tree.os, "killpg", signal_group, raising=False)
    monkeypatch.setattr(
        process_tree.time,
        "monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(process_tree.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        process_tree,
        "prove_darwin_process_group_all_zombies",
        reject_proof,
    )

    with pytest.raises(
        LocalQwenError,
        match="could not verify the owned POSIX process group after SIGTERM",
    ):
        process_tree.terminate_anchored_posix_process_group(  # type: ignore[arg-type]
            leader,
            error_type=LocalQwenError,
        )

    assert events == [
        ("observe", LiveLeader.pid),
        ("signal", signal.SIGTERM),
        ("observe", LiveLeader.pid),
        ("signal", 0),
        ("observe", LiveLeader.pid),
    ]
    assert sleeps == [0.01]


@pytest.mark.parametrize("final_signal_denied", [False, True])
def test_darwin_final_sigkill_requires_proof_and_rejects_a_live_survivor(
    monkeypatch: pytest.MonkeyPatch,
    final_signal_denied: bool,
) -> None:
    events: list[tuple[str, int]] = []

    class ExitedLeader:
        pid = 4314
        returncode = None

    def waitid(_id_type: int, process_id: int, _options: int) -> object:
        events.append(("waitid", process_id))
        return object()

    def signal_group(process_group_id: int, requested_signal: int) -> None:
        assert process_group_id == 4314
        events.append(("signal", requested_signal))
        if requested_signal == 0 or (
            requested_signal == getattr(signal, "SIGKILL", 9)
            and final_signal_denied
        ):
            raise PermissionError(errno.EPERM, "Darwin mixed-identity group")

    def reject_live_member(
        process_group_id: int,
        *,
        expected_leader_pid: int,
        error_type: type[RuntimeError],
        termination_signal_delivered: bool,
    ) -> None:
        assert process_group_id == expected_leader_pid == 4314
        assert termination_signal_delivered is (not final_signal_denied)
        events.append(("proof", process_group_id))
        raise error_type("anchored Darwin process group still contains live PID 4315")

    monkeypatch.setattr(process_tree.sys, "platform", "darwin")
    monkeypatch.setattr(process_tree.os, "P_PID", 1, raising=False)
    monkeypatch.setattr(process_tree.os, "WEXITED", 2, raising=False)
    monkeypatch.setattr(process_tree.os, "WNOHANG", 4, raising=False)
    monkeypatch.setattr(process_tree.os, "WNOWAIT", 8, raising=False)
    monkeypatch.setattr(process_tree.os, "waitid", waitid, raising=False)
    monkeypatch.setattr(process_tree.os, "killpg", signal_group, raising=False)
    monkeypatch.setattr(
        process_tree,
        "prove_darwin_process_group_all_zombies",
        reject_live_member,
    )

    with pytest.raises(LocalQwenError, match="live PID 4315"):
        process_tree.terminate_anchored_posix_process_group(  # type: ignore[arg-type]
            ExitedLeader(),
            error_type=LocalQwenError,
        )

    assert events == [
        ("waitid", 4314),
        ("signal", getattr(signal, "SIGKILL", 9)),
        ("signal", 0),
        ("proof", 4314),
    ]


def test_darwin_denied_final_sigkill_retains_successful_sigterm_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int]] = []
    observations = iter((None, object()))
    liveness_probes = 0

    class ExitingLeader:
        pid = 4319
        returncode = None

    def waitid(_id_type: int, process_id: int, _options: int) -> object | None:
        events.append(("waitid", process_id))
        return next(observations)

    def signal_group(process_group_id: int, requested_signal: int) -> None:
        nonlocal liveness_probes
        assert process_group_id == ExitingLeader.pid
        events.append(("signal", requested_signal))
        if requested_signal == 0:
            liveness_probes += 1
            if liveness_probes == 2:
                raise PermissionError(
                    errno.EPERM,
                    "Darwin zombie-only process group",
                )
            return
        if requested_signal == getattr(signal, "SIGKILL", 9):
            raise PermissionError(
                errno.EPERM,
                "Darwin zombie-only process group",
            )
        assert requested_signal == signal.SIGTERM

    def reject_live_member(
        process_group_id: int,
        *,
        expected_leader_pid: int,
        error_type: type[RuntimeError],
        termination_signal_delivered: bool,
    ) -> None:
        assert process_group_id == expected_leader_pid == ExitingLeader.pid
        assert termination_signal_delivered is True
        events.append(("proof", process_group_id))
        raise error_type("anchored Darwin process group still contains live PID 4320")

    monkeypatch.setattr(process_tree.sys, "platform", "darwin")
    monkeypatch.setattr(process_tree.os, "P_PID", 1, raising=False)
    monkeypatch.setattr(process_tree.os, "WEXITED", 2, raising=False)
    monkeypatch.setattr(process_tree.os, "WNOHANG", 4, raising=False)
    monkeypatch.setattr(process_tree.os, "WNOWAIT", 8, raising=False)
    monkeypatch.setattr(process_tree.os, "waitid", waitid, raising=False)
    monkeypatch.setattr(process_tree.os, "killpg", signal_group, raising=False)
    monkeypatch.setattr(
        process_tree,
        "prove_darwin_process_group_all_zombies",
        reject_live_member,
    )

    with pytest.raises(LocalQwenError, match="live PID 4320"):
        process_tree.terminate_anchored_posix_process_group(  # type: ignore[arg-type]
            ExitingLeader(),
            error_type=LocalQwenError,
        )

    assert events == [
        ("waitid", ExitingLeader.pid),
        ("signal", signal.SIGTERM),
        ("waitid", ExitingLeader.pid),
        ("signal", 0),
        ("signal", getattr(signal, "SIGKILL", 9)),
        ("signal", 0),
        ("proof", ExitingLeader.pid),
    ]


def test_read_and_group_cleanup_failure_preserves_cleanup_failure_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Stdout:
        def close(self) -> None:
            events.append("stdout-close")

    class UnreapedLeader:
        pid = 4315
        args = ("lms", "chat")
        returncode: int | None = None
        stdout = Stdout()

        def poll(self) -> int | None:
            raise AssertionError("POSIX cleanup must not poll or reap the leader")

        def kill(self) -> None:
            events.append("direct-kill")

        def wait(self, *, timeout: float) -> int:
            assert timeout == 1.0
            events.append("direct-wait")
            self.returncode = -getattr(signal, "SIGKILL", 9)
            return self.returncode

    process = UnreapedLeader()

    def fail_read(_stdout: object, *, max_bytes: int) -> bytes:
        assert max_bytes == 64 * 1024
        events.append("read")
        raise OSError("private pipe detail")

    def reject_group_cleanup(
        candidate: object,
        *,
        error_type: type[RuntimeError],
    ) -> None:
        assert candidate is process
        assert error_type is LocalQwenError
        events.append("group-cleanup")
        raise LocalQwenError("group proof rejected")

    monkeypatch.setattr(local_qwen.os, "name", "posix")
    monkeypatch.setattr(
        local_qwen.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )
    monkeypatch.setattr(local_qwen, "_prepare_process_stdout", lambda _stdout: None)
    monkeypatch.setattr(local_qwen, "_read_process_stdout_chunk", fail_read)
    monkeypatch.setattr(
        local_qwen,
        "terminate_anchored_posix_process_group",
        reject_group_cleanup,
    )

    with pytest.raises(
        LocalQwenError,
        match="process tree could not be terminated",
    ) as raised:
        local_qwen._run_bounded_process(
            ["lms", "chat"],
            timeout=5.0,
            creationflags=0,
            max_output_chars=512,
        )

    assert "private pipe detail" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert events == [
        "read",
        "group-cleanup",
        "direct-kill",
        "stdout-close",
        "direct-wait",
    ]


def test_posix_cleanup_failure_uses_direct_child_fallback_without_pgid_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    cleanup_error = LocalQwenError("group proof rejected")
    install_bytesio_stdout_reader(monkeypatch)

    class UnreapedLeader:
        pid = 4316
        args = ("lms", "chat")
        returncode: int | None = None
        stdout = io.BytesIO(b'{"items":[]}')

        def poll(self) -> int | None:
            raise AssertionError("POSIX cleanup must not poll or reap the leader")

        def kill(self) -> None:
            assert self.returncode is None
            events.append("direct-kill")

        def wait(self, *, timeout: float) -> int:
            assert timeout == 1.0
            events.append("direct-wait")
            self.returncode = -9
            return -9

    process = UnreapedLeader()

    def reject_group_cleanup(
        candidate: object,
        *,
        error_type: type[RuntimeError],
    ) -> None:
        assert candidate is process
        assert error_type is LocalQwenError
        events.append("group-cleanup")
        raise cleanup_error

    monkeypatch.setattr(local_qwen.os, "name", "posix")
    monkeypatch.setattr(
        local_qwen.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )
    monkeypatch.setattr(
        local_qwen,
        "_wait_posix_process_without_reaping",
        lambda *_args, **_kwargs: events.append("wnowait"),
    )
    monkeypatch.setattr(
        local_qwen,
        "terminate_anchored_posix_process_group",
        reject_group_cleanup,
    )

    with pytest.raises(
        LocalQwenError,
        match="process tree could not be terminated",
    ) as raised:
        local_qwen._run_bounded_process(
            ["lms", "chat"],
            timeout=5.0,
            creationflags=0,
            max_output_chars=512,
        )

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert events == [
        "wnowait",
        "group-cleanup",
        "direct-kill",
        "direct-wait",
    ]


def test_unobservable_posix_leader_uses_only_direct_child_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    install_bytesio_stdout_reader(monkeypatch)

    class UnobservableLeader:
        pid = 4317
        args = ("lms", "chat")
        returncode: int | None = None
        stdout = io.BytesIO(b'{"items":[]}')

        def poll(self) -> int | None:
            raise AssertionError("POSIX cleanup must not poll or reap the leader")

        def kill(self) -> None:
            events.append("direct-kill")

        def wait(self, *, timeout: float) -> int:
            assert timeout == 1.0
            events.append("direct-wait")
            self.returncode = -9
            return -9

    process = UnobservableLeader()

    def reject_unsafe_group_retry(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("unobservable leader must never be used as a numeric PGID")

    def reject_wait(*_args: object, **_kwargs: object) -> None:
        events.append("wnowait-rejected")
        raise LocalQwenError("leader was already reaped")

    monkeypatch.setattr(local_qwen.os, "name", "posix")
    monkeypatch.setattr(
        local_qwen.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )
    monkeypatch.setattr(
        local_qwen,
        "_wait_posix_process_without_reaping",
        reject_wait,
    )
    monkeypatch.setattr(
        local_qwen,
        "terminate_anchored_posix_process_group",
        reject_unsafe_group_retry,
    )

    with pytest.raises(
        LocalQwenError,
        match="process tree could not be terminated",
    ) as raised:
        local_qwen._run_bounded_process(
            ["lms", "chat"],
            timeout=5.0,
            creationflags=0,
            max_output_chars=512,
        )

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert events == ["wnowait-rejected", "direct-kill", "direct-wait"]


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
