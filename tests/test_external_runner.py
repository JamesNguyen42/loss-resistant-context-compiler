from __future__ import annotations

import hashlib
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from benchmarks.external_runner import (
    ExternalRunnerError,
    RunnerIdentity,
    RunnerLimits,
    load_external_run_manifest,
    run_external_cases,
    run_external_command,
)
from benchmarks.external_runner import main as runner_main
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


def write_corpus(
    path: Path,
    *,
    histories: int = 1,
) -> tuple[BenchmarkConfig, dict]:
    config = BenchmarkConfig(
        histories=histories,
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


def test_per_case_runner_executes_without_a_shell_and_validates_candidate(
    tmp_path,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    _config, document = write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"

    manifest = run_external_cases(
        valid_adapter_command(),
        system="fixture-adapter",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5, max_memory_mb=256),
        identity=claim_identity(),
    )

    assert manifest.process_succeeded
    assert manifest.candidate_valid
    assert manifest.ready_for_scoring
    assert manifest.claim_metadata_complete
    assert manifest.isolation_mode == "per_case"
    assert manifest.case_count == 1
    assert len(manifest.case_runs) == 1
    assert manifest.exit_code == 0
    assert manifest.termination_reason is None
    assert manifest.corpus_sha256 == document["corpus_sha256"]
    assert manifest.candidate_sha256 == hashlib.sha256(candidate_path.read_bytes()).hexdigest()
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


def test_cli_defaults_to_claim_eligible_per_case_mode(tmp_path, capsys) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    manifest_path = tmp_path / "manifest.json"

    exit_code = runner_main(
        [
            "--system",
            "cli-fixture",
            "--corpus",
            str(corpus_path),
            "--candidate-out",
            str(candidate_path),
            "--manifest-out",
            str(manifest_path),
            "--timeout-seconds",
            "5",
            "--max-memory-mb",
            "256",
            "--adapter-revision",
            "fixture-revision",
            "--environment-id",
            "fixture-environment",
            "--model-id",
            QWEN_Q4_VARIANT,
            "--model-context-length",
            "8192",
            "--tokenizer-id",
            "character-estimate-v1",
            "--inference-concurrency",
            "1",
            "--retry-count",
            "0",
            "--model-service-cost-usd",
            "0",
            "--",
            *valid_adapter_command(),
        ]
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert payload["isolation_mode"] == "per_case"
    assert payload["claim_metadata_complete"]
    assert "ready for registered scoring" in capsys.readouterr().out


def test_per_case_runner_uses_one_validated_corpus_case_per_process(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    _config, _document = write_corpus(corpus_path, histories=3)
    candidate_path = tmp_path / "candidate.json"
    program = (
        "import json,sys;"
        "corpus=json.load(open(sys.argv[1],encoding='utf-8'));"
        "assert len(corpus['cases'])==1;"
        "case=corpus['cases'][0];"
        "assert case['case_id']==sys.argv[4];"
        "payload={'schema':'lrcbench-candidate-output-0.1',"
        "'dataset_sha256':corpus['dataset_sha256'],'system':sys.argv[3],"
        "'cases':[{'case_id':case['case_id'],'rendered_text':sys.argv[4],"
        "'claims':[]}]};"
        "json.dump(payload,open(sys.argv[2],'w',encoding='utf-8'))"
    )
    command = [
        sys.executable,
        "-c",
        program,
        "{corpus}",
        "{candidate}",
        "{system}",
        "{case_id}",
    ]

    manifest = run_external_cases(
        command,
        system="isolated-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5, max_memory_mb=256),
        identity=claim_identity(),
    )

    payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    assert manifest.ready_for_scoring
    assert manifest.claim_metadata_complete
    assert manifest.case_count == 3
    assert len(manifest.case_runs) == 3
    assert [record.case_id for record in manifest.case_runs] == [
        value["case_id"] for value in payload["cases"]
    ]
    assert all(record.command[-1] == record.case_id for record in manifest.case_runs)
    assert len({record.corpus_sha256 for record in manifest.case_runs}) == 3


def test_per_case_runner_retains_failure_and_continues_later_cases(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path, histories=2)
    candidate_path = tmp_path / "candidate.json"
    program = (
        "import json,sys;"
        "corpus=json.load(open(sys.argv[1],encoding='utf-8'));"
        "case=corpus['cases'][0];"
        "sys.exit(7) if case['case_id'].endswith('000') else None;"
        "payload={'schema':'lrcbench-candidate-output-0.1',"
        "'dataset_sha256':corpus['dataset_sha256'],'system':sys.argv[3],"
        "'cases':[{'case_id':case['case_id'],'rendered_text':'','claims':[]}]};"
        "json.dump(payload,open(sys.argv[2],'w',encoding='utf-8'))"
    )

    manifest = run_external_cases(
        [
            sys.executable,
            "-c",
            program,
            "{corpus}",
            "{candidate}",
            "{system}",
        ],
        system="failure-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5, max_memory_mb=256),
    )

    assert len(manifest.case_runs) == 2
    assert manifest.case_runs[0].exit_code == 7
    assert not manifest.case_runs[0].process_succeeded
    assert manifest.case_runs[1].candidate_valid
    assert manifest.termination_reason == ("case_failure:history-000:nonzero_exit")
    assert not manifest.ready_for_scoring
    assert not candidate_path.exists()


def test_whole_corpus_mode_is_diagnostic_even_with_complete_identity(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    _config, document = write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    manifest_path = tmp_path / "manifest.json"
    manifest = run_external_command(
        valid_adapter_command(),
        system="whole-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5, max_memory_mb=256),
        identity=claim_identity(),
    )
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")

    reference = load_external_run_manifest(
        manifest_path,
        expected_dataset_sha256=document["dataset_sha256"],
    )

    assert manifest.ready_for_scoring
    assert manifest.isolation_mode == "whole_corpus"
    assert not manifest.claim_metadata_complete
    assert "per-case isolation" in reference.failure_reason


def test_ready_manifest_reloads_candidate_and_binds_benchmark_evidence(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    config, document = write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    manifest_path = tmp_path / "manifest.json"
    manifest = run_external_cases(
        valid_adapter_command(),
        system="fixture-adapter",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5, max_memory_mb=256),
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
        value for value in report.certificate.comparisons if value.system == "fixture-adapter"
    )
    assert "no validated external run manifest" not in comparison.reasons

    corpus_path.write_text(
        corpus_path.read_text(encoding="utf-8") + " ",
        encoding="utf-8",
    )
    with pytest.raises(ExternalRunnerError, match="corpus file SHA-256"):
        load_external_run_manifest(
            manifest_path,
            expected_dataset_sha256=document["dataset_sha256"],
        )


def test_ready_candidate_with_unrecorded_identity_is_a_certificate_nonwin(
    tmp_path,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    config, _document = write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    manifest_path = tmp_path / "manifest.json"
    manifest = run_external_cases(
        valid_adapter_command(),
        system="fixture-adapter",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5, max_memory_mb=256),
    )
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")

    report = run_benchmark(
        config,
        external_manifest_paths=(manifest_path,),
        expected_external_systems=("fixture-adapter",),
    )

    comparison = next(
        value for value in report.certificate.comparisons if value.system == "fixture-adapter"
    )
    assert manifest.ready_for_scoring
    assert not manifest.claim_metadata_complete
    assert comparison.decision == "invalid"
    assert any("exact-Qwen identity" in reason for reason in comparison.reasons)


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


def test_rehashed_inconsistent_case_audit_record_is_rejected(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    _config, document = write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    original_payload = run_external_cases(
        valid_adapter_command(),
        system="fixture-adapter",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5, max_memory_mb=256),
        identity=claim_identity(),
    ).to_dict()
    payload = json.loads(json.dumps(original_payload))
    payload["case_runs"][0]["stdout_bytes"] += 1
    unsigned = dict(payload)
    unsigned.pop("manifest_sha256")
    canonical = json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    payload["manifest_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ExternalRunnerError, match="stdout byte count"):
        load_external_run_manifest(
            manifest_path,
            expected_dataset_sha256=document["dataset_sha256"],
        )

    count_payload = json.loads(json.dumps(original_payload))
    count_payload["case_count"] = 2
    count_unsigned = dict(count_payload)
    count_unsigned.pop("manifest_sha256")
    count_canonical = json.dumps(
        count_unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    count_payload["manifest_sha256"] = hashlib.sha256(count_canonical.encode("utf-8")).hexdigest()
    count_manifest_path = tmp_path / "count-manifest.json"
    count_manifest_path.write_text(json.dumps(count_payload), encoding="utf-8")

    with pytest.raises(ExternalRunnerError, match="case_count.*retained corpus"):
        load_external_run_manifest(
            count_manifest_path,
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


def test_runner_removes_descendants_after_successful_adapter_exit(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    marker_path = tmp_path / "orphan-marker.txt"
    child_program = (
        "from pathlib import Path;import sys,time;"
        "time.sleep(0.4);"
        "Path(sys.argv[1]).write_text('orphan',encoding='utf-8')"
    )
    adapter_program = (
        "import json,subprocess,sys;"
        f"child_program={child_program!r};"
        "subprocess.Popen([sys.executable,'-c',child_program,sys.argv[4]]);"
        "corpus=json.load(open(sys.argv[1],encoding='utf-8'));"
        "payload={'schema':'lrcbench-candidate-output-0.1',"
        "'dataset_sha256':corpus['dataset_sha256'],'system':sys.argv[3],"
        "'cases':[{'case_id':case['case_id'],'rendered_text':'','claims':[]}"
        " for case in corpus['cases']]};"
        "json.dump(payload,open(sys.argv[2],'w',encoding='utf-8'))"
    )

    manifest = run_external_command(
        [
            sys.executable,
            "-c",
            adapter_program,
            "{corpus}",
            "{candidate}",
            "{system}",
            str(marker_path),
        ],
        system="descendant-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5),
    )
    time.sleep(0.6)

    assert manifest.ready_for_scoring
    assert not marker_path.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object regression")
def test_windows_job_memory_limit_allows_bounded_adapter(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"

    manifest = run_external_command(
        valid_adapter_command(),
        system="bounded-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5, max_memory_mb=256),
    )

    assert manifest.memory_limit_enforced
    assert manifest.ready_for_scoring


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object regression")
def test_windows_job_memory_limit_blocks_descendant_allocation(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    program = (
        "import subprocess,sys;"
        "result=subprocess.run([sys.executable,'-c',"
        "'data=bytearray(256*1024*1024)']);"
        "raise SystemExit(result.returncode)"
    )

    manifest = run_external_command(
        [sys.executable, "-c", program],
        system="memory-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5, max_memory_mb=64),
    )

    assert manifest.memory_limit_enforced
    assert manifest.exit_code != 0
    assert manifest.termination_reason == "nonzero_exit"
    assert not manifest.ready_for_scoring
    assert not candidate_path.exists()


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
        value for value in report.certificate.comparisons if value.system == "timeout-fixture"
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
        "from pathlib import Path; import sys;Path(sys.argv[1]).write_text('{}',encoding='utf-8')"
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
