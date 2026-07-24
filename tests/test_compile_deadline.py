from __future__ import annotations

import json
import math
import pickle
import time
from pathlib import Path

import pytest

from context_compiler import (
    CompilationIsolationError,
    CompilationPolicy,
    ContextCompiler,
    SourceRecord,
)
from context_compiler.cli import main
from context_compiler.isolation import _decode_worker_artifact
from tests.deadline_fixtures import (
    BlockingExtractor,
    DescendantExtractor,
    MutatingExtractor,
)


def make_source() -> SourceRecord:
    return SourceRecord.create(
        id="deadline-source",
        sequence=0,
        role="user",
        content="constraint: Preserve the public API",
    )


def test_compile_deadline_requires_a_finite_positive_number() -> None:
    source = make_source()
    for invalid in (True, 0, -1, math.nan, math.inf, "1"):
        with pytest.raises(ValueError, match="finite positive"):
            ContextCompiler().compile(  # type: ignore[arg-type]
                [source],
                timeout_seconds=invalid,
            )


def test_compile_deadline_requires_materialized_sources() -> None:
    source = make_source()

    with pytest.raises(TypeError, match="materialized list or tuple"):
        ContextCompiler().compile(
            (value for value in [source]),
            timeout_seconds=5,
        )


def test_isolated_compile_returns_an_equivalent_sealed_snapshot() -> None:
    source = make_source()
    direct = ContextCompiler().compile([source])
    isolated = ContextCompiler().compile([source], timeout_seconds=5)

    assert isolated.to_prompt() == direct.to_prompt()
    assert isolated.verification.to_dict() == direct.verification.to_dict()
    assert isolated.compression.to_dict() == direct.compression.to_dict()
    direct_metrics = dict(direct.compiler_metadata["metrics"])
    isolated_metrics = dict(isolated.compiler_metadata["metrics"])
    direct_metrics.pop("compile_duration_seconds")
    isolated_metrics.pop("compile_duration_seconds")
    assert isolated_metrics == direct_metrics
    assert [item.to_dict() for item in isolated.items] == [
        item.to_dict() for item in direct.items
    ]
    round_trip = pickle.loads(pickle.dumps(isolated))
    assert round_trip.to_dict() == isolated.to_dict()
    tampered = json.loads(isolated.to_json())
    tampered["source_count"] += 1
    with pytest.raises(CompilationIsolationError, match="digest"):
        _decode_worker_artifact(tampered)


def test_timeout_terminates_blocked_extractor_before_late_side_effect(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "late-marker.txt"
    compiler = ContextCompiler(BlockingExtractor(str(marker)))

    with pytest.raises(TimeoutError, match="process tree was terminated"):
        compiler.compile([make_source()], timeout_seconds=0.5)

    time.sleep(2)
    assert not marker.exists()


def test_timeout_terminates_extractor_descendant_tree(tmp_path: Path) -> None:
    started = tmp_path / "extractor-started.txt"
    orphan_marker = tmp_path / "orphan-marker.txt"
    compiler = ContextCompiler(
        DescendantExtractor(str(started), str(orphan_marker))
    )

    with pytest.raises(TimeoutError, match="process tree was terminated"):
        compiler.compile([make_source()], timeout_seconds=1)

    assert started.is_file()
    time.sleep(3.2)
    assert not orphan_marker.exists()


def test_isolation_prevents_extractor_mutation_of_caller_sources() -> None:
    source = make_source()
    original = source.to_dict()
    compiler = ContextCompiler(
        MutatingExtractor(),
        policy=CompilationPolicy(
            fail_on_primary_extractor_error=True,
        ),
    )

    with pytest.raises(ValueError, match="content hash mismatch"):
        compiler.compile([source], timeout_seconds=5)

    assert source.to_dict() == original
    source.ensure_integrity()


def test_cli_compile_deadline_succeeds_and_reports_timeout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sources_path = tmp_path / "sources.json"
    output_path = tmp_path / "artifact.json"
    sources_path.write_text(
        json.dumps([{"role": "user", "content": "goal: Stay responsive"}]),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "compile",
                str(sources_path),
                "--compile-timeout-seconds",
                "5",
                "--output",
                str(output_path),
            ]
        )
        == 0
    )
    assert json.loads(output_path.read_text(encoding="utf-8"))[
        "schema_version"
    ] == "1.0"
    assert capsys.readouterr().err == ""

    assert (
        main(
            [
                "compile",
                str(sources_path),
                "--compile-timeout-seconds",
                "0.000000001",
                "--error-format",
                "json",
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    diagnostic = json.loads(captured.err)
    assert captured.out == ""
    assert diagnostic["category"] == "timeout"
    assert diagnostic["code"] == "operation_timed_out"
