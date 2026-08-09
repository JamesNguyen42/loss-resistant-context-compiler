from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import context_compiler.atomic as atomic_module
from context_compiler import (
    ArtifactLimitError,
    PathBoundaryError,
    SourceLimitError,
    SourceRecord,
    __version__,
    cli,
)
from context_compiler.cli import main

ROOT = Path(__file__).resolve().parents[1]


def test_version_cli_emits_exact_binary_distribution_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BinaryBuffer:
        payloads: list[bytes] = []
        flushes = 0

        def write(self, payload: bytes) -> int:
            self.payloads.append(payload)
            return len(payload)

        def flush(self) -> None:
            self.flushes += 1

    class HostTextOutput:
        buffer = BinaryBuffer()

        def write(self, _value: str) -> int:
            raise AssertionError("version output must not use the locale text stream")

    output = HostTextOutput()
    monkeypatch.setattr(cli.sys, "stdout", output)

    assert main(["version"]) == 0
    assert output.buffer.payloads == [
        f"loss-resistant-context-compiler {__version__}\n".encode("ascii")
    ]
    assert output.buffer.flushes == 1


@pytest.mark.parametrize(
    "mode",
    ["missing", "short", "write", "flush", "missing-flush"],
)
def test_version_cli_output_failure_is_single_attempt(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    class OutputBuffer:
        def __init__(self) -> None:
            self.writes = 0
            self.flushes = 0

        def write(self, payload: bytes) -> int:
            self.writes += 1
            if mode == "write":
                raise OSError("injected version write failure")
            if mode == "short":
                return len(payload) - 1
            return len(payload)

    class FlushableOutputBuffer(OutputBuffer):
        def flush(self) -> None:
            self.flushes += 1
            if mode == "flush":
                raise OSError("injected version flush failure")

    class DiagnosticBuffer:
        payloads: list[bytes] = []
        flushes = 0

        def write(self, payload: bytes) -> int:
            self.payloads.append(payload)
            return len(payload)

        def flush(self) -> None:
            self.flushes += 1

    output = type("Output", (), {})()
    output_buffer = OutputBuffer() if mode == "missing-flush" else FlushableOutputBuffer()
    if mode != "missing":
        output.buffer = output_buffer
    diagnostic = type("Diagnostic", (), {"buffer": DiagnosticBuffer()})()
    monkeypatch.setattr(cli.sys, "stdout", output)
    monkeypatch.setattr(cli.sys, "stderr", diagnostic)

    assert main(["version"]) == 2
    assert output_buffer.writes == (0 if mode == "missing" else 1)
    assert output_buffer.flushes == (1 if mode == "flush" else 0)
    assert diagnostic.buffer.payloads == [
        b"ctxc: package version was not emitted completely; treat any emitted bytes as unusable\n"
    ]
    assert diagnostic.buffer.flushes == 1


def test_version_cli_rejects_extra_arguments() -> None:
    with pytest.raises(SystemExit) as raised:
        main(["version", "unexpected"])
    assert raised.value.code == 2


def write_sources(path: Path, *, content: str = "goal: stay reliable") -> None:
    path.write_text(
        json.dumps([{"role": "user", "content": content}]),
        encoding="utf-8",
    )


def test_atomic_output_replaces_complete_file_and_cleans_temporary_file(
    tmp_path: Path,
) -> None:
    output = tmp_path / "nested" / "artifact.json"
    output.parent.mkdir()
    output.write_text("old\n", encoding="utf-8")
    if os.name != "nt":
        output.chmod(0o640)

    cli._write_output('{"new":true}', str(output))

    assert output.read_text(encoding="utf-8") == '{"new":true}\n'
    assert list(output.parent.glob(".ctxc-*.tmp")) == []
    if os.name != "nt":
        assert stat.S_IMODE(output.stat().st_mode) == 0o640


def test_atomic_output_creates_parent_and_appends_exactly_one_newline(
    tmp_path: Path,
) -> None:
    output = tmp_path / "new" / "result.txt"

    cli._write_output("value\n", str(output))

    assert output.read_bytes() == b"value\n"


def test_atomic_replace_failure_preserves_old_file_and_removes_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "artifact.json"
    output.write_text("trusted-old\n", encoding="utf-8")

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(atomic_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="injected replace failure"):
        cli._write_output("uncommitted-new", str(output))

    assert output.read_text(encoding="utf-8") == "trusted-old\n"
    assert list(tmp_path.glob(".ctxc-*.tmp")) == []


def test_file_fsync_failure_preserves_old_file_and_removes_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "artifact.json"
    output.write_text("trusted-old\n", encoding="utf-8")

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("injected fsync failure")

    monkeypatch.setattr(atomic_module.os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="injected fsync failure"):
        cli._write_output("uncommitted-new", str(output))

    assert output.read_text(encoding="utf-8") == "trusted-old\n"
    assert list(tmp_path.glob(".ctxc-*.tmp")) == []


def test_stdout_output_retains_existing_text_contract(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli._write_output("value", None)
    assert capsys.readouterr().out == "value\n"


def test_report_output_retains_builtin_stringio_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", output)

    cli._write_output("caf\u00e9 \U0001f9ea e\u0301", None)

    assert output.getvalue() == "caf\u00e9 \U0001f9ea e\u0301\n"


def test_new_report_output_retains_builtin_stringio_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", output)

    cli._write_new_output("caf\u00e9 \U0001f9ea e\u0301", None)

    assert output.getvalue() == "caf\u00e9 \U0001f9ea e\u0301\n"


def test_report_output_bypasses_the_host_text_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BinaryBuffer:
        payloads: list[bytes] = []
        flushes = 0

        def write(self, payload: bytes) -> int:
            self.payloads.append(payload)
            return len(payload)

        def flush(self) -> None:
            self.flushes += 1

    class HostTextOutput:
        buffer = BinaryBuffer()

        def write(self, _value: str) -> int:
            raise AssertionError("the locale text writer must not be used")

    output = HostTextOutput()
    monkeypatch.setattr(cli.sys, "stdout", output)

    cli._write_output("caf\u00e9 \U0001f9ea e\u0301", None)

    assert output.buffer.payloads == ["caf\u00e9 \U0001f9ea e\u0301\n".encode("utf-8")]
    assert output.buffer.flushes == 1


def test_report_output_requires_a_binary_buffer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TextOnlyOutput:
        pass

    monkeypatch.setattr(cli.sys, "stdout", TextOnlyOutput())

    with pytest.raises(
        OSError,
        match="standard output does not expose a binary buffer",
    ):
        cli._write_output("report", None)


def test_missing_binary_stdout_returns_a_stable_cli_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TextOnlyOutput:
        writes = 0

        def write(self, _value: str) -> int:
            self.writes += 1
            raise AssertionError("the text writer must not be used")

    class DiagnosticBuffer:
        payloads: list[bytes] = []
        flushes = 0

        def write(self, payload: bytes) -> int:
            self.payloads.append(payload)
            return len(payload)

        def flush(self) -> None:
            self.flushes += 1

    class BinaryDiagnosticOutput:
        buffer = DiagnosticBuffer()

    stdout = TextOnlyOutput()
    stderr = BinaryDiagnosticOutput()
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    monkeypatch.setattr(cli.sys, "stderr", stderr)

    assert main(["schema", "--error-format", "json"]) == 2

    assert stdout.writes == 0
    assert stderr.buffer.flushes == 1
    assert len(stderr.buffer.payloads) == 1
    diagnostic = json.loads(stderr.buffer.payloads[0].decode("utf-8", errors="strict"))
    assert diagnostic["command"] == "schema"
    assert diagnostic["category"] == "io"
    assert diagnostic["code"] == "io_error"
    assert diagnostic["exception_type"] == "OSError"


@pytest.mark.parametrize("write_result", [None, True, 0])
def test_report_output_rejects_an_incomplete_binary_write_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    write_result: object,
) -> None:
    class RefusingBuffer:
        writes = 0
        flushes = 0

        def write(self, _payload: bytes) -> object:
            self.writes += 1
            return write_result

        def flush(self) -> None:
            self.flushes += 1

    class BinaryOutput:
        buffer = RefusingBuffer()

    output = BinaryOutput()
    monkeypatch.setattr(cli.sys, "stdout", output)

    with pytest.raises(
        OSError,
        match="standard output did not accept the complete canonical report",
    ):
        cli._write_output("report", None)

    assert output.buffer.writes == 1
    assert output.buffer.flushes == 0


def test_report_output_failed_flush_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FlushFailureBuffer:
        writes = 0
        flushes = 0

        def write(self, payload: bytes) -> int:
            self.writes += 1
            return len(payload)

        def flush(self) -> None:
            self.flushes += 1
            raise OSError("injected report flush failure")

    class BinaryOutput:
        buffer = FlushFailureBuffer()

    output = BinaryOutput()
    monkeypatch.setattr(cli.sys, "stdout", output)

    with pytest.raises(OSError, match="injected report flush failure"):
        cli._write_output("report", None)

    assert output.buffer.writes == 1
    assert output.buffer.flushes == 1


def test_report_output_encoding_failure_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UntouchedBuffer:
        writes = 0

        def write(self, _payload: bytes) -> int:
            self.writes += 1
            raise AssertionError("invalid text must fail before output")

    class BinaryOutput:
        buffer = UntouchedBuffer()

    output = BinaryOutput()
    monkeypatch.setattr(cli.sys, "stdout", output)

    with pytest.raises(UnicodeEncodeError):
        cli._write_output("invalid surrogate: \ud800", None)

    assert output.buffer.writes == 0


def test_exact_utf8_output_retains_builtin_stringio_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", output)

    cli._write_exact_utf8_output("caf\u00e9 \U0001f9ea e\u0301", None)

    assert output.getvalue() == "caf\u00e9 \U0001f9ea e\u0301\n"


def test_binary_stderr_requires_a_binary_buffer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TextOnlyStderr:
        pass

    monkeypatch.setattr(cli.sys, "stderr", TextOnlyStderr())

    with pytest.raises(
        RuntimeError,
        match="standard error does not expose a binary buffer",
    ):
        cli._write_binary_stderr("diagnostic\n")


def test_binary_stderr_rejects_a_short_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ShortBuffer:
        def write(self, payload: bytes) -> int:
            return len(payload) - 1

        def flush(self) -> None:
            raise AssertionError("short writes must fail before flush")

    class BinaryStderr:
        buffer = ShortBuffer()

    monkeypatch.setattr(cli.sys, "stderr", BinaryStderr())

    with pytest.raises(
        cli._StderrEmissionError,
        match="standard error did not accept the complete diagnostic",
    ):
        cli._write_binary_stderr("diagnostic\n")


def test_broken_error_stderr_is_attempted_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ShortBuffer:
        writes = 0
        flushes = 0

        def write(self, payload: bytes) -> int:
            self.writes += 1
            return len(payload) - 1

        def flush(self) -> None:
            self.flushes += 1

    buffer = ShortBuffer()

    class BinaryStderr:
        pass

    stderr = BinaryStderr()
    stderr.buffer = buffer
    monkeypatch.setattr(cli.sys, "stderr", stderr)

    assert main(["inspect", str(tmp_path / "missing.json")]) == 2
    assert buffer.writes == 1
    assert buffer.flushes == 0


def test_failed_error_stderr_flush_is_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FlushFailureBuffer:
        writes = 0
        flushes = 0

        def write(self, payload: bytes) -> int:
            self.writes += 1
            return len(payload)

        def flush(self) -> None:
            self.flushes += 1
            raise OSError("injected diagnostic flush failure")

    buffer = FlushFailureBuffer()

    class BinaryStderr:
        pass

    stderr = BinaryStderr()
    stderr.buffer = buffer
    monkeypatch.setattr(cli.sys, "stderr", stderr)

    assert main(["inspect", str(tmp_path / "missing.json")]) == 2
    assert buffer.writes == 1
    assert buffer.flushes == 1


@pytest.mark.parametrize("output_format", ["json", "prompt"])
def test_compile_stdout_is_exact_utf8_outside_utf8_mode(
    tmp_path: Path,
    output_format: str,
) -> None:
    sources = tmp_path / "sources.json"
    detail = "preserve caf\u00e9, \U0001f9ea, and e\u0301 exactly"
    content = f"constraint: {detail}"
    sources.write_text(
        json.dumps([{"role": "user", "content": content}], ensure_ascii=False),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["PYTHONUTF8"] = "0"
    environment["PYTHONIOENCODING"] = "cp1252:strict"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"

    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "context_compiler",
            "compile",
            str(sources),
            "--format",
            output_format,
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0
    assert completed.stderr == b""
    rendered = completed.stdout.decode("utf-8", errors="strict")
    assert detail in rendered
    assert "caf\u00c3\u00a9" not in rendered


@pytest.mark.parametrize("error_format", ["json", "text"])
def test_error_diagnostic_is_exact_utf8_outside_utf8_mode(
    tmp_path: Path,
    error_format: str,
) -> None:
    missing = tmp_path / "missing-\U0001f9ea.json"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["PYTHONUTF8"] = "0"
    environment["PYTHONIOENCODING"] = "cp1252:strict"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"

    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "context_compiler",
            "inspect",
            str(missing),
            "--error-format",
            error_format,
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 2
    assert completed.stdout == b""
    assert completed.stderr.endswith(b"\n")
    rendered = completed.stderr.decode("utf-8", errors="strict")
    assert missing.name in rendered
    if error_format == "json":
        diagnostic = json.loads(rendered)
        assert diagnostic["category"] == "io"
        assert diagnostic["code"] == "path_not_found"
        assert diagnostic["exception_type"] == "FileNotFoundError"
    else:
        assert rendered.startswith("ctxc: ")


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (SourceLimitError("limit"), ("resource_limit", "resource_limit_exceeded")),
        (ArtifactLimitError("limit"), ("resource_limit", "resource_limit_exceeded")),
        (TimeoutError("late"), ("timeout", "operation_timed_out")),
        (FileNotFoundError("missing"), ("io", "path_not_found")),
        (PermissionError("denied"), ("io", "permission_denied")),
        (
            PathBoundaryError("changed parent"),
            ("io", "unsafe_path_boundary"),
        ),
        (
            json.JSONDecodeError("bad", "{", 1),
            ("invalid_input", "invalid_json"),
        ),
        (UnicodeError("encoding"), ("invalid_input", "invalid_encoding")),
        (OSError("disk"), ("io", "io_error")),
        (TypeError("wrong"), ("invalid_input", "invalid_type")),
        (
            ValueError("duplicate JSON object key: items"),
            ("invalid_input", "invalid_json"),
        ),
        (ValueError("record hash mismatch"), ("integrity", "integrity_check_failed")),
        (ValueError("exceeds token budget"), ("policy", "policy_rejected")),
        (ValueError("bad value"), ("invalid_input", "invalid_value")),
    ],
)
def test_error_categories_are_stable(
    exception: BaseException,
    expected: tuple[str, str],
) -> None:
    assert cli._error_identity(exception) == expected


def test_json_error_format_emits_one_versioned_resource_diagnostic(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sources = tmp_path / "sources.json"
    write_sources(sources)

    exit_code = main(
        [
            "compile",
            str(sources),
            "--max-source-bytes",
            str(sources.stat().st_size - 1),
            "--error-format",
            "json",
        ]
    )
    captured = capsys.readouterr()
    diagnostic = json.loads(captured.err)

    assert exit_code == 2
    assert captured.out == ""
    assert diagnostic == {
        "schema": "ctxc-diagnostic-0.1",
        "command": "compile",
        "category": "resource_limit",
        "code": "resource_limit_exceeded",
        "exit_code": 2,
        "exception_type": "SourceLimitError",
        "message": (f"source input exceeds {sources.stat().st_size - 1} bytes"),
    }


def test_json_error_format_classifies_missing_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "missing.json"

    assert main(["inspect", str(missing), "--error-format", "json"]) == 2
    diagnostic = json.loads(capsys.readouterr().err)

    assert diagnostic["schema"] == "ctxc-diagnostic-0.1"
    assert diagnostic["command"] == "inspect"
    assert diagnostic["category"] == "io"
    assert diagnostic["code"] == "path_not_found"
    assert diagnostic["exception_type"] == "FileNotFoundError"


def test_default_error_format_remains_human_readable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "missing.json"

    assert main(["inspect", str(missing)]) == 2
    error = capsys.readouterr().err

    assert error.startswith("ctxc: ")
    assert "ctxc-diagnostic" not in error


def test_strict_budget_failure_has_policy_diagnostic(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sources = tmp_path / "sources.json"
    write_sources(
        sources,
        content="constraint: " + "Preserve this exact requirement. " * 20,
    )

    exit_code = main(
        [
            "compile",
            str(sources),
            "--token-budget",
            "1",
            "--strict-budget",
            "--error-format",
            "json",
        ]
    )
    raw_diagnostic = capsys.readouterr().err
    diagnostic = json.loads(raw_diagnostic)

    assert exit_code == 2
    assert raw_diagnostic == (
        '{"category":"policy","code":"policy_rejected","command":"compile",'
        '"exception_type":"ValueError","exit_code":2,"message":"loss-resistant '
        'context exceeds token budget by 298 estimated tokens","schema":'
        '"ctxc-diagnostic-0.1"}\n'
    )
    assert diagnostic == {
        "category": "policy",
        "code": "policy_rejected",
        "command": "compile",
        "exception_type": "ValueError",
        "exit_code": 2,
        "message": "loss-resistant context exceeds token budget by 298 estimated tokens",
        "schema": "ctxc-diagnostic-0.1",
    }
    assert "details" not in diagnostic


def test_compile_uses_atomic_output_path(
    tmp_path: Path,
) -> None:
    sources = tmp_path / "sources.json"
    output = tmp_path / "artifact.json"
    write_sources(sources)
    output.write_text("old partial artifact", encoding="utf-8")

    assert main(["compile", str(sources), "--output", str(output)]) == 0

    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact["schema_version"] == "1.0"
    assert list(tmp_path.glob(".ctxc-*.tmp")) == []


def test_compile_file_output_does_not_require_stdout_binary_buffer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = tmp_path / "sources.json"
    output = tmp_path / "artifact.json"
    write_sources(sources, content="constraint: preserve file output")

    class TextOnlyStdout:
        pass

    monkeypatch.setattr(cli.sys, "stdout", TextOnlyStdout())

    assert main(["compile", str(sources), "--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"] == "1.0"


def test_archive_command_name_is_unambiguous_in_json_diagnostic(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "missing.json"

    exit_code = main(
        [
            "archive",
            "append",
            str(tmp_path / "archive"),
            str(missing),
            "--error-format",
            "json",
        ]
    )
    diagnostic = json.loads(capsys.readouterr().err)

    assert exit_code == 2
    assert diagnostic["command"] == "archive append"


def test_integrity_failure_is_structured_without_exposing_source_content(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "sources.json"
    forged = SourceRecord.create(
        sequence=0,
        role="user",
        content="secret source content",
    ).to_dict()
    forged["content_sha256"] = "0" * 64
    path.write_text(json.dumps([forged]), encoding="utf-8")

    assert main(["compile", str(path), "--error-format", "json"]) == 2
    diagnostic = json.loads(capsys.readouterr().err)

    assert diagnostic["category"] == "integrity"
    assert diagnostic["code"] == "integrity_check_failed"
    assert "secret source content" not in diagnostic["message"]
