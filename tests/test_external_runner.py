from __future__ import annotations

import hashlib
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

import benchmarks.external_runner as external_runner_module
from benchmarks.external_runner import (
    ExternalRunnerError,
    RunnerIdentity,
    RunnerLimits,
    load_external_run_manifest,
    run_external_cases,
    run_external_command,
)
from benchmarks.external_runner import main as runner_main
from benchmarks.json_io import StrictJsonLimits
from benchmarks.lrcbench import (
    CANDIDATE_SCHEMA,
    CORPUS_PRODUCER_SCHEMA,
    BenchmarkConfig,
    CorpusProducerMetadata,
    ExternalBaselineError,
    _canonical_sha256,
    corpus_document,
    dataset_digest,
    decode_corpus_document,
    generate_histories,
    load_external_candidates,
    run_benchmark,
)
from context_compiler.local_qwen import QWEN_Q4_VARIANT
from tests.protocol_fixtures import write_frozen_external_protocol

FIXTURE_ADAPTER_REVISION = "a" * 40
FIXTURE_ENVIRONMENT_ID = "sha256:" + "b" * 64


def corpus_producer(
    *,
    created_at: str = "2026-01-01T00:00:00+00:00",
) -> CorpusProducerMetadata:
    return CorpusProducerMetadata(
        created_at=created_at,
        repository_commit=None,
        repository_dirty=None,
        package_version="0.1.0",
        python_version="test-python",
        platform="test-platform",
        command=("pytest", "external-runner"),
    )


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
    document = corpus_document(
        cases,
        config,
        dataset_digest(cases, config),
        producer=corpus_producer(),
    )
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
        adapter_revision=FIXTURE_ADAPTER_REVISION,
        environment_id=FIXTURE_ENVIRONMENT_ID,
        model_id=QWEN_Q4_VARIANT,
        model_context_length=8192,
        tokenizer_id="character-estimate-v1",
        inference_concurrency=1,
        retry_count=0,
        model_service_cost_usd=0.0,
    )


def test_claim_identity_requires_frozen_model_and_environment_contract() -> None:
    identity = claim_identity()

    assert identity.claim_metadata_complete
    assert not replace(identity, model_id="another/model").claim_metadata_complete
    assert not replace(
        identity,
        environment_id="fixture-environment",
    ).claim_metadata_complete
    assert not replace(
        identity,
        adapter_revision="fixture-revision",
    ).claim_metadata_complete
    assert not replace(identity, model_context_length=4096).claim_metadata_complete
    assert not replace(identity, tokenizer_id="other-tokenizer").claim_metadata_complete
    assert not replace(identity, inference_concurrency=2).claim_metadata_complete
    assert not replace(identity, retry_count=1).claim_metadata_complete
    assert not replace(identity, model_service_cost_usd=0.01).claim_metadata_complete
    with pytest.raises(TypeError, match="model_service_cost_usd must be numeric"):
        replace(identity, model_service_cost_usd=None)  # type: ignore[arg-type]


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


def test_corpus_producer_changes_evidence_without_changing_dataset_identity() -> None:
    config = BenchmarkConfig(
        histories=1,
        messages_per_history=24,
        noise_lines_per_message=1,
        token_budget=900,
        bootstrap_samples=100,
    )
    cases = generate_histories(config)
    digest = dataset_digest(cases, config)
    first = corpus_document(
        cases,
        config,
        digest,
        producer=corpus_producer(),
    )
    second = corpus_document(
        cases,
        config,
        digest,
        producer=corpus_producer(created_at="2026-01-02T00:00:00+00:00"),
    )

    assert first["producer"]["schema"] == CORPUS_PRODUCER_SCHEMA
    assert first["dataset_sha256"] == second["dataset_sha256"] == digest
    assert first["corpus_sha256"] != second["corpus_sha256"]

    tampered = json.loads(json.dumps(first))
    tampered["producer"]["tokenizer_id"] = "wrong-tokenizer"
    tampered.pop("corpus_sha256")
    tampered["corpus_sha256"] = _canonical_sha256(tampered)
    with pytest.raises(ExternalBaselineError, match="tokenizer_id must be"):
        decode_corpus_document(tampered)


def test_interchange_file_loaders_reject_duplicate_keys_and_size_overflow(
    tmp_path,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    config, document = write_corpus(corpus_path)
    original_corpus = corpus_path.read_text(encoding="utf-8")
    corpus_path.write_text(
        original_corpus.replace(
            "{",
            '{"schema":"lrcbench-corpus-0.3",',
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ExternalRunnerError, match="duplicate JSON object key"):
        external_runner_module._load_corpus(corpus_path)
    corpus_path.write_text(original_corpus, encoding="utf-8")

    candidate_payload = {
        "schema": CANDIDATE_SCHEMA,
        "dataset_sha256": document["dataset_sha256"],
        "system": "strict-fixture",
        "cases": [
            {
                "case_id": case["case_id"],
                "rendered_text": "",
                "claims": [],
            }
            for case in document["cases"]
        ],
    }
    candidate_path = tmp_path / "candidate.json"
    candidate_text = json.dumps(candidate_payload)
    systems = (
        "strict-fixture",
        "strict-fixture-b",
        "strict-fixture-c",
        "strict-fixture-d",
    )
    protocol_path = write_frozen_external_protocol(
        tmp_path,
        systems,
        synthetic_dataset_sha256=document["dataset_sha256"],
    )
    candidate_path.write_text(
        candidate_text.replace(
            "{",
            f'{{"schema":"{CANDIDATE_SCHEMA}",',
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ExternalBaselineError, match="duplicate JSON object key"):
        run_benchmark(
            config,
            external_baseline_paths=(candidate_path,),
            expected_external_systems=systems,
            external_protocol_path=protocol_path,
        )

    candidate_path.write_text(candidate_text, encoding="utf-8")
    with pytest.raises(ExternalBaselineError, match="external candidate exceeds"):
        load_external_candidates(
            (candidate_path,),
            cases=generate_histories(config),
            dataset_sha256=document["dataset_sha256"],
            token_budget=config.token_budget,
            max_candidate_bytes=len(candidate_text.encode("utf-8")) - 1,
        )
    with pytest.raises(
        ExternalBaselineError,
        match="candidate byte limit does not match",
    ):
        run_benchmark(
            config,
            external_baseline_paths=(candidate_path,),
            expected_external_systems=systems,
            external_protocol_path=protocol_path,
            max_external_candidate_bytes=len(candidate_text.encode("utf-8")) - 1,
        )
    with pytest.raises(ExternalBaselineError, match="max_bytes must be positive"):
        run_benchmark(
            config,
            max_external_candidate_bytes=0,
        )

    runner_candidate = tmp_path / "runner-candidate.json"
    manifest = run_external_command(
        valid_adapter_command(),
        system="strict-fixture",
        corpus_path=corpus_path,
        candidate_path=runner_candidate,
        limits=RunnerLimits(timeout_seconds=5),
    )
    manifest_text = manifest.to_json()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        manifest_text.replace(
            "{",
            f'{{"schema":"{external_runner_module.RUNNER_MANIFEST_SCHEMA}",',
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ExternalRunnerError, match="duplicate JSON object key"):
        load_external_run_manifest(
            manifest_path,
            expected_dataset_sha256=document["dataset_sha256"],
        )


def test_corpus_loader_enforces_serialized_size_before_decoding(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    byte_count = corpus_path.stat().st_size
    monkeypatch.setattr(
        external_runner_module,
        "_CORPUS_JSON_LIMITS",
        StrictJsonLimits(
            max_bytes=byte_count - 1,
            max_line_chars=byte_count,
            max_depth=128,
        ),
    )

    with pytest.raises(ExternalRunnerError, match="benchmark corpus exceeds"):
        external_runner_module._load_corpus(corpus_path)


def test_candidate_mutation_during_validation_fails_closed(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    original_loader = external_runner_module.load_strict_json_file
    mutated = False

    def mutate_before_candidate_read(path, *, limits, label):
        nonlocal mutated
        if label == "external candidate" and not mutated:
            mutated = True
            candidate = Path(path)
            candidate.write_text(
                candidate.read_text(encoding="utf-8") + " ",
                encoding="utf-8",
            )
        return original_loader(path, limits=limits, label=label)

    monkeypatch.setattr(
        external_runner_module,
        "load_strict_json_file",
        mutate_before_candidate_read,
    )

    manifest = run_external_command(
        valid_adapter_command(),
        system="strict-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5),
    )

    assert mutated
    assert manifest.process_succeeded
    assert not manifest.candidate_valid
    assert not manifest.ready_for_scoring
    assert manifest.validation_error == "candidate changed while it was being validated"


def test_per_case_candidate_mutation_before_aggregation_is_rejected(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    original_loader = external_runner_module.load_strict_json_file

    def mutate_before_aggregate_read(path, *, limits, label):
        if label == "per-case external candidate":
            candidate = Path(path)
            candidate.write_text(
                candidate.read_text(encoding="utf-8") + " ",
                encoding="utf-8",
            )
        return original_loader(path, limits=limits, label=label)

    monkeypatch.setattr(
        external_runner_module,
        "load_strict_json_file",
        mutate_before_aggregate_read,
    )

    with pytest.raises(ExternalRunnerError, match="changed before aggregation"):
        run_external_cases(
            valid_adapter_command(),
            system="strict-fixture",
            corpus_path=corpus_path,
            candidate_path=candidate_path,
            limits=RunnerLimits(timeout_seconds=5, max_memory_mb=256),
            identity=claim_identity(),
        )


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
    candidate_payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate_payload_sha256 = candidate_payload.pop("candidate_payload_sha256")
    assert candidate_payload["schema"] == CANDIDATE_SCHEMA
    assert candidate_payload["producer"] == claim_identity().to_candidate_producer().to_dict()
    assert candidate_payload_sha256 == _canonical_sha256(candidate_payload)
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
            FIXTURE_ADAPTER_REVISION,
            "--environment-id",
            FIXTURE_ENVIRONMENT_ID,
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


def test_ready_manifest_reloads_candidate_and_binds_benchmark_evidence(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    config, document = write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    manifest_path = tmp_path / "manifest.json"
    manifest = run_external_cases(
        valid_adapter_command(),
        system="fixture-adapter",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=300, max_memory_mb=256),
        identity=claim_identity(),
    )
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")
    systems = (
        "fixture-adapter",
        "fixture-adapter-b",
        "fixture-adapter-c",
        "fixture-adapter-d",
    )
    protocol_path = write_frozen_external_protocol(
        tmp_path,
        systems,
        adapter_revisions={
            "fixture-adapter": FIXTURE_ADAPTER_REVISION,
        },
        environment_ids={
            "fixture-adapter": FIXTURE_ENVIRONMENT_ID,
        },
        synthetic_dataset_sha256=document["dataset_sha256"],
    )

    reference = load_external_run_manifest(
        manifest_path,
        expected_dataset_sha256=document["dataset_sha256"],
    )
    report = run_benchmark(
        config,
        external_manifest_paths=(manifest_path,),
        expected_external_systems=systems,
        external_protocol_path=protocol_path,
    )

    assert reference.system == "fixture-adapter"
    assert reference.candidate_path == candidate_path.resolve()
    assert reference.candidate_bytes == candidate_path.stat().st_size
    assert reference.candidate_sha256 == hashlib.sha256(
        candidate_path.read_bytes()
    ).hexdigest()
    assert reference.failure_reason is None
    assert reference.adapter_revision == FIXTURE_ADAPTER_REVISION
    assert reference.environment_id == FIXTURE_ENVIRONMENT_ID
    assert reference.model_id == QWEN_Q4_VARIANT
    assert reference.model_service_cost_usd == 0.0
    assert report.run_metadata.model_id == "unrecorded"
    assert report.run_metadata.model_service_cost_usd is None
    assert {
        revision.name: revision.revision
        for revision in report.run_metadata.baseline_revisions
    }["fixture-adapter"] == FIXTURE_ADAPTER_REVISION
    assert report.certificate.external_manifests[0].system == "fixture-adapter"
    assert (
        report.certificate.external_manifests[0].manifest_sha256
        == manifest.to_dict()["manifest_sha256"]
    )
    comparison = next(
        value for value in report.certificate.comparisons if value.system == "fixture-adapter"
    )
    assert "no validated external run manifest" not in comparison.reasons
    mismatched_protocol_directory = tmp_path / "mismatched-environment"
    mismatched_protocol_directory.mkdir()
    mismatched_protocol_path = write_frozen_external_protocol(
        mismatched_protocol_directory,
        systems,
        adapter_revisions={
            "fixture-adapter": FIXTURE_ADAPTER_REVISION,
        },
        environment_ids={
            "fixture-adapter": "sha256:" + "c" * 64,
        },
        synthetic_dataset_sha256=document["dataset_sha256"],
    )
    with pytest.raises(
        ExternalBaselineError,
        match="environment identity does not match the frozen protocol",
    ):
        run_benchmark(
            config,
            external_manifest_paths=(manifest_path,),
            expected_external_systems=systems,
            external_protocol_path=mismatched_protocol_path,
        )
    mismatched_limits_directory = tmp_path / "mismatched-limits"
    mismatched_limits_directory.mkdir()
    mismatched_limits_protocol = write_frozen_external_protocol(
        mismatched_limits_directory,
        systems,
        adapter_revisions={
            "fixture-adapter": FIXTURE_ADAPTER_REVISION,
        },
        environment_ids={
            "fixture-adapter": FIXTURE_ENVIRONMENT_ID,
        },
        synthetic_dataset_sha256=document["dataset_sha256"],
        max_memory_mb=512,
    )
    with pytest.raises(
        ExternalBaselineError,
        match="run manifest limits do not match the frozen protocol",
    ):
        run_benchmark(
            config,
            external_manifest_paths=(manifest_path,),
            expected_external_systems=systems,
            external_protocol_path=mismatched_limits_protocol,
        )

    mismatched_poll_candidate = tmp_path / "mismatched-poll-candidate.json"
    mismatched_poll_manifest_path = tmp_path / "mismatched-poll-manifest.json"
    mismatched_poll_manifest = run_external_cases(
        valid_adapter_command(),
        system="fixture-adapter",
        corpus_path=corpus_path,
        candidate_path=mismatched_poll_candidate,
        limits=RunnerLimits(
            timeout_seconds=300,
            max_memory_mb=256,
            poll_interval_seconds=0.01,
        ),
        identity=claim_identity(),
    )
    mismatched_poll_manifest_path.write_text(
        mismatched_poll_manifest.to_json(),
        encoding="utf-8",
    )
    with pytest.raises(
        ExternalBaselineError,
        match="run manifest limits do not match the frozen protocol",
    ):
        run_benchmark(
            config,
            external_manifest_paths=(mismatched_poll_manifest_path,),
            expected_external_systems=systems,
            external_protocol_path=protocol_path,
        )

    mismatched_model_candidate = tmp_path / "mismatched-model-candidate.json"
    mismatched_model_manifest_path = tmp_path / "mismatched-model-manifest.json"
    mismatched_model_manifest = run_external_cases(
        valid_adapter_command(),
        system="fixture-adapter",
        corpus_path=corpus_path,
        candidate_path=mismatched_model_candidate,
        limits=RunnerLimits(timeout_seconds=300, max_memory_mb=256),
        identity=replace(claim_identity(), model_context_length=4096),
    )
    mismatched_model_manifest_path.write_text(
        mismatched_model_manifest.to_json(),
        encoding="utf-8",
    )
    with pytest.raises(
        ExternalBaselineError,
        match="run manifest model contract does not match the frozen protocol",
    ):
        run_benchmark(
            config,
            external_manifest_paths=(mismatched_model_manifest_path,),
            expected_external_systems=systems,
            external_protocol_path=protocol_path,
        )

    original_manifest_loader = external_runner_module.load_external_run_manifest

    def load_then_mutate_candidate(*args, **kwargs):
        loaded = original_manifest_loader(*args, **kwargs)
        candidate_path.write_text(
            candidate_path.read_text(encoding="utf-8") + " ",
            encoding="utf-8",
        )
        return loaded

    monkeypatch.setattr(
        external_runner_module,
        "load_external_run_manifest",
        load_then_mutate_candidate,
    )
    with pytest.raises(ExternalBaselineError, match="changed after manifest validation"):
        run_benchmark(
            config,
            external_manifest_paths=(manifest_path,),
            expected_external_systems=systems,
            external_protocol_path=protocol_path,
        )

    corpus_path.write_text(
        corpus_path.read_text(encoding="utf-8") + " ",
        encoding="utf-8",
    )
    with pytest.raises(ExternalRunnerError, match="corpus file SHA-256"):
        load_external_run_manifest(
            manifest_path,
            expected_dataset_sha256=document["dataset_sha256"],
        )


def test_ready_manifest_wraps_candidate_disappearance_during_validation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    _config, document = write_corpus(corpus_path)
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
    original_loader = external_runner_module.load_strict_json_file

    def remove_before_candidate_load(path, *, limits, label):
        if label == "manifest candidate":
            candidate_path.unlink()
        return original_loader(path, limits=limits, label=label)

    monkeypatch.setattr(
        external_runner_module,
        "load_strict_json_file",
        remove_before_candidate_load,
    )

    with pytest.raises(
        ExternalRunnerError,
        match="manifest candidate could not be validated",
    ):
        load_external_run_manifest(
            manifest_path,
            expected_dataset_sha256=document["dataset_sha256"],
        )


def test_ready_manifest_rejects_rehashed_candidate_producer_mismatch(
    tmp_path,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    _config, document = write_corpus(corpus_path)
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
    candidate_payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate_payload["producer"]["adapter_revision"] = "forged-revision"
    candidate_payload.pop("candidate_payload_sha256")
    candidate_payload["candidate_payload_sha256"] = _canonical_sha256(
        candidate_payload
    )
    candidate_text = (
        json.dumps(
            candidate_payload,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    )
    candidate_path.write_text(candidate_text, encoding="utf-8")
    candidate_bytes = candidate_path.read_bytes()

    manifest_payload = manifest.to_dict()
    manifest_payload["candidate_bytes"] = len(candidate_bytes)
    manifest_payload["candidate_sha256"] = hashlib.sha256(candidate_bytes).hexdigest()
    manifest_payload.pop("manifest_sha256")
    manifest_payload["manifest_sha256"] = _canonical_sha256(manifest_payload)
    manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")

    with pytest.raises(ExternalRunnerError, match="registered runner identity"):
        load_external_run_manifest(
            manifest_path,
            expected_dataset_sha256=document["dataset_sha256"],
        )


def test_ready_candidate_with_unrecorded_identity_cannot_cross_frozen_protocol(
    tmp_path,
) -> None:
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
    )
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")
    systems = (
        "fixture-adapter",
        "fixture-adapter-b",
        "fixture-adapter-c",
        "fixture-adapter-d",
    )
    protocol_path = write_frozen_external_protocol(
        tmp_path,
        systems,
        adapter_revisions={
            "fixture-adapter": FIXTURE_ADAPTER_REVISION,
        },
        environment_ids={
            "fixture-adapter": FIXTURE_ENVIRONMENT_ID,
        },
        synthetic_dataset_sha256=document["dataset_sha256"],
    )

    with pytest.raises(
        ExternalBaselineError,
        match="adapter revision does not match the frozen protocol",
    ):
        run_benchmark(
            config,
            external_manifest_paths=(manifest_path,),
            expected_external_systems=systems,
            external_protocol_path=protocol_path,
        )

    assert manifest.ready_for_scoring
    assert not manifest.claim_metadata_complete


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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows sharing violation regression")
def test_windows_temp_cleanup_retries_sharing_violation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "runner-temp"
    directory.mkdir()
    (directory / "stderr.bin").write_bytes(b"fixture")
    real_rmtree = external_runner_module.shutil.rmtree
    calls = 0

    def fail_once(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            error = PermissionError(13, "injected sharing violation")
            error.winerror = 32
            raise error
        real_rmtree(path)

    monkeypatch.setattr(external_runner_module.shutil, "rmtree", fail_once)

    external_runner_module._remove_runner_directory(directory)

    assert calls == 2
    assert not directory.exists()


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


def test_failed_exact_contract_manifest_becomes_a_registered_invalid_nonwin(
    tmp_path,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    config, document = write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    manifest_path = tmp_path / "manifest.json"
    manifest = run_external_cases(
        [sys.executable, "-c", "raise SystemExit(7)"],
        system="timeout-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=300, max_memory_mb=256),
        identity=claim_identity(),
    )
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")
    systems = (
        "timeout-fixture",
        "timeout-fixture-b",
        "timeout-fixture-c",
        "timeout-fixture-d",
    )
    protocol_path = write_frozen_external_protocol(
        tmp_path,
        systems,
        adapter_revisions={
            "timeout-fixture": FIXTURE_ADAPTER_REVISION,
        },
        environment_ids={
            "timeout-fixture": FIXTURE_ENVIRONMENT_ID,
        },
        synthetic_dataset_sha256=document["dataset_sha256"],
    )

    report = run_benchmark(
        config,
        external_manifest_paths=(manifest_path,),
        expected_external_systems=systems,
        external_protocol_path=protocol_path,
    )

    comparison = next(
        value for value in report.certificate.comparisons if value.system == "timeout-fixture"
    )
    assert comparison.decision == "invalid"
    assert any(reason.endswith("nonzero_exit") for reason in comparison.reasons)
    assert report.certificate.external_manifests[
        0
    ].failure_reason.endswith("nonzero_exit")


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
