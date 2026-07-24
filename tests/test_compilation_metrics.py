from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from context_compiler import (
    COMPILATION_METRICS_SCHEMA,
    CompilationPolicy,
    ContextCompiler,
    MemoryStatus,
    SourceRecord,
)
from context_compiler.cli import main
from context_compiler.extractors import ExtractionResult
from context_compiler.io import verify_artifact_dict


class EmptyExtractor:
    name = "empty-test-extractor"

    def extract(self, _sources: list[SourceRecord]) -> ExtractionResult:
        return ExtractionResult()


def make_source(sequence: int, content: str) -> SourceRecord:
    return SourceRecord.create(
        id=f"source-{sequence}",
        sequence=sequence,
        role="user",
        content=content,
    )


def resign(artifact: dict[str, object]) -> None:
    unsigned = {
        key: value for key, value in artifact.items() if key != "artifact_sha256"
    }
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    artifact["artifact_sha256"] = hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def test_compiler_emits_versioned_replayable_metrics() -> None:
    sources = [
        make_source(0, "constraint: Runtime must be exactly Python 3.11"),
        make_source(1, "constraint: Runtime must be exactly Python 3.12"),
    ]

    result = ContextCompiler().compile(sources)
    metrics = result.compiler_metadata["metrics"]

    assert metrics["schema"] == COMPILATION_METRICS_SCHEMA
    assert metrics["source_records"] == len(sources)
    assert metrics["resolved_items"] == len(result.items)
    assert metrics["selected_items"] == len(result.selected_item_ids)
    assert metrics["conflicting_items"] == sum(
        item.status == MemoryStatus.CONFLICTING for item in result.items
    )
    assert metrics["detected_conflicts"] == 1
    assert metrics["protected_selected_items"] <= metrics["protected_items"]
    assert metrics["compile_duration_seconds"] >= 0
    for severity in ("error", "warning", "info"):
        assert metrics[f"verification_{severity}_count"] == sum(
            issue.severity.value == severity
            for issue in result.verification.issues
        )

    replay = verify_artifact_dict(result.to_dict(), sources)
    assert replay["passed"] is True
    assert replay["compilation_metrics_present"] is True
    assert replay["compilation_metrics_valid"] is True


def test_metrics_capture_post_resolution_recovery_and_protected_overflow() -> None:
    sources = [
        make_source(
            0,
            "constraint: " + "Preserve this exact requirement. " * 20,
        )
    ]
    result = ContextCompiler(
        EmptyExtractor(),
        policy=CompilationPolicy(
            token_budget=10,
            minimum_compression_ratio=1.0,
        ),
    ).compile(sources)
    metrics = result.compiler_metadata["metrics"]

    assert metrics["recovery_candidate_items"] > metrics["recovery_added_items"]
    assert metrics["recovery_added_items"] == 1
    assert metrics["recovery_added_items"] == result.verification.recovered_items
    assert metrics["recovery_added_items"] == result.compiler_metadata["recovered_items"]
    assert metrics["protected_prompt_tokens"] > 10
    assert metrics["protected_budget_overflow"] == (
        metrics["protected_prompt_tokens"] - 10
    )
    assert verify_artifact_dict(result.to_dict(), sources)["passed"] is True


def test_replay_rejects_rehashed_metrics_mismatch() -> None:
    source = make_source(0, "constraint: Keep the public API stable")
    artifact = ContextCompiler().compile([source]).to_dict()
    artifact["compiler_metadata"]["metrics"]["source_records"] += 1
    resign(artifact)

    report = verify_artifact_dict(artifact, [source])

    assert report["artifact_digest_valid"] is True
    assert report["compilation_metrics_valid"] is False
    assert report["passed"] is False
    assert "compilation_metrics_mismatch" in {
        issue["code"] for issue in report["issues"]
    }


def test_replay_rejects_invalid_compilation_metrics_shape() -> None:
    source = make_source(0, "goal: Keep a durable memory")
    artifact = ContextCompiler().compile([source]).to_dict()
    artifact["compiler_metadata"]["metrics"]["compile_duration_seconds"] = -1
    resign(artifact)

    report = verify_artifact_dict(artifact, [source])

    assert report["artifact_digest_valid"] is True
    assert report["compilation_metrics_valid"] is False
    assert report["passed"] is False
    assert "invalid_compilation_metrics" in {
        issue["code"] for issue in report["issues"]
    }


def test_optional_metrics_and_legacy_recovery_metadata_remain_compatible() -> None:
    source = make_source(0, "decision: Keep backward compatibility")
    artifact = ContextCompiler().compile([source]).to_dict()
    artifact["compiler_metadata"].pop("metrics")
    resign(artifact)

    report = verify_artifact_dict(artifact, [source])

    assert report["passed"] is True
    assert report["compilation_metrics_present"] is False
    assert report["compilation_metrics_valid"] is True

    artifact = ContextCompiler().compile([source]).to_dict()
    artifact["compiler_metadata"].pop("recovered_items")
    resign(artifact)
    report = verify_artifact_dict(artifact, [source])

    assert report["passed"] is True
    assert report["compilation_metrics_present"] is True
    assert report["compilation_metrics_valid"] is True

    source = make_source(
        0,
        "constraint: Preserve the public API\nprogress:\n"
        + "\n".join(
            f"- Investigated optional module {index}" for index in range(30)
        ),
    )
    result = ContextCompiler(
        policy=CompilationPolicy(
            token_budget=120,
            minimum_compression_ratio=1.0,
        )
    ).compile([source])
    assert len(result.items) > len(result.selected_item_ids)
    report = verify_artifact_dict(
        result.to_dict(include_all_items=False),
        [source],
    )

    assert report["passed"] is False
    assert report["compilation_metrics_valid"] is True
    assert "incomplete_ledger" in {
        issue["code"] for issue in report["issues"]
    }
    assert "compilation_metrics_mismatch" not in {
        issue["code"] for issue in report["issues"]
    }


def test_inspect_surfaces_compilation_metrics(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = make_source(0, "goal: Make telemetry observable")
    artifact = ContextCompiler().compile([source]).to_dict()
    artifact_path = tmp_path / "artifact.json"
    artifact_path.write_text(
        json.dumps(artifact, ensure_ascii=False),
        encoding="utf-8",
    )

    assert main(["inspect", str(artifact_path)]) == 0
    summary = json.loads(capsys.readouterr().out)

    assert summary["metrics"] == artifact["compiler_metadata"]["metrics"]
