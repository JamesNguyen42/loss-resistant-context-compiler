from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import context_compiler.cli as cli_module
from context_compiler import (
    CompilationPolicy,
    CompiledMemory,
    ContextCompiler,
    SourceRecord,
)
from context_compiler.cli import _compile_event, main
from context_compiler.extractors import ExtractionResult


class EmptyExtractor:
    name = "empty-event-test-extractor"

    def extract(self, _sources: list[SourceRecord]) -> ExtractionResult:
        return ExtractionResult()


class UnicodeExtractor:
    name = "extractor-caf\u00e9-\U0001f9ea"

    def extract(self, _sources: list[SourceRecord]) -> ExtractionResult:
        return ExtractionResult()


def write_sources(path: Path, content: str) -> None:
    path.write_text(
        json.dumps([{"role": "user", "content": content}]),
        encoding="utf-8",
    )


def test_compile_event_is_opt_in_and_binds_written_artifact(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sources = tmp_path / "sources.json"
    ordinary_output = tmp_path / "ordinary.json"
    event_output = tmp_path / "event.json"
    write_sources(sources, "constraint: Preserve the public API")

    assert main(["compile", str(sources), "-o", str(ordinary_output)]) == 0
    assert capsys.readouterr().err == ""

    assert (
        main(
            [
                "compile",
                str(sources),
                "-o",
                str(event_output),
                "--event-format",
                "jsonl",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    event = json.loads(captured.err)
    artifact = json.loads(event_output.read_text(encoding="utf-8"))

    assert captured.out == ""
    assert event["schema"] == "ctxc-event-0.1"
    assert event["event"] == "compilation_completed"
    assert event["outcome"] == "accepted"
    assert event["exit_code"] == 0
    assert event["artifact_sha256"] == artifact["artifact_sha256"]
    assert event["ledger_complete"] is True
    assert event["verification"]["passed"] is True
    assert event["metrics"]["schema"] == "compilation-metrics-0.1"


def test_compile_event_writes_exact_utf8_to_binary_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = SourceRecord.create(
        id="unicode-event-source",
        sequence=0,
        role="user",
        content="constraint: preserve the event identity",
    )
    result = ContextCompiler(UnicodeExtractor()).compile([source])
    artifact = result.to_dict()
    output = io.BytesIO()

    class BinaryStderr:
        buffer = output

        def write(self, _value: str) -> int:
            raise AssertionError("compile events must bypass the locale text stream")

    monkeypatch.setattr(cli_module.sys, "stderr", BinaryStderr())

    cli_module._emit_compile_event(
        SimpleNamespace(event_format="jsonl", format="json"),
        result,
        artifact,
        exit_code=0,
    )

    raw = output.getvalue()
    assert raw.endswith(b"\n")
    event = json.loads(raw.decode("utf-8", errors="strict"))
    assert event["extraction"]["primary_extractor"] == UnicodeExtractor.name


def test_compile_event_short_write_is_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = tmp_path / "sources.json"
    output = tmp_path / "artifact.json"
    write_sources(sources, "constraint: preserve one event attempt")

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
    monkeypatch.setattr(cli_module.sys, "stderr", stderr)

    assert (
        main(
            [
                "compile",
                str(sources),
                "-o",
                str(output),
                "--event-format",
                "jsonl",
            ]
        )
        == 2
    )
    assert output.is_file()
    assert buffer.writes == 1
    assert buffer.flushes == 0


def test_active_only_event_describes_the_actual_compact_artifact(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sources = tmp_path / "sources.json"
    output = tmp_path / "active-only.json"
    content = (
        "constraint: Preserve the public API\nprogress:\n"
        + "\n".join(
            f"- Investigated optional module {index}" for index in range(30)
        )
    )
    write_sources(sources, content)

    assert (
        main(
            [
                "compile",
                str(sources),
                "-o",
                str(output),
                "--active-only",
                "--token-budget",
                "120",
                "--minimum-compression",
                "1",
                "--event-format",
                "jsonl",
            ]
        )
        == 0
    )
    event = json.loads(capsys.readouterr().err)
    artifact = json.loads(output.read_text(encoding="utf-8"))

    assert artifact["ledger_complete"] is False
    assert event["ledger_complete"] is False
    assert event["artifact_sha256"] == artifact["artifact_sha256"]
    assert event["metrics"]["resolved_items"] > len(artifact["items"])


def test_compile_event_reports_nonzero_compression_outcome(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sources = tmp_path / "sources.json"
    output = tmp_path / "artifact.json"
    write_sources(sources, "goal: Keep the state concise")

    assert (
        main(
            [
                "compile",
                str(sources),
                "-o",
                str(output),
                "--minimum-compression",
                "100",
                "--require-target",
                "--event-format",
                "jsonl",
            ]
        )
        == 4
    )
    event = json.loads(capsys.readouterr().err)

    assert output.is_file()
    assert event["outcome"] == "compression_target_not_met"
    assert event["exit_code"] == 4
    assert event["compression"]["target_met"] is False


def test_prompt_events_bind_full_artifacts_for_success_and_refusal(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = tmp_path / "sources.json"
    output = tmp_path / "prompt.txt"
    write_sources(sources, "constraint: Keep the prompt auditable")

    assert (
        main(
            [
                "compile",
                str(sources),
                "-o",
                str(output),
                "--format",
                "prompt",
                "--event-format",
                "jsonl",
            ]
        )
        == 0
    )
    accepted = json.loads(capsys.readouterr().err)

    assert output.read_text(encoding="utf-8").startswith(
        '<typed_memory schema="1.0"'
    )
    assert accepted["output_format"] == "prompt"
    assert accepted["ledger_complete"] is True
    assert len(accepted["artifact_sha256"]) == 64

    record = SourceRecord.create(
        id="unverified-event-source",
        sequence=0,
        role="user",
        content="constraint: Refuse this unverified prompt",
    )
    unsafe = ContextCompiler(
        policy=CompilationPolicy(verify=False)
    ).compile([record])

    class UnverifiedCompiler:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def compile(
            self,
            _sources: list[SourceRecord],
            *,
            timeout_seconds: float | None = None,
        ) -> CompiledMemory:
            del timeout_seconds
            return unsafe

    monkeypatch.setattr(cli_module, "ContextCompiler", UnverifiedCompiler)
    refused_output = tmp_path / "refused.txt"

    assert (
        main(
            [
                "compile",
                str(sources),
                "-o",
                str(refused_output),
                "--format",
                "prompt",
                "--event-format",
                "jsonl",
                "--error-format",
                "json",
            ]
        )
        == 3
    )
    event_line, diagnostic_line = capsys.readouterr().err.splitlines()
    refused = json.loads(event_line)
    diagnostic = json.loads(diagnostic_line)

    assert not refused_output.exists()
    assert refused["outcome"] == "verification_failed"
    assert refused["exit_code"] == 3
    assert refused["ledger_complete"] is True
    assert refused["verification"]["error_count"] == 1
    assert refused["verification"]["issue_codes"] == [
        "verification_not_performed"
    ]
    assert "issues" not in refused["verification"]
    assert diagnostic["code"] == "unverified_prompt_refused"


def test_compile_event_surfaces_recovery_contributions() -> None:
    source = SourceRecord.create(
        id="event-source",
        sequence=0,
        role="user",
        content="constraint: Keep the fallback deterministic",
    )
    result = ContextCompiler(EmptyExtractor()).compile([source])
    artifact = result.to_dict()

    event = _compile_event(
        result,
        artifact,
        exit_code=0,
        output_format="json",
    )

    assert event["extraction"]["primary_extractor"] == EmptyExtractor.name
    assert event["extraction"]["recovered_items"] == 1
    assert event["metrics"]["recovery_added_items"] == 1
