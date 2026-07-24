from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import benchmarks.external_runner as external_runner_module
import benchmarks.lrcbench as lrcbench_module
import context_compiler.atomic as atomic_module
from benchmarks.lrcbench import BenchmarkConfig


def small_config() -> BenchmarkConfig:
    return BenchmarkConfig(
        histories=1,
        messages_per_history=24,
        noise_lines_per_message=1,
        token_budget=900,
        bootstrap_samples=100,
    )


def test_report_metadata_is_self_contained_and_self_hashed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "a" * 40
    monkeypatch.setattr(
        lrcbench_module,
        "_repository_state",
        lambda: (commit, True),
    )

    report = lrcbench_module.run_benchmark(
        small_config(),
        command=("python", "-m", "benchmarks", "--histories", "1"),
    )
    payload = report.to_dict(include_histories=True)
    claimed_digest = payload.pop("report_sha256")

    assert payload["report_schema"] == lrcbench_module.REPORT_SCHEMA
    assert lrcbench_module._canonical_sha256(payload) == claimed_digest
    assert payload["run_metadata"]["repository_commit"] == commit
    assert payload["run_metadata"]["repository_dirty"] is True
    assert payload["run_metadata"]["package_version"] == "0.1.0"
    assert payload["run_metadata"]["tokenizer_id"] == lrcbench_module.TOKENIZER_ID
    assert payload["run_metadata"]["model_id"] == "deterministic-no-model"
    assert payload["run_metadata"]["model_service_cost_usd"] == 0.0
    assert payload["run_metadata"]["command"] == (
        "python",
        "-m",
        "benchmarks",
        "--histories",
        "1",
    )
    assert {value["name"] for value in payload["run_metadata"]["schema_versions"]} == {
        "candidate",
        "corpus",
        "report",
    }

    altered = replace(
        report,
        run_metadata=replace(report.run_metadata, command=("different",)),
    )
    assert altered.certificate.evidence_sha256 == report.certificate.evidence_sha256
    assert altered.to_dict()["report_sha256"] != report.to_dict()["report_sha256"]
    with pytest.raises(ValueError, match="Git object id"):
        replace(report.run_metadata, repository_commit="not-a-commit")
    with pytest.raises(ValueError, match="finite and non-negative"):
        replace(report.run_metadata, model_service_cost_usd=-0.01)
    with pytest.raises(TypeError, match="command"):
        replace(report.run_metadata, command=())


def test_benchmark_cli_records_reproducible_command(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    arguments = [
        "--histories",
        "1",
        "--messages",
        "24",
        "--noise-lines",
        "1",
        "--bootstrap-samples",
        "100",
        "--json-out",
        str(output),
    ]

    assert lrcbench_module.main(arguments) in {0, 2}

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["run_metadata"]["command"] == [
        sys.executable,
        "-m",
        "benchmarks",
        *arguments,
    ]
    claimed_digest = payload.pop("report_sha256")
    assert lrcbench_module._canonical_sha256(payload) == claimed_digest


def test_failed_corpus_export_preserves_previous_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "corpus.json"
    output.write_text("committed-corpus", encoding="utf-8")

    def fail_replace(_temporary: object, _target: object) -> None:
        raise OSError("injected corpus replace failure")

    monkeypatch.setattr(atomic_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="injected corpus replace failure"):
        lrcbench_module.run_benchmark(
            small_config(),
            corpus_export_path=output,
        )

    assert output.read_text(encoding="utf-8") == "committed-corpus"
    assert list(tmp_path.glob(".ctxc-*.tmp")) == []


def test_failed_benchmark_report_replace_preserves_previous_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "report.json"
    output.write_text("committed-report", encoding="utf-8")

    def fail_replace(_temporary: object, _target: object) -> None:
        raise OSError("injected report replace failure")

    monkeypatch.setattr(atomic_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="injected report replace failure"):
        lrcbench_module.main(
            [
                "--histories",
                "1",
                "--messages",
                "24",
                "--noise-lines",
                "1",
                "--bootstrap-samples",
                "100",
                "--json-out",
                str(output),
            ]
        )

    assert output.read_text(encoding="utf-8") == "committed-report"
    assert list(tmp_path.glob(".ctxc-*.tmp")) == []


def test_manifest_commit_loses_creation_race_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest_path = tmp_path / "manifest.json"
    candidate_path = tmp_path / "candidate.json"
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text("fixture", encoding="utf-8")
    manifest = SimpleNamespace(
        system="fixture",
        ready_for_scoring=False,
        claim_metadata_complete=False,
        to_json=lambda: '{"complete":true}',
    )

    def create_racer_then_finish(*_args: object, **_kwargs: object) -> object:
        manifest_path.write_text("racer-manifest", encoding="utf-8")
        return manifest

    monkeypatch.setattr(
        external_runner_module,
        "run_external_command",
        create_racer_then_finish,
    )

    with pytest.raises(SystemExit) as raised:
        external_runner_module.main(
            [
                "--system",
                "fixture",
                "--corpus",
                str(corpus_path),
                "--candidate-out",
                str(candidate_path),
                "--manifest-out",
                str(manifest_path),
                "--isolation",
                "whole-corpus",
            ]
        )

    assert raised.value.code == 2
    assert "refusing to overwrite" in capsys.readouterr().err
    assert manifest_path.read_text(encoding="utf-8") == "racer-manifest"
    assert list(tmp_path.glob(".ctxc-*.tmp")) == []
