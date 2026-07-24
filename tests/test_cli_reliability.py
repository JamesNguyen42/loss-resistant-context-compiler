from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

import context_compiler.atomic as atomic_module
from context_compiler import ArtifactLimitError, SourceLimitError, SourceRecord, cli
from context_compiler.cli import main


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


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (SourceLimitError("limit"), ("resource_limit", "resource_limit_exceeded")),
        (ArtifactLimitError("limit"), ("resource_limit", "resource_limit_exceeded")),
        (TimeoutError("late"), ("timeout", "operation_timed_out")),
        (FileNotFoundError("missing"), ("io", "path_not_found")),
        (PermissionError("denied"), ("io", "permission_denied")),
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
    diagnostic = json.loads(capsys.readouterr().err)

    assert exit_code == 2
    assert diagnostic["category"] == "policy"
    assert diagnostic["code"] == "policy_rejected"


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
