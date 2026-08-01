from __future__ import annotations

import json
import re
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import benchmarks.external_runner as external_runner_module
import benchmarks.lrcbench as lrcbench_module
import benchmarks.report_verifier as report_verifier_module
import context_compiler.atomic as atomic_module
from benchmarks.lrcbench import BenchmarkConfig
from benchmarks.report_verifier import (
    BenchmarkReportError,
    BenchmarkReportLimits,
    load_benchmark_report,
    verify_benchmark_report,
)
from tests.protocol_fixtures import write_frozen_external_protocol


def small_config() -> BenchmarkConfig:
    return BenchmarkConfig(
        histories=1,
        messages_per_history=24,
        noise_lines_per_message=1,
        token_budget=900,
        bootstrap_samples=100,
    )


def report_payload(*, include_histories: bool = False) -> dict[str, object]:
    return lrcbench_module.run_benchmark(
        small_config(),
        command=("python", "-m", "benchmarks"),
    ).to_dict(include_histories=include_histories)


def rehash_report(payload: dict[str, object], *, evidence: bool = False) -> None:
    if evidence:
        certificate = payload["certificate"]
        systems = payload["systems"]
        assert isinstance(certificate, dict)
        assert isinstance(systems, list)
        summaries = {}
        for raw_system in systems:
            assert isinstance(raw_system, dict)
            summary = dict(raw_system)
            summary.pop("per_history", None)
            summaries[summary["system"]] = summary
        certificate["evidence_sha256"] = (
            report_verifier_module._certificate_evidence_sha256(
                report_verifier_module._evidence_document(
                    report=payload,
                    system_summaries=summaries,
                    certificate=certificate,
                )
            )
        )
    unsigned = dict(payload)
    unsigned.pop("report_sha256", None)
    payload["report_sha256"] = lrcbench_module._canonical_sha256(unsigned)


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
    assert payload["run_metadata"]["package_version"] == "0.1.1a13"
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
        "candidate_producer",
        "corpus",
        "corpus_producer",
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


def test_saved_report_verifier_rechecks_hashes_dataset_metadata_and_cli(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = report_payload(include_histories=True)
    output = tmp_path / "report.json"
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    verified = load_benchmark_report(output)

    assert verified.report_sha256 == payload["report_sha256"]
    assert verified.evidence_sha256 == payload["certificate"]["evidence_sha256"]
    assert verified.dataset_sha256 == payload["dataset_sha256"]
    assert verified.systems == ("compiler", "head", "tail", "extractive")
    assert verified.histories == 1
    assert verified.per_history_included is True
    assert lrcbench_module.main(["--verify-report", str(output)]) == 0
    cli_result = json.loads(capsys.readouterr().out)
    assert cli_result == json.loads(
        json.dumps({"verified": True, **verified.to_dict()})
    )


def test_report_verifier_rejects_layered_tampering() -> None:
    report_hash_tamper = report_payload()
    command = report_hash_tamper["run_metadata"]["command"]
    report_hash_tamper["run_metadata"]["command"] = (*command, "--changed")
    with pytest.raises(BenchmarkReportError, match="report_sha256 mismatch"):
        verify_benchmark_report(report_hash_tamper)

    evidence_tamper = report_payload()
    evidence_tamper["systems"][0]["quality_score"] = 0.5
    evidence_tamper["certificate"]["candidate_quality"] = 0.5
    rehash_report(evidence_tamper)
    with pytest.raises(BenchmarkReportError, match="evidence_sha256 mismatch"):
        verify_benchmark_report(evidence_tamper)

    dataset_tamper = report_payload()
    dataset_tamper["dataset_sha256"] = "0" * 64
    rehash_report(dataset_tamper, evidence=True)
    with pytest.raises(BenchmarkReportError, match="dataset_sha256 mismatch"):
        verify_benchmark_report(dataset_tamper)

    token_tamper = report_payload()
    token_tamper["systems"][0]["active_tokens"] = 0
    rehash_report(token_tamper, evidence=True)
    with pytest.raises(BenchmarkReportError, match="at least one token per history"):
        verify_benchmark_report(token_tamper)


def test_report_verifier_reconciles_raw_history_metrics() -> None:
    payload = report_payload(include_histories=True)
    compiler_history = payload["systems"][0]["per_history"][0]
    assert compiler_history["critical_recalled"] > 0
    compiler_history["critical_recalled"] -= 1
    rehash_report(payload)

    with pytest.raises(BenchmarkReportError, match="critical_atom_recall is inconsistent"):
        verify_benchmark_report(payload)


def test_report_verifier_accepts_retained_missing_external_nonwin(
    tmp_path: Path,
) -> None:
    config = small_config()
    digest = lrcbench_module.dataset_digest(
        lrcbench_module.generate_histories(config),
        config,
    )
    systems = (
        "missing-adapter",
        "missing-adapter-b",
        "missing-adapter-c",
        "missing-adapter-d",
    )
    protocol_path = write_frozen_external_protocol(
        tmp_path,
        systems,
        synthetic_dataset_sha256=digest,
    )
    payload = lrcbench_module.run_benchmark(
        config,
        expected_external_systems=systems,
        external_protocol_path=protocol_path,
        command=("python", "-m", "benchmarks"),
    ).to_dict()

    verified = verify_benchmark_report(payload)

    assert verified.certificate_scope == "external-inclusive"
    assert verified.systems == ("compiler", "head", "tail", "extractive")
    assert verified.model_id == "unrecorded"
    assert verified.model_service_cost_usd is None


def test_report_verifier_rejects_external_protocol_tampering(
    tmp_path: Path,
) -> None:
    config = small_config()
    digest = lrcbench_module.dataset_digest(
        lrcbench_module.generate_histories(config),
        config,
    )
    systems = ("alpha", "beta", "delta", "gamma")
    protocol_path = write_frozen_external_protocol(
        tmp_path,
        systems,
        synthetic_dataset_sha256=digest,
    )
    payload = lrcbench_module.run_benchmark(
        config,
        external_protocol_path=protocol_path,
    ).to_dict()
    payload["certificate"]["external_protocol"]["protocol_sha256"] = "0" * 64
    rehash_report(payload)

    with pytest.raises(BenchmarkReportError, match="evidence_sha256 mismatch"):
        verify_benchmark_report(payload)

    mismatch = lrcbench_module.run_benchmark(
        config,
        external_protocol_path=protocol_path,
    ).to_dict()
    mismatch["certificate"]["external_protocol"]["registered_systems"] = [
        "alpha",
        "beta",
        "gamma",
        "other",
    ]
    rehash_report(mismatch, evidence=True)
    with pytest.raises(BenchmarkReportError, match="does not match"):
        verify_benchmark_report(mismatch)


def test_report_verifier_replays_committed_local_v01_report() -> None:
    report_path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "results"
        / "lrcbench-local.json"
    )
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    verified = load_benchmark_report(report_path)

    assert payload["report_schema"] == lrcbench_module.LEGACY_REPORT_SCHEMA
    assert verified.report_sha256 == payload["report_sha256"]
    assert verified.certificate_scope == "local-bundled-only"


def test_report_loader_rejects_duplicate_nonfinite_and_oversized_json(
    tmp_path: Path,
) -> None:
    payload = report_payload()
    encoded = json.dumps(payload, indent=2, sort_keys=True)
    duplicate = encoded.replace(
        "{",
        '{"report_schema":"lrcbench-report-0.1",',
        1,
    )
    duplicate_path = tmp_path / "duplicate.json"
    duplicate_path.write_text(duplicate, encoding="utf-8")
    with pytest.raises(BenchmarkReportError, match="duplicate JSON object key"):
        load_benchmark_report(duplicate_path)

    nonfinite = re.sub(
        r'("duration_seconds": )[^,\n]+',
        r"\1NaN",
        encoded,
        count=1,
    )
    nonfinite_path = tmp_path / "nonfinite.json"
    nonfinite_path.write_text(nonfinite, encoding="utf-8")
    with pytest.raises(BenchmarkReportError, match="non-standard JSON constant"):
        load_benchmark_report(nonfinite_path)

    valid_path = tmp_path / "valid.json"
    valid_path.write_text(encoded, encoding="utf-8")
    with pytest.raises(BenchmarkReportError, match="regular file"):
        load_benchmark_report(tmp_path)
    with pytest.raises(BenchmarkReportError, match="report exceeds"):
        load_benchmark_report(
            valid_path,
            limits=BenchmarkReportLimits(max_input_bytes=len(encoded.encode("utf-8")) - 1),
        )


def test_verify_report_cli_rejects_generation_options(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "report.json"
    output.write_text(json.dumps(report_payload()), encoding="utf-8")

    with pytest.raises(SystemExit) as raised:
        lrcbench_module.main(
            ["--verify-report", str(output), "--histories", "1"]
        )

    assert raised.value.code == 2
    assert "cannot be combined" in capsys.readouterr().err


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
