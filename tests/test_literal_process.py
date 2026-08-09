from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import benchmarks.literal_process as literal_process_module
from benchmarks.literal_process import (
    LiteralProcessError,
    LiteralProcessLimits,
    run_literal_argv,
)

_PYTHON = str(Path(sys.executable).resolve())
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _run_python(
    cwd: Path,
    program: str,
    *arguments: str,
    environment: dict[str, str] | None = None,
    limits: LiteralProcessLimits | None = None,
):
    return run_literal_argv(
        [_PYTHON, "-c", program, *arguments],
        cwd=cwd.resolve(),
        environment={} if environment is None else environment,
        limits=limits or LiteralProcessLimits(timeout_seconds=5),
    )


def test_literal_process_limits_defaults_and_validation() -> None:
    assert LiteralProcessLimits() == LiteralProcessLimits(
        timeout_seconds=300.0,
        poll_interval_seconds=0.02,
        max_stdout_bytes=1_000_000,
        max_stderr_bytes=1_000_000,
        max_memory_mb=None,
    )
    zero_caps = LiteralProcessLimits(max_stdout_bytes=0, max_stderr_bytes=0)
    assert zero_caps.max_stdout_bytes == zero_caps.max_stderr_bytes == 0

    invalid_values = (
        ("timeout_seconds", True),
        ("timeout_seconds", 0),
        ("timeout_seconds", float("nan")),
        ("timeout_seconds", float("inf")),
        ("timeout_seconds", 1e308),
        ("timeout_seconds", 10**10_000),
        ("poll_interval_seconds", "0.1"),
        ("poll_interval_seconds", -1),
        ("poll_interval_seconds", 1e308),
        ("max_stdout_bytes", True),
        ("max_stdout_bytes", -1),
        ("max_stderr_bytes", 1.5),
        ("max_memory_mb", 0),
        ("max_memory_mb", 1.5),
    )
    for name, value in invalid_values:
        with pytest.raises(LiteralProcessError):
            LiteralProcessLimits(**{name: value})


def test_preflight_validation_never_reaches_popen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regular_file = tmp_path / "not-a-directory"
    regular_file.write_text("fixture", encoding="utf-8")
    valid_argv = [_PYTHON, "-c", "pass"]
    valid_cwd = tmp_path.resolve()
    valid_environment: object = {}
    valid_limits: object = LiteralProcessLimits(timeout_seconds=5)
    requests: list[tuple[object, object, object, object]] = [
        (_PYTHON, valid_cwd, valid_environment, valid_limits),
        (_PYTHON.encode(), valid_cwd, valid_environment, valid_limits),
        ([], valid_cwd, valid_environment, valid_limits),
        ([_PYTHON, ""], valid_cwd, valid_environment, valid_limits),
        ([_PYTHON, 7], valid_cwd, valid_environment, valid_limits),
        ([_PYTHON, "bad\0argument"], valid_cwd, valid_environment, valid_limits),
        ([Path(_PYTHON).name], valid_cwd, valid_environment, valid_limits),
        ([str((tmp_path / "missing-python").resolve())], valid_cwd, {}, valid_limits),
        (valid_argv, Path("relative"), valid_environment, valid_limits),
        (valid_argv, (tmp_path / "missing").resolve(), valid_environment, valid_limits),
        (valid_argv, regular_file.resolve(), valid_environment, valid_limits),
        (valid_argv, 7, valid_environment, valid_limits),
        (valid_argv, valid_cwd, [], valid_limits),
        (valid_argv, valid_cwd, {"": "value"}, valid_limits),
        (valid_argv, valid_cwd, {"BAD-NAME": "value"}, valid_limits),
        (valid_argv, valid_cwd, {1: "value"}, valid_limits),
        (valid_argv, valid_cwd, {"NAME": 1}, valid_limits),
        (valid_argv, valid_cwd, {"NAME": "nul\0value"}, valid_limits),
        (valid_argv, valid_cwd, valid_environment, object()),
    ]
    if os.name == "nt":
        requests.append(
            (valid_argv, valid_cwd, {"NAME": "one", "name": "two"}, valid_limits)
        )

    def reject_popen(*_args: object, **_kwargs: object) -> None:
        pytest.fail("invalid input reached Popen")

    monkeypatch.setattr(literal_process_module.subprocess, "Popen", reject_popen)
    for argv, cwd, environment, limits in requests:
        with pytest.raises(LiteralProcessError):
            run_literal_argv(
                argv,  # type: ignore[arg-type]
                cwd=cwd,  # type: ignore[arg-type]
                environment=environment,  # type: ignore[arg-type]
                limits=limits,  # type: ignore[arg-type]
            )


def test_run_literal_argv_preserves_literal_arguments_and_exact_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LITERAL_PROCESS_PARENT_ONLY", "must-not-leak")
    environment = {
        "ALPHA": "first",
        "EMPTY": "",
        "UNICODE_VALUE": "snowman ☃\n=literal",
    }
    literal_arguments = (
        "semi;colon",
        "amp&&ersand",
        "pipe|value",
        "redirect>value",
        "glob*value",
        "$HOME",
        "%PATH%",
        "$(echo-not-run)",
        "white space",
        '"double"',
        "'single'",
    )
    program = (
        "import json,os,sys;"
        "payload={'argv':sys.argv[1:],'cwd':os.getcwd(),"
        "'values':{name:os.environ.get(name) for name in "
        "('ALPHA','EMPTY','UNICODE_VALUE')},"
        "'parent_only_present':'LITERAL_PROCESS_PARENT_ONLY' in os.environ};"
        "sys.stdout.buffer.write(json.dumps(payload,sort_keys=True,"
        "separators=(',',':')).encode('utf-8'));"
        "sys.stderr.buffer.write(b'fixture-stderr')"
    )
    expected_payload = {
        "argv": list(literal_arguments),
        "cwd": str(tmp_path.resolve()),
        "parent_only_present": False,
        "values": environment,
    }
    expected_stdout = json.dumps(
        expected_payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    limits = LiteralProcessLimits(timeout_seconds=5)
    real_popen = literal_process_module.subprocess.Popen
    launch: dict[str, object] = {}

    def observed_popen(arguments: list[str], **options: object):
        launch["arguments"] = arguments
        launch["options"] = options
        return real_popen(arguments, **options)

    monkeypatch.setattr(literal_process_module.subprocess, "Popen", observed_popen)
    result = _run_python(
        tmp_path,
        program,
        *literal_arguments,
        environment=environment,
        limits=limits,
    )

    expected_argv = (_PYTHON, "-c", program, *literal_arguments)
    assert result.argv == expected_argv
    assert result.cwd == str(tmp_path.resolve())
    assert result.limits == limits
    assert result.exit_code == 0
    assert result.termination_trigger is None
    assert result.execution_error is None
    assert result.cleanup_error is None
    assert result.cleanup_detail is None
    assert result.process_succeeded
    assert not result.swebench_containment_claim_ready
    assert result.stdout.observed_bytes == len(expected_stdout)
    assert result.stdout.captured_bytes == len(expected_stdout)
    assert result.stdout.prefix == expected_stdout
    assert result.stdout.captured_sha256 == hashlib.sha256(expected_stdout).hexdigest()
    assert result.stdout.complete
    assert result.stderr.observed_bytes == len(b"fixture-stderr")
    assert result.stderr.prefix == b"fixture-stderr"
    assert result.stderr.captured_sha256 == hashlib.sha256(b"fixture-stderr").hexdigest()
    assert result.stderr.complete
    assert result.environment_names == tuple(sorted(environment))
    canonical_environment = json.dumps(
        environment,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    assert result.environment_sha256 == hashlib.sha256(canonical_environment).hexdigest()
    assert result.memory_limit_scope == "none"
    assert result.containment_scope == (
        "windows-job-process-and-descendants"
        if os.name == "nt"
        else "posix-session-process-group-escapable"
    )
    assert min(
        result.duration_seconds,
        result.setup_duration_seconds,
        result.process_duration_seconds,
        result.cleanup_duration_seconds,
    ) >= 0

    assert launch["arguments"] == list(expected_argv)
    options = launch["options"]
    assert isinstance(options, dict)
    assert options["cwd"] == tmp_path.resolve()
    assert options["env"] == environment
    assert options["stdin"] is subprocess.DEVNULL
    assert options["stdout"] is subprocess.PIPE
    assert options["stderr"] is subprocess.PIPE
    assert options["shell"] is False
    assert options["close_fds"] is True
    assert options["start_new_session"] is (os.name != "nt")


def test_stream_evidence_uses_cap_plus_one_prefix_and_enforces_limits(
    tmp_path: Path,
) -> None:
    cases = (
        (1, "stdout", 4, b"abcd"),
        (1, "stdout", 4, b"abcde"),
        (1, "stdout", 0, b"x"),
        (2, "stderr", 4, b"abcd"),
        (2, "stderr", 4, b"abcde"),
        (2, "stderr", 0, b"x"),
    )
    for descriptor, stream_name, cap, payload in cases:
        limits = LiteralProcessLimits(
            timeout_seconds=5,
            max_stdout_bytes=cap if stream_name == "stdout" else 64,
            max_stderr_bytes=cap if stream_name == "stderr" else 64,
        )
        result = _run_python(
            tmp_path,
            "import os,sys;os.write(int(sys.argv[1]),bytes.fromhex(sys.argv[2]))",
            str(descriptor),
            payload.hex(),
            limits=limits,
        )
        evidence = getattr(result, stream_name)
        prefix = payload[: cap + 1]

        assert evidence.observed_bytes == len(payload)
        assert evidence.captured_bytes == len(prefix)
        assert evidence.prefix == prefix
        assert evidence.captured_sha256 == hashlib.sha256(prefix).hexdigest()
        assert evidence.complete
        other = result.stderr if stream_name == "stdout" else result.stdout
        assert other.observed_bytes == 0
        assert other.captured_bytes == 0
        assert other.captured_sha256 == _EMPTY_SHA256
        assert other.prefix == b""
        assert other.complete
        if len(payload) <= cap:
            assert result.termination_trigger is None
            assert result.process_succeeded
        else:
            assert result.termination_trigger == f"{stream_name}_limit"
            assert not result.process_succeeded


def test_large_fast_output_has_bounded_retained_evidence(tmp_path: Path) -> None:
    result = _run_python(
        tmp_path,
        "import os;os.write(1,b'x'*(1024*1024))",
        limits=LiteralProcessLimits(timeout_seconds=5, max_stdout_bytes=16),
    )

    assert result.termination_trigger == "stdout_limit"
    assert result.stdout.observed_bytes >= 17
    assert result.stdout.captured_bytes == 17
    assert result.stdout.prefix == b"x" * 17
    assert result.stdout.captured_sha256 == hashlib.sha256(b"x" * 17).hexdigest()
    assert result.stdout.complete
    assert not result.process_succeeded


def test_timeout_terminates_the_process_and_completes_pipe_capture(tmp_path: Path) -> None:
    started = time.monotonic()
    result = _run_python(
        tmp_path,
        "import time;time.sleep(30)",
        limits=LiteralProcessLimits(
            timeout_seconds=0.05,
            poll_interval_seconds=0.005,
        ),
    )

    assert time.monotonic() - started < 5
    assert result.termination_trigger == "timeout"
    assert result.stdout.complete
    assert result.stderr.complete
    assert not result.process_succeeded
    assert not result.swebench_containment_claim_ready


def test_deadline_expiry_before_launch_never_reaches_popen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock_calls = 0

    def monotonic() -> float:
        nonlocal clock_calls
        clock_calls += 1
        return 0.0 if clock_calls == 1 else 5.0

    def reject_popen(*_args: object, **_kwargs: object) -> None:
        pytest.fail("an expired lifecycle reached Popen")

    monkeypatch.setattr(literal_process_module.time, "monotonic", monotonic)
    monkeypatch.setattr(literal_process_module.subprocess, "Popen", reject_popen)
    result = run_literal_argv(
        [_PYTHON, "-c", "pass"],
        cwd=tmp_path.resolve(),
        environment={},
        limits=LiteralProcessLimits(timeout_seconds=5),
    )

    assert result.exit_code is None
    assert result.termination_trigger == "timeout"
    assert result.process_duration_seconds == 0
    assert not result.process_succeeded


def test_slow_popen_times_out_before_completion_observation_or_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_popen = literal_process_module.subprocess.Popen
    real_observer = literal_process_module.posix_process_exited_without_reaping
    completion_probes = 0
    resume_calls = 0

    def slow_popen(*args: object, **kwargs: object):
        process = real_popen(*args, **kwargs)
        time.sleep(0.05)
        return process

    def observe(*args: object, **kwargs: object) -> bool:
        nonlocal completion_probes
        completion_probes += 1
        return real_observer(*args, **kwargs)

    def resume(*_args: object, **_kwargs: object) -> None:
        nonlocal resume_calls
        resume_calls += 1

    monkeypatch.setattr(literal_process_module.subprocess, "Popen", slow_popen)
    monkeypatch.setattr(
        literal_process_module,
        "posix_process_exited_without_reaping",
        observe,
    )
    if os.name == "nt":
        monkeypatch.setattr(literal_process_module, "resume_windows_process", resume)

    result = _run_python(
        tmp_path,
        "pass",
        limits=LiteralProcessLimits(timeout_seconds=0.01),
    )

    assert result.termination_trigger == "timeout"
    assert completion_probes == 0
    assert resume_calls == 0
    assert not result.process_succeeded


def test_popen_failure_is_sanitized_and_deadline_started_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_monotonic = literal_process_module.time.monotonic
    events: list[str] = []

    def monotonic() -> float:
        events.append("clock")
        return real_monotonic()

    def reject_popen(*_args: object, **_kwargs: object) -> None:
        events.append("popen")
        raise OSError("sensitive injected launch detail")

    monkeypatch.setattr(literal_process_module.time, "monotonic", monotonic)
    monkeypatch.setattr(literal_process_module.subprocess, "Popen", reject_popen)
    with pytest.raises(
        LiteralProcessError,
        match="could not start literal process: OSError",
    ) as raised:
        run_literal_argv(
            [_PYTHON, "-c", "pass"],
            cwd=tmp_path.resolve(),
            environment={},
            limits=LiteralProcessLimits(timeout_seconds=5),
        )

    assert "sensitive injected" not in str(raised.value)
    assert events.index("clock") < events.index("popen")


def test_unexpected_post_launch_exception_still_cleans_process_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "leaked-process-marker"

    def fail_observation(_capture: object) -> str | None:
        raise RuntimeError("injected unexpected observer failure")

    monkeypatch.setattr(
        literal_process_module._PipeCapture,
        "error",
        property(fail_observation),
    )

    with pytest.raises(RuntimeError, match="unexpected observer failure"):
        _run_python(
            tmp_path,
            "import pathlib,sys,time;time.sleep(0.3);"
            "pathlib.Path(sys.argv[1]).write_text('leaked',encoding='utf-8')",
            str(marker),
            limits=LiteralProcessLimits(timeout_seconds=5),
        )

    time.sleep(0.5)
    assert not marker.exists()


def test_expected_post_launch_failure_preserves_original_error_after_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    starts = 0
    real_start = literal_process_module._PipeCapture.start

    def fail_second_start(capture: object) -> None:
        nonlocal starts
        starts += 1
        if starts == 2:
            raise RuntimeError("injected second capture startup failure")
        real_start(capture)  # type: ignore[arg-type]

    monkeypatch.setattr(
        literal_process_module._PipeCapture,
        "start",
        fail_second_start,
    )

    with pytest.raises(
        LiteralProcessError,
        match="could not start literal process pipe capture",
    ) as raised:
        _run_python(
            tmp_path,
            "import time;time.sleep(30)",
            limits=LiteralProcessLimits(timeout_seconds=5),
        )

    assert "emergency cleanup was incomplete" not in str(raised.value)


def test_configured_memory_limit_reports_its_actual_scope(tmp_path: Path) -> None:
    limits = LiteralProcessLimits(timeout_seconds=5, max_memory_mb=1024)
    if os.name != "nt":
        with pytest.raises(LiteralProcessError, match="external controller"):
            _run_python(tmp_path, "pass", limits=limits)
        return

    result = _run_python(tmp_path, "pass", limits=limits)
    assert result.memory_limit_scope == "windows-job-process-and-aggregate"
    assert not result.swebench_containment_claim_ready
