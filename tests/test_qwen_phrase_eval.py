from __future__ import annotations

import copy
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from benchmarks.phrase_eval import PhraseCorpus, load_phrase_corpus
from benchmarks.qwen_phrase_eval import (
    DEFAULT_QWEN_PHRASE_REPORT,
    EVALUATION_MAX_CANDIDATES,
    EVALUATION_MAX_OUTPUT_CHARS,
    EVALUATION_TIMEOUT_SECONDS,
    NORMALIZED_QWEN_PHRASE_COMMAND,
    QWEN_PHRASE_REPORT_SCHEMA,
    QWEN_PHRASE_VERIFICATION_SCHEMA,
    QwenPhraseEvaluationError,
    _canonical_sha256,
    exact_qwen_system,
    load_qwen_phrase_report,
    main,
    run_qwen_phrase_evaluation,
    verify_qwen_phrase_report,
)
from context_compiler import __version__
from context_compiler.local_qwen import QWEN_Q4_VARIANT


class _StringSubclass(str):
    pass


def preflight() -> dict[str, Any]:
    return {
        "model_id": QWEN_Q4_VARIANT,
        "quantization": "Q4_K_M",
        "parallel": 1,
        "context_length": 8_192,
        "transport": "lm-studio-cli",
        "network_model_api": False,
        "command_line_prompt_exposure": True,
    }


def system() -> dict[str, Any]:
    return exact_qwen_system(
        preflight(),
        lms_executable_sha256="a" * 64,
        lms_cli_version="lms test version",
    )


class CorpusCompletion:
    def __init__(
        self,
        corpus: PhraseCorpus,
        *,
        mode: str = "perfect",
    ) -> None:
        self.by_source_id = {
            f"phrase:{case.id}": case for case in corpus.cases
        }
        self.mode = mode
        self.calls = 0
        self.active = False
        self.maximum_active = 0

    def __call__(self, prompt: str) -> str:
        assert not self.active
        self.active = True
        self.maximum_active = max(self.maximum_active, 1)
        try:
            self.calls += 1
            payload = json.loads(prompt)
            source = payload["sources"][0]
            case = self.by_source_id[source["source_id"]]
            if self.mode == "empty":
                items: list[dict[str, Any]] = []
            elif self.mode == "invalid":
                items = [
                    {
                        "kind": "constraint",
                        "text": "not an exact source span",
                        "provenance": [
                            {
                                "source_id": source["source_id"],
                                "start": 0,
                                "end": len(source["content"]),
                            }
                        ],
                    }
                ]
            else:
                items = [
                    {
                        "kind": atom.kind.value,
                        "text": atom.text,
                        "exact": atom.kind.value
                        in {"exact_error", "exact_reference"},
                        "provenance": [
                            {
                                "source_id": source["source_id"],
                                "start": atom.start,
                                "end": atom.end,
                            }
                        ],
                    }
                    for atom in case.expected
                ]
            return json.dumps(
                {"items": items},
                separators=(",", ":"),
            )
        finally:
            self.active = False


def run_report(
    corpus: PhraseCorpus,
    complete: Any,
) -> dict[str, Any]:
    return run_qwen_phrase_evaluation(
        corpus,
        complete,
        system=system(),
        command=NORMALIZED_QWEN_PHRASE_COMMAND,
        repository_commit="b" * 40,
        repository_dirty=False,
    )


@pytest.fixture(scope="module")
def corpus() -> PhraseCorpus:
    return load_phrase_corpus()


@pytest.fixture(scope="module")
def perfect_report(corpus: PhraseCorpus) -> dict[str, Any]:
    return run_report(corpus, CorpusCompletion(corpus))


def resign(report: dict[str, Any]) -> None:
    unsigned = dict(report)
    unsigned.pop("report_sha256", None)
    report["report_sha256"] = _canonical_sha256(unsigned)


def test_perfect_captured_model_is_scored_separately_and_replays(
    corpus: PhraseCorpus,
) -> None:
    completion = CorpusCompletion(corpus)

    report = run_report(corpus, completion)
    verification = verify_qwen_phrase_report(report, corpus)

    assert completion.calls == len(corpus.cases) == 64
    assert completion.maximum_active == 1
    assert report["schema"] == QWEN_PHRASE_REPORT_SCHEMA
    assert report["system"] == {
        "model_id": QWEN_Q4_VARIANT,
        "quantization": "Q4_K_M",
        "parallel": 1,
        "context_length": 8_192,
        "transport": "lm-studio-cli",
        "network_model_api": False,
        "command_line_prompt_exposure": True,
        "model_service_cost_usd": 0.0,
        "max_output_chars": EVALUATION_MAX_OUTPUT_CHARS,
        "max_response_chars": EVALUATION_MAX_OUTPUT_CHARS,
        "max_candidates": EVALUATION_MAX_CANDIDATES,
        "timeout_seconds": EVALUATION_TIMEOUT_SECONDS,
        "per_call_preflight": True,
        "lms_executable_sha256": "a" * 64,
        "lms_cli_version": "lms test version",
    }
    assert report["run"]["package_version"] == __version__
    assert report["run"]["repository_dirty"] is False
    assert report["metrics"]["model_only"]["precision"] == 1.0
    assert report["metrics"]["model_only"]["recall"] == 1.0
    assert report["metrics"]["model_only"]["case_exact_matches"] == 64
    assert report["metrics"]["candidates"] == {
        "reported_candidates": 40,
        "accepted_candidates": 40,
        "rejected_candidates": 0,
        "candidate_rejection_rate": 0.0,
        "rejection_events": 0,
        "rejection_reasons": {},
        "degraded_cases": 0,
    }
    assert report["metrics"]["cost"] == {
        "model_service_cost_usd": 0.0,
        "cost_per_case_usd": 0.0,
    }
    assert report["metrics"]["latency"]["model_calls"] == 64
    assert report["metrics"]["deterministic_recovery"]["recall_gain"] == 0.0
    assert all(
        case["capture"]["raw_output_sha256"]
        == hashlib.sha256(
            case["capture"]["raw_output"].encode("utf-8")
        ).hexdigest()
        for case in report["cases"]
    )
    assert verification == {
        "schema": QWEN_PHRASE_VERIFICATION_SCHEMA,
        "verified": True,
        "corpus_sha256": corpus.corpus_sha256,
        "report_sha256": report["report_sha256"],
        "case_count": 64,
        "model_id": QWEN_Q4_VARIANT,
        "model_calls": 64,
        "network_model_api": False,
        "model_service_cost_usd": 0.0,
    }


@pytest.mark.parametrize("package_version", ["0.1.0", "0.1.1a1", "0.1.1a2", "0.1.1a3", "0.1.1a4"])
def test_qwen_phrase_reader_accepts_only_recorded_supported_versions(
    package_version: str,
    corpus: PhraseCorpus,
    perfect_report: dict[str, Any],
) -> None:
    report = copy.deepcopy(perfect_report)
    report["run"]["package_version"] = package_version
    resign(report)

    assert verify_qwen_phrase_report(report, corpus)["verified"] is True


@pytest.mark.parametrize(
    "package_version",
    [True, 1, 0.1, None, "0.1.1", "0.1.1a1 ", _StringSubclass("0.1.1a1")],
)
def test_qwen_phrase_reader_rejects_other_package_versions(
    package_version: object,
    corpus: PhraseCorpus,
    perfect_report: dict[str, Any],
) -> None:
    report = copy.deepcopy(perfect_report)
    report["run"]["package_version"] = package_version
    resign(report)

    with pytest.raises(QwenPhraseEvaluationError, match="package_version is unsupported"):
        verify_qwen_phrase_report(report, corpus)


def test_empty_model_exposes_deterministic_recovery_contribution(
    corpus: PhraseCorpus,
) -> None:
    report = run_report(
        corpus,
        CorpusCompletion(corpus, mode="empty"),
    )
    metrics = report["metrics"]

    assert metrics["model_only"]["predicted_atoms"] == 0
    assert metrics["model_only"]["recall"] == 0.0
    assert metrics["deterministic_recovery"]["added_atoms"] > 0
    assert (
        metrics["deterministic_recovery"][
            "expected_atoms_added_after_model_miss"
        ]
        > 0
    )
    assert metrics["deterministic_recovery"]["recall_gain"] > 0
    assert metrics["final_compiler"]["recall"] > 0
    assert verify_qwen_phrase_report(report, corpus)["verified"]


def test_invalid_candidates_have_an_explicit_rejection_rate(
    corpus: PhraseCorpus,
) -> None:
    report = run_report(
        corpus,
        CorpusCompletion(corpus, mode="invalid"),
    )
    candidates = report["metrics"]["candidates"]

    assert candidates["reported_candidates"] == 64
    assert candidates["accepted_candidates"] == 0
    assert candidates["rejected_candidates"] == 64
    assert candidates["candidate_rejection_rate"] == 1.0
    assert candidates["rejection_events"] == 64
    assert candidates["rejection_reasons"] == {
        "invalid_candidate": 64
    }
    assert candidates["degraded_cases"] == 64
    assert verify_qwen_phrase_report(report, corpus)["verified"]


def test_transport_failures_are_captured_and_replayed_without_a_model(
    corpus: PhraseCorpus,
) -> None:
    calls = 0

    def timeout(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        raise TimeoutError("local transport")

    report = run_report(corpus, timeout)
    metrics = report["metrics"]

    assert calls == 64
    assert metrics["completion"] == {
        "successful_calls": 0,
        "failed_calls": 64,
        "error_types": {"TimeoutError": 64},
    }
    assert metrics["candidates"]["degraded_cases"] == 64
    assert all(
        case["capture"]["raw_output"] is None
        and case["capture"]["error_type"] == "TimeoutError"
        for case in report["cases"]
    )
    assert verify_qwen_phrase_report(report, corpus)["verified"]


def test_non_string_completion_fails_closed_into_recorded_fallback(
    corpus: PhraseCorpus,
) -> None:
    report = run_report(corpus, lambda _prompt: {"items": []})

    assert report["metrics"]["completion"]["error_types"] == {
        "TypeError": 64
    }
    assert report["metrics"]["completion"]["failed_calls"] == 64
    assert verify_qwen_phrase_report(report, corpus)["verified"]


def test_replay_rejects_rehashed_output_prompt_and_metric_tampering(
    corpus: PhraseCorpus,
    perfect_report: dict[str, Any],
) -> None:
    output_tamper = copy.deepcopy(perfect_report)
    capture = output_tamper["cases"][0]["capture"]
    capture["raw_output"] = '{"items":[]}'
    capture["raw_output_chars"] = len(capture["raw_output"])
    capture["raw_output_sha256"] = hashlib.sha256(
        capture["raw_output"].encode("utf-8")
    ).hexdigest()
    resign(output_tamper)
    with pytest.raises(
        QwenPhraseEvaluationError,
        match="case analysis",
    ):
        verify_qwen_phrase_report(output_tamper, corpus)

    prompt_tamper = copy.deepcopy(perfect_report)
    prompt_tamper["cases"][0]["capture"]["prompt_sha256"] = "0" * 64
    resign(prompt_tamper)
    with pytest.raises(
        QwenPhraseEvaluationError,
        match="captured prompt mismatch",
    ):
        verify_qwen_phrase_report(prompt_tamper, corpus)

    metric_tamper = copy.deepcopy(perfect_report)
    metric_tamper["metrics"]["model_only"]["recall"] = 0.5
    resign(metric_tamper)
    with pytest.raises(
        QwenPhraseEvaluationError,
        match="metrics do not match",
    ):
        verify_qwen_phrase_report(metric_tamper, corpus)


def test_strict_system_types_and_hash_are_enforced(
    corpus: PhraseCorpus,
    perfect_report: dict[str, Any],
) -> None:
    boolean_forgery = copy.deepcopy(perfect_report)
    boolean_forgery["system"]["network_model_api"] = 0
    resign(boolean_forgery)
    with pytest.raises(
        QwenPhraseEvaluationError,
        match="network_model_api",
    ):
        verify_qwen_phrase_report(boolean_forgery, corpus)

    hash_tamper = copy.deepcopy(perfect_report)
    hash_tamper["report_sha256"] = "0" * 64
    with pytest.raises(
        QwenPhraseEvaluationError,
        match="SHA-256 mismatch",
    ):
        verify_qwen_phrase_report(hash_tamper, corpus)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("model_id", "different/model"),
        ("quantization", "Q8_0"),
        ("parallel", 2),
        ("parallel", True),
        ("context_length", 0),
        ("context_length", 16_384),
        ("network_model_api", True),
        ("network_model_api", 0),
        ("command_line_prompt_exposure", False),
    ),
)
def test_exact_system_refuses_nonconforming_preflight(
    field: str,
    value: Any,
) -> None:
    forged = preflight()
    forged[field] = value

    with pytest.raises(QwenPhraseEvaluationError):
        exact_qwen_system(
            forged,
            lms_executable_sha256="a" * 64,
            lms_cli_version="test",
        )


def test_invalid_run_identity_fails_before_any_model_call(
    corpus: PhraseCorpus,
) -> None:
    calls = 0

    def completion(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        return '{"items":[]}'

    with pytest.raises(
        QwenPhraseEvaluationError,
        match="Git object id",
    ):
        run_qwen_phrase_evaluation(
            corpus,
            completion,
            system=system(),
            command=["test"],
            repository_commit="not-a-commit",
            repository_dirty=False,
        )
    assert calls == 0

    with pytest.raises(TypeError, match="sequence of strings"):
        run_qwen_phrase_evaluation(
            corpus,
            completion,
            system=system(),
            command="not-a-command-list",
            repository_commit="b" * 40,
            repository_dirty=False,
        )
    assert calls == 0

    with pytest.raises(
        QwenPhraseEvaluationError,
        match="normalized_command",
    ):
        run_qwen_phrase_evaluation(
            corpus,
            completion,
            system=system(),
            command=["python", "-m", "benchmarks.qwen_phrase_eval"],
            repository_commit="b" * 40,
            repository_dirty=False,
        )
    assert calls == 0


def test_rehashed_noncanonical_command_is_rejected(
    corpus: PhraseCorpus,
    perfect_report: dict[str, Any],
) -> None:
    tampered = copy.deepcopy(perfect_report)
    tampered["run"]["normalized_command"][0] = sys.executable
    resign(tampered)

    with pytest.raises(
        QwenPhraseEvaluationError,
        match="normalized_command",
    ):
        verify_qwen_phrase_report(tampered, corpus)


def test_loader_and_cli_verify_saved_report_without_lms(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    corpus: PhraseCorpus,
    perfect_report: dict[str, Any],
) -> None:
    path = tmp_path / "qwen-report.json"
    path.write_text(
        json.dumps(perfect_report, indent=2),
        encoding="utf-8",
    )

    loaded = load_qwen_phrase_report(path)
    assert loaded["report_sha256"] == perfect_report["report_sha256"]
    assert main(["--verify-report", str(path)]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["schema"] == QWEN_PHRASE_VERIFICATION_SCHEMA
    assert output["verified"] is True
    assert output["model_calls"] == 64


def test_committed_exact_qwen_report_replays_without_model(
    corpus: PhraseCorpus,
) -> None:
    report = load_qwen_phrase_report(DEFAULT_QWEN_PHRASE_REPORT)
    verification = verify_qwen_phrase_report(report, corpus)

    assert verification == {
        "schema": QWEN_PHRASE_VERIFICATION_SCHEMA,
        "verified": True,
        "corpus_sha256": corpus.corpus_sha256,
        "report_sha256": (
            "db2e054537e366056a8fd482f1a8e17b8fa0d02163a08ea79ef3c682af79c171"
        ),
        "case_count": 64,
        "model_id": QWEN_Q4_VARIANT,
        "model_calls": 64,
        "network_model_api": False,
        "model_service_cost_usd": 0.0,
    }
    assert report["run"]["repository_commit"] == (
        "f79fe2bdd39e046d79fc41491f8ada87e8b42438"
    )
    assert report["run"]["repository_dirty"] is False
    model_only = report["metrics"]["model_only"]
    assert {
        name: model_only[name]
        for name in (
            "predicted_atoms",
            "true_positives",
            "false_positives",
            "false_negatives",
            "precision",
            "recall",
            "f1",
        )
    } == {
        "predicted_atoms": 2,
        "true_positives": 2,
        "false_positives": 0,
        "false_negatives": 38,
        "precision": 1.0,
        "recall": 0.05,
        "f1": 0.095238,
    }
    assert report["metrics"]["candidates"] == {
        "reported_candidates": 65,
        "accepted_candidates": 2,
        "rejected_candidates": 63,
        "candidate_rejection_rate": 0.969231,
        "rejection_events": 63,
        "rejection_reasons": {"invalid_candidate": 63},
        "degraded_cases": 62,
    }
    assert report["metrics"]["deterministic_recovery"]["recall_gain"] == 0.825
    assert report["metrics"]["final_compiler"]["precision"] == 0.853659
    assert report["metrics"]["final_compiler"]["recall"] == 0.875
    assert report["metrics"]["final_compiler"]["verification_failures"] == 0
    assert all(case["primary"]["candidate_count"] >= 1 for case in report["cases"])
    assert sum(
        case["primary"]["candidate_count"]
        for case in report["cases"]
        if not case["expected"]
    ) == 24
    rejection_details = Counter(
        rejection["detail"]
        for case in report["cases"]
        for rejection in case["primary"]["rejections"]
    )
    assert rejection_details == {
        "provenance span exceeds source content": 28,
        "exact candidate text must equal its source literal": 14,
        "candidate text must equal a cited source literal": 12,
        (
            "constraint requires provenance exclusively from an authoritative role"
        ): 4,
        "confirmed_fact cannot be sourced from untrusted tool output": 1,
        (
            "user_correction requires provenance exclusively from an "
            "authoritative role"
        ): 1,
        "decision cannot be sourced from tool output": 1,
        "candidate source literal is not an atomic clause": 1,
        "unresolved cannot be sourced from tool output": 1,
    }


def test_loader_rejects_duplicate_keys(
    tmp_path: Path,
    perfect_report: dict[str, Any],
) -> None:
    encoded = json.dumps(perfect_report)
    duplicate = encoded.replace(
        "{",
        '{"schema":"ctxc-qwen-phrase-eval-report-0.1",',
        1,
    )
    path = tmp_path / "duplicate.json"
    path.write_text(duplicate, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate JSON object key"):
        load_qwen_phrase_report(path)
