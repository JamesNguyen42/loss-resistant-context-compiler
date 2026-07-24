from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from benchmarks.external_runner import (
    ExternalRunnerError,
    RunnerIdentity,
    RunnerLimits,
    load_external_run_manifest,
    run_external_command,
)
from benchmarks.lrcbench import (
    BenchmarkConfig,
    ExternalBaselineError,
    corpus_document,
    dataset_digest,
    decode_corpus_document,
    generate_histories,
    run_benchmark,
)
from context_compiler.local_qwen import QWEN_Q4_VARIANT


def write_corpus(path: Path) -> tuple[BenchmarkConfig, dict]:
    config = BenchmarkConfig(
        histories=1,
        messages_per_history=24,
        noise_lines_per_message=1,
        token_budget=900,
        bootstrap_samples=100,
    )
    cases = generate_histories(config)
    document = corpus_document(cases, config, dataset_digest(cases, config))
    path.write_text(json.dumps(document), encoding="utf-8")
    return config, document


def valid_adapter_command() -> list[str]:
    program = (
        "import json,sys;"
        "corpus=json.load(open(sys.argv[1],encoding='utf-8'));"
        "payload={'schema':'lrcbench-candidate-output-0.1',"
        "'dataset_sha256':corpus['dataset_sha256'],'system':sys.argv[3],"
        "'cases':[{'case_id':case['case_id'],'rendered_text':'','claims':[]}"
        " for case in corpus['cases']]};"
        "json.dump(payload,open(sys.argv[2],'w',encoding='utf-8'))"
    )
    return [
        sys.executable,
        "-c",
        program,
        "{corpus}",
        "{candidate}",
        "{system}",
    ]


def claim_identity() -> RunnerIdentity:
    return RunnerIdentity(
        adapter_revision="fixture-revision",
        environment_id="fixture-environment",
        model_id=QWEN_Q4_VARIANT,
        model_context_length=8192,
        tokenizer_id="character-estimate-v1",
        inference_concurrency=1,
        retry_count=0,
        model_service_cost_usd=0.0,
    )


def test_claim_identity_requires_exact_qwen_one_slot_and_zero_service_cost() -> None:
    identity = claim_identity()

    assert identity.claim_metadata_complete
    assert not replace(identity, model_id="another/model").claim_metadata_complete
    assert not replace(identity, inference_concurrency=2).claim_metadata_complete
    assert not replace(identity, model_service_cost_usd=0.01).claim_metadata_complete


def test_corpus_export_digest_detects_gold_free_source_tampering(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    _config, document = write_corpus(corpus_path)

    decoded_config, cases, digest = decode_corpus_document(document)

    assert decoded_config.histories == 1
    assert len(cases) == 1
    assert digest == document["dataset_sha256"]

    tampered = json.loads(json.dumps(document))
    tampered["cases"][0]["source_events"][0]["content"] += "tampered"
    with pytest.raises(ExternalBaselineError, match="corpus_sha256 mismatch"):
        decode_corpus_document(tampered)


def test_runner_executes_without_a_shell_and_validates_candidate(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    _config, document = write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"

    manifest = run_external_command(
        valid_adapter_command(),
        system="fixture-adapter",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5),
        identity=claim_identity(),
    )

    assert manifest.process_succeeded
    assert manifest.candidate_valid
    assert manifest.ready_for_scoring
    assert manifest.claim_metadata_complete
    assert manifest.exit_code == 0
    assert manifest.termination_reason is None
    assert manifest.corpus_sha256 == document["corpus_sha256"]
    assert manifest.candidate_sha256 == hashlib.sha256(
        candidate_path.read_bytes()
    ).hexdigest()
    payload = manifest.to_dict()
    manifest_sha = payload.pop("manifest_sha256")
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    assert manifest_sha == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_ready_manifest_reloads_candidate_and_binds_benchmark_evidence(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    config, document = write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    manifest_path = tmp_path / "manifest.json"
    manifest = run_external_command(
        valid_adapter_command(),
        system="fixture-adapter",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5),
        identity=claim_identity(),
    )
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")

    reference = load_external_run_manifest(
        manifest_path,
        expected_dataset_sha256=document["dataset_sha256"],
    )
    report = run_benchmark(
        config,
        external_manifest_paths=(manifest_path,),
        expected_external_systems=("fixture-adapter",),
    )

    assert reference.system == "fixture-adapter"
    assert reference.candidate_path == candidate_path.resolve()
    assert reference.failure_reason is None
    assert report.certificate.external_manifests[0].system == "fixture-adapter"
    assert (
        report.certificate.external_manifests[0].manifest_sha256
        == manifest.to_dict()["manifest_sha256"]
    )
    comparison = next(
        value
        for value in report.certificate.comparisons
        if value.system == "fixture-adapter"
    )
    assert "no validated external run manifest" not in comparison.reasons


def test_ready_candidate_with_unrecorded_identity_is_a_certificate_nonwin(
    tmp_path,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    config, _document = write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    manifest_path = tmp_path / "manifest.json"
    manifest = run_external_command(
        valid_adapter_command(),
        system="fixture-adapter",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5),
    )
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")

    report = run_benchmark(
        config,
        external_manifest_paths=(manifest_path,),
        expected_external_systems=("fixture-adapter",),
    )

    comparison = next(
        value
        for value in report.certificate.comparisons
        if value.system == "fixture-adapter"
    )
    assert manifest.ready_for_scoring
    assert not manifest.claim_metadata_complete
    assert comparison.decision == "invalid"
    assert any(
        "exact-Qwen identity metadata" in reason
        for reason in comparison.reasons
    )


def test_manifest_tampering_is_rejected_before_candidate_scoring(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    _config, document = write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    manifest = run_external_command(
        valid_adapter_command(),
        system="fixture-adapter",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5),
    ).to_dict()
    manifest["system"] = "forged-system"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ExternalRunnerError, match="SHA-256 mismatch"):
        load_external_run_manifest(
            manifest_path,
            expected_dataset_sha256=document["dataset_sha256"],
        )


def test_runner_timeout_is_a_retained_nonwin_and_releases_process(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"

    manifest = run_external_command(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        system="timeout-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=0.05, poll_interval_seconds=0.01),
    )

    assert not manifest.process_succeeded
    assert not manifest.candidate_valid
    assert not manifest.ready_for_scoring
    assert manifest.termination_reason == "timeout"


def test_failed_manifest_reason_becomes_a_registered_invalid_nonwin(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    config, _document = write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    manifest_path = tmp_path / "manifest.json"
    manifest = run_external_command(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        system="timeout-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=0.05, poll_interval_seconds=0.01),
    )
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")

    report = run_benchmark(
        config,
        external_manifest_paths=(manifest_path,),
        expected_external_systems=("timeout-fixture",),
    )

    comparison = next(
        value
        for value in report.certificate.comparisons
        if value.system == "timeout-fixture"
    )
    assert comparison.decision == "invalid"
    assert "timeout" in comparison.reasons
    assert report.certificate.external_manifests[0].failure_reason == "timeout"


def test_runner_enforces_final_stdout_limit_even_for_fast_process(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"

    manifest = run_external_command(
        [sys.executable, "-c", "print('x' * 10000)"],
        system="output-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(max_stdout_bytes=128, timeout_seconds=5),
    )

    assert manifest.stdout_bytes > 128
    assert manifest.termination_reason == "stdout_limit"
    assert not manifest.ready_for_scoring


def test_runner_rejects_invalid_candidate_after_successful_process(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    program = (
        "from pathlib import Path; import sys;"
        "Path(sys.argv[1]).write_text('{}',encoding='utf-8')"
    )

    manifest = run_external_command(
        [sys.executable, "-c", program, "{candidate}"],
        system="invalid-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5),
    )

    assert manifest.process_succeeded
    assert not manifest.candidate_valid
    assert not manifest.ready_for_scoring
    assert manifest.termination_reason is None
    assert "missing fields" in manifest.validation_error


def test_runner_detects_corpus_modification_during_adapter_execution(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    program = (
        "from pathlib import Path; import sys;"
        "path=Path(sys.argv[1]);"
        "path.write_text(path.read_text(encoding='utf-8')+' ',encoding='utf-8')"
    )

    manifest = run_external_command(
        [sys.executable, "-c", program, "{corpus}"],
        system="mutation-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5),
    )

    assert manifest.termination_reason == "corpus_modified"
    assert not manifest.process_succeeded
    assert not manifest.ready_for_scoring


def test_runner_refuses_to_overwrite_candidate_output(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text("owned by caller", encoding="utf-8")

    with pytest.raises(ExternalRunnerError, match="refusing to overwrite"):
        run_external_command(
            valid_adapter_command(),
            system="fixture-adapter",
            corpus_path=corpus_path,
            candidate_path=candidate_path,
        )
