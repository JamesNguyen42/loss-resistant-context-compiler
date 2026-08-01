from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from benchmarks.phrase_eval import PhraseCorpus, load_phrase_corpus
from benchmarks.qwen_paired_eval import (
    DEFAULT_HELDOUT_LITERAL_CORPUS,
    DEFAULT_HELDOUT_LITERAL_CORPUS_SHA256,
    DEFAULT_QWEN_PAIRED_REPORT,
    NORMALIZED_QWEN_PAIRED_COMMAND,
    QWEN_PAIRED_REPORT_SCHEMA,
    QWEN_PAIRED_VERIFICATION_SCHEMA,
    QwenPairedEvaluationError,
    _canonical_sha256,
    _quality_delta,
    load_qwen_paired_report,
    main,
    qwen_paired_protocol,
    run_qwen_paired_evaluation,
    verify_qwen_paired_report,
)
from benchmarks.qwen_phrase_eval import (
    EVALUATION_MAX_CANDIDATES,
    EVALUATION_MAX_OUTPUT_CHARS,
    EVALUATION_TIMEOUT_SECONDS,
    exact_qwen_system,
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
        lms_cli_version="lms paired test",
    )


class PairedCompletion:
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
        self.calls: list[tuple[str, str]] = []
        self.active = False
        self.maximum_active = 0

    def __call__(self, prompt: str) -> str:
        assert not self.active
        self.active = True
        self.maximum_active = max(self.maximum_active, 1)
        try:
            payload = json.loads(prompt)
            source = payload["sources"][0]
            case = self.by_source_id[source["source_id"]]
            mode = (
                "literal"
                if "source_ids" in payload["instructions"]
                else "coordinate"
            )
            self.calls.append((case.id, mode))
            if self.mode == "empty":
                items: list[dict[str, Any]] = []
            elif (
                self.mode == "bad-coordinate"
                and mode == "coordinate"
            ):
                items = [
                    {
                        "kind": atom.kind.value,
                        "text": atom.text,
                        "exact": atom.kind.value
                        in {"exact_error", "exact_reference"},
                        "provenance": [
                            {
                                "source_id": source["source_id"],
                                "start": 0,
                                "end": len(source["content"]) + 1,
                            }
                        ],
                    }
                    for atom in case.expected
                ]
            else:
                items = []
                for atom in case.expected:
                    item: dict[str, Any] = {
                        "kind": atom.kind.value,
                        "text": atom.text,
                        "exact": atom.kind.value
                        in {"exact_error", "exact_reference"},
                    }
                    if mode == "literal":
                        item["source_ids"] = [
                            source["source_id"]
                        ]
                    else:
                        item["provenance"] = [
                            {
                                "source_id": source["source_id"],
                                "start": atom.start,
                                "end": atom.end,
                            }
                        ]
                    items.append(item)
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
    return run_qwen_paired_evaluation(
        corpus,
        complete,
        system=system(),
        command=NORMALIZED_QWEN_PAIRED_COMMAND,
        repository_commit="b" * 40,
        repository_dirty=False,
    )


@pytest.fixture(scope="module")
def corpus() -> PhraseCorpus:
    return load_phrase_corpus(DEFAULT_HELDOUT_LITERAL_CORPUS)


@pytest.fixture(scope="module")
def perfect_report(corpus: PhraseCorpus) -> dict[str, Any]:
    return run_report(corpus, PairedCompletion(corpus))


def resign(report: dict[str, Any]) -> None:
    unsigned = dict(report)
    unsigned.pop("report_sha256", None)
    report["report_sha256"] = _canonical_sha256(unsigned)


def test_heldout_corpus_is_balanced_self_hashed_and_disjoint(
    corpus: PhraseCorpus,
) -> None:
    previous = load_phrase_corpus()

    assert corpus.corpus_sha256 == (
        DEFAULT_HELDOUT_LITERAL_CORPUS_SHA256
    )
    assert corpus.scope == (
        "local-heldout-paired-extractor-diagnostic"
    )
    assert corpus.authoring.draft_model == "none"
    assert corpus.authoring.network_model_api is False
    assert corpus.authoring.model_service_cost_usd == 0.0
    assert len(corpus.cases) == 64
    assert sum(bool(case.expected) for case in corpus.cases) == 40
    assert sum(not case.expected for case in corpus.cases) == 24
    assert Counter(
        atom.kind.value
        for case in corpus.cases
        for atom in case.expected
    ) == {
        "goal": 5,
        "constraint": 5,
        "unresolved": 5,
        "decision": 5,
        "confirmed_fact": 5,
        "discarded_attempt": 5,
        "exact_error": 5,
        "exact_reference": 5,
    }
    assert {case.id for case in corpus.cases}.isdisjoint(
        case.id for case in previous.cases
    )
    assert {case.content for case in corpus.cases}.isdisjoint(
        case.content for case in previous.cases
    )


def test_frozen_protocol_returns_independent_documents() -> None:
    first = qwen_paired_protocol()
    first["case_call_order"]["even_index"].append("forged")

    assert qwen_paired_protocol()["case_call_order"] == {
        "even_index": ["coordinate", "literal"],
        "odd_index": ["literal", "coordinate"],
    }


def test_perfect_paired_oracle_is_sequential_and_replays(
    corpus: PhraseCorpus,
) -> None:
    completion = PairedCompletion(corpus)

    report = run_report(corpus, completion)
    verification = verify_qwen_paired_report(report, corpus)

    assert len(completion.calls) == 128
    assert completion.maximum_active == 1
    expected_calls: list[tuple[str, str]] = []
    for index, case in enumerate(corpus.cases):
        modes = (
            ("coordinate", "literal")
            if index % 2 == 0
            else ("literal", "coordinate")
        )
        expected_calls.extend(
            (case.id, mode) for mode in modes
        )
    assert completion.calls == expected_calls
    assert report["schema"] == QWEN_PAIRED_REPORT_SCHEMA
    assert report["protocol"] == qwen_paired_protocol()
    assert report["run"]["package_version"] == __version__
    assert report["run"]["repository_dirty"] is False
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
        "lms_cli_version": "lms paired test",
    }
    for mode in ("coordinate", "literal"):
        metrics = report["metrics"][mode]
        assert metrics["model_only"]["precision"] == 1.0
        assert metrics["model_only"]["recall"] == 1.0
        assert metrics["model_only"]["case_exact_matches"] == 64
        assert metrics["candidates"][
            "reported_candidates"
        ] == 40
        assert metrics["candidates"][
            "accepted_candidates"
        ] == 40
        assert metrics["latency"]["model_calls"] == 64
    assert report["metrics"]["execution"] == {
        "expected_model_calls": 128,
        "recorded_model_calls": 128,
        "case_concurrency": 1,
        "case_call_order": {
            "even_index": ["coordinate", "literal"],
            "odd_index": ["literal", "coordinate"],
        },
        "samples_per_case_mode": 1,
        "retries_per_call": 0,
        "network_model_api": False,
        "model_service_cost_usd": 0.0,
    }

    assert report["metrics"]["comparison"][
        "literal_minus_coordinate"
    ]["model_only"] == {
        name: 0.0
        if name in {"precision", "recall", "f1"}
        else 0
        for name in (
            "predicted_atoms",
            "true_positives",
            "false_positives",
            "false_negatives",
            "precision",
            "recall",
            "f1",
            "case_exact_matches",
            "positive_case_exact_matches",
            "negative_cases_without_predictions",
        )
    }
    assert verification == {
        "schema": QWEN_PAIRED_VERIFICATION_SCHEMA,
        "verified": True,
        "corpus_sha256": corpus.corpus_sha256,
        "report_sha256": report["report_sha256"],
        "case_count": 64,
        "model_id": QWEN_Q4_VARIANT,
        "model_calls": 128,
        "case_concurrency": 1,
        "network_model_api": False,
        "model_service_cost_usd": 0.0,
    }


@pytest.mark.parametrize(
    "package_version",
    ["0.1.0", "0.1.1a1", "0.1.1a2", "0.1.1a3", "0.1.1a4", "0.1.1a5", "0.1.1a6"],
)
def test_qwen_paired_reader_accepts_only_recorded_supported_versions(
    package_version: str,
    corpus: PhraseCorpus,
    perfect_report: dict[str, Any],
) -> None:
    report = copy.deepcopy(perfect_report)
    report["run"]["package_version"] = package_version
    resign(report)

    assert verify_qwen_paired_report(report, corpus)["verified"] is True


@pytest.mark.parametrize(
    "package_version",
    [True, 1, 0.1, None, "0.1.1", "0.1.1a1 ", _StringSubclass("0.1.1a1")],
)
def test_qwen_paired_reader_rejects_other_package_versions(
    package_version: object,
    corpus: PhraseCorpus,
    perfect_report: dict[str, Any],
) -> None:
    report = copy.deepcopy(perfect_report)
    report["run"]["package_version"] = package_version
    resign(report)

    with pytest.raises(QwenPairedEvaluationError, match="package version is unsupported"):
        verify_qwen_paired_report(report, corpus)


def test_bad_coordinate_oracle_exposes_literal_delta(
    corpus: PhraseCorpus,
) -> None:
    report = run_report(
        corpus,
        PairedCompletion(corpus, mode="bad-coordinate"),
    )
    metrics = report["metrics"]

    assert metrics["coordinate"]["model_only"]["recall"] == 0.0
    assert metrics["literal"]["model_only"]["recall"] == 1.0
    delta = metrics["comparison"][
        "literal_minus_coordinate"
    ]["model_only"]
    assert delta["true_positives"] == 40
    assert delta["false_negatives"] == -40
    assert delta["recall"] == 1.0
    assert metrics["comparison"]["paired_primary_exactness"] == {
        "both_exact": 24,
        "coordinate_only_exact": 0,
        "literal_only_exact": 40,
        "neither_exact": 0,
    }
    assert verify_qwen_paired_report(report, corpus)["verified"]


def test_quality_delta_rejects_non_numeric_upstream_metrics(
    perfect_report: dict[str, Any],
) -> None:
    coordinate = copy.deepcopy(
        perfect_report["metrics"]["coordinate"]["model_only"]
    )
    literal = copy.deepcopy(
        perfect_report["metrics"]["literal"]["model_only"]
    )
    coordinate["recall"] = None

    with pytest.raises(
        QwenPairedEvaluationError,
        match="must be finite numeric",
    ):
        _quality_delta(coordinate, literal)


def test_empty_modes_expose_recovery_without_changing_call_count(
    corpus: PhraseCorpus,
) -> None:
    completion = PairedCompletion(corpus, mode="empty")
    report = run_report(corpus, completion)

    assert len(completion.calls) == 128
    for mode in ("coordinate", "literal"):
        metrics = report["metrics"][mode]
        assert metrics["model_only"]["predicted_atoms"] == 0
        assert metrics["model_only"]["recall"] == 0.0
        assert metrics["deterministic_recovery"][
            "added_atoms"
        ] > 0
        assert metrics["final_compiler"]["recall"] > 0
    assert verify_qwen_paired_report(report, corpus)["verified"]


def test_transport_failures_are_retained_for_both_modes(
    corpus: PhraseCorpus,
) -> None:
    calls = 0

    def timeout(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        raise TimeoutError("local transport")

    report = run_report(corpus, timeout)

    assert calls == 128
    for mode in ("coordinate", "literal"):
        assert report["metrics"][mode]["completion"] == {
            "successful_calls": 0,
            "failed_calls": 64,
            "error_types": {"TimeoutError": 64},
        }
    assert verify_qwen_paired_report(report, corpus)["verified"]


def test_rehashed_output_prompt_metric_and_protocol_tampering_fail(
    corpus: PhraseCorpus,
    perfect_report: dict[str, Any],
) -> None:
    output_tamper = copy.deepcopy(perfect_report)
    capture = output_tamper["cases"][0]["literal"]["capture"]
    capture["raw_output"] = '{"items":[]}'
    capture["raw_output_chars"] = len(capture["raw_output"])
    capture["raw_output_sha256"] = hashlib.sha256(
        capture["raw_output"].encode("utf-8")
    ).hexdigest()
    resign(output_tamper)
    with pytest.raises(
        QwenPairedEvaluationError,
        match="case analysis",
    ):
        verify_qwen_paired_report(output_tamper, corpus)

    prompt_tamper = copy.deepcopy(perfect_report)
    prompt_tamper["cases"][0]["coordinate"]["capture"][
        "prompt_sha256"
    ] = "0" * 64
    resign(prompt_tamper)
    with pytest.raises(ValueError, match="captured prompt mismatch"):
        verify_qwen_paired_report(prompt_tamper, corpus)

    metric_tamper = copy.deepcopy(perfect_report)
    metric_tamper["metrics"]["literal"]["model_only"][
        "recall"
    ] = 0.5
    resign(metric_tamper)
    with pytest.raises(
        QwenPairedEvaluationError,
        match="metrics do not match",
    ):
        verify_qwen_paired_report(metric_tamper, corpus)

    protocol_tamper = copy.deepcopy(perfect_report)
    protocol_tamper["protocol"]["retries_per_call"] = 1
    resign(protocol_tamper)
    with pytest.raises(
        QwenPairedEvaluationError,
        match="frozen plan",
    ):
        verify_qwen_paired_report(protocol_tamper, corpus)


def test_hash_tampering_fails_before_replay(
    corpus: PhraseCorpus,
    perfect_report: dict[str, Any],
) -> None:
    tampered = copy.deepcopy(perfect_report)
    tampered["report_sha256"] = "0" * 64

    with pytest.raises(
        QwenPairedEvaluationError,
        match="SHA-256 mismatch",
    ):
        verify_qwen_paired_report(tampered, corpus)


def test_nonfrozen_corpus_dirty_run_and_bad_command_fail_before_calls(
    corpus: PhraseCorpus,
) -> None:
    calls = 0

    def complete(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        return '{"items":[]}'

    with pytest.raises(
        QwenPairedEvaluationError,
        match="frozen held-out corpus",
    ):
        run_report(
            replace(corpus, corpus_sha256="0" * 64),
            complete,
        )
    assert calls == 0

    with pytest.raises(
        QwenPairedEvaluationError,
        match="clean repository",
    ):
        run_qwen_paired_evaluation(
            corpus,
            complete,
            system=system(),
            command=NORMALIZED_QWEN_PAIRED_COMMAND,
            repository_commit="b" * 40,
            repository_dirty=True,
        )
    assert calls == 0

    with pytest.raises(
        QwenPairedEvaluationError,
        match="normalized_command",
    ):
        run_qwen_paired_evaluation(
            corpus,
            complete,
            system=system(),
            command=["python", "-m", "wrong"],
            repository_commit="b" * 40,
            repository_dirty=False,
        )
    assert calls == 0


def test_cli_verifies_saved_report_without_lms(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    perfect_report: dict[str, Any],
) -> None:
    path = tmp_path / "paired.json"
    path.write_text(
        json.dumps(perfect_report, indent=2),
        encoding="utf-8",
    )

    loaded = load_qwen_paired_report(path)
    assert loaded["report_sha256"] == (
        perfect_report["report_sha256"]
    )
    assert main(["--verify-report", str(path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["schema"] == QWEN_PAIRED_VERIFICATION_SCHEMA
    assert output["verified"] is True
    assert output["model_calls"] == 128


def test_committed_exact_qwen_paired_report_replays_without_model(
    corpus: PhraseCorpus,
) -> None:
    report = load_qwen_paired_report(DEFAULT_QWEN_PAIRED_REPORT)
    verification = verify_qwen_paired_report(report, corpus)

    assert verification == {
        "schema": QWEN_PAIRED_VERIFICATION_SCHEMA,
        "verified": True,
        "corpus_sha256": corpus.corpus_sha256,
        "report_sha256": (
            "eb76a5accefdb50b906bb5c4658432d70958be112ef2a1febb1d71e957c9e6d2"
        ),
        "case_count": 64,
        "model_id": QWEN_Q4_VARIANT,
        "model_calls": 128,
        "case_concurrency": 1,
        "network_model_api": False,
        "model_service_cost_usd": 0.0,
    }
    assert report["run"]["repository_commit"] == (
        "b6d7095714e01c7e1d43a34ea86f8bee9235794b"
    )
    assert report["run"]["repository_dirty"] is False
    assert report["metrics"]["execution"] == {
        "expected_model_calls": 128,
        "recorded_model_calls": 128,
        "case_concurrency": 1,
        "case_call_order": {
            "even_index": ["coordinate", "literal"],
            "odd_index": ["literal", "coordinate"],
        },
        "samples_per_case_mode": 1,
        "retries_per_call": 0,
        "network_model_api": False,
        "model_service_cost_usd": 0.0,
    }

    coordinate = report["metrics"]["coordinate"]
    literal = report["metrics"]["literal"]
    assert coordinate["completion"] == {
        "successful_calls": 64,
        "failed_calls": 0,
        "error_types": {},
    }
    assert literal["completion"] == coordinate["completion"]
    assert coordinate["candidates"] == {
        "reported_candidates": 66,
        "accepted_candidates": 7,
        "rejected_candidates": 59,
        "candidate_rejection_rate": 0.893939,
        "rejection_events": 60,
        "rejection_reasons": {
            "invalid_candidate": 59,
            "invalid_json": 1,
        },
        "degraded_cases": 57,
    }
    assert literal["candidates"] == {
        "reported_candidates": 65,
        "accepted_candidates": 50,
        "rejected_candidates": 15,
        "candidate_rejection_rate": 0.230769,
        "rejection_events": 15,
        "rejection_reasons": {"invalid_candidate": 15},
        "degraded_cases": 15,
    }
    assert {
        mode: {
            metric: values["model_only"][metric]
            for metric in (
                "predicted_atoms",
                "true_positives",
                "false_positives",
                "false_negatives",
                "precision",
                "recall",
                "f1",
                "case_exact_matches",
            )
        }
        for mode, values in {
            "coordinate": coordinate,
            "literal": literal,
        }.items()
    } == {
        "coordinate": {
            "predicted_atoms": 7,
            "true_positives": 7,
            "false_positives": 0,
            "false_negatives": 33,
            "precision": 1.0,
            "recall": 0.175,
            "f1": 0.297872,
            "case_exact_matches": 31,
        },
        "literal": {
            "predicted_atoms": 50,
            "true_positives": 37,
            "false_positives": 13,
            "false_negatives": 3,
            "precision": 0.74,
            "recall": 0.925,
            "f1": 0.822222,
            "case_exact_matches": 52,
        },
    }
    assert {
        mode: {
            metric: values["final_compiler"][metric]
            for metric in (
                "true_positives",
                "false_positives",
                "false_negatives",
                "precision",
                "recall",
                "f1",
                "case_exact_matches",
                "verification_failures",
            )
        }
        for mode, values in {
            "coordinate": coordinate,
            "literal": literal,
        }.items()
    } == {
        "coordinate": {
            "true_positives": 26,
            "false_positives": 8,
            "false_negatives": 14,
            "precision": 0.764706,
            "recall": 0.65,
            "f1": 0.702703,
            "case_exact_matches": 43,
            "verification_failures": 0,
        },
        "literal": {
            "true_positives": 38,
            "false_positives": 21,
            "false_negatives": 2,
            "precision": 0.644068,
            "recall": 0.95,
            "f1": 0.767677,
            "case_exact_matches": 44,
            "verification_failures": 4,
        },
    }
    assert report["metrics"]["comparison"] == {
        "literal_minus_coordinate": {
            "model_only": {
                "predicted_atoms": 43,
                "true_positives": 30,
                "false_positives": 13,
                "false_negatives": -30,
                "precision": -0.26,
                "recall": 0.75,
                "f1": 0.52435,
                "case_exact_matches": 21,
                "positive_case_exact_matches": 30,
                "negative_cases_without_predictions": -9,
            },
            "final_compiler": {
                "predicted_atoms": 25,
                "true_positives": 12,
                "false_positives": 13,
                "false_negatives": -12,
                "precision": -0.120638,
                "recall": 0.3,
                "f1": 0.064974,
                "case_exact_matches": 1,
                "positive_case_exact_matches": 6,
                "negative_cases_without_predictions": -5,
            },
            "accepted_candidates": 43,
            "rejected_candidates": -44,
            "degraded_cases": -42,
            "verification_failures": 4,
        },
        "paired_primary_exactness": {
            "both_exact": 22,
            "coordinate_only_exact": 9,
            "literal_only_exact": 30,
            "neither_exact": 3,
        },
        "paired_final_exactness": {
            "both_exact": 37,
            "coordinate_only_exact": 6,
            "literal_only_exact": 7,
            "neither_exact": 14,
        },
    }

    invalid_json_cases = [
        case["id"]
        for case in report["cases"]
        if any(
            rejection["reason"] == "invalid_json"
            for rejection in case["coordinate"]["primary"]["rejections"]
        )
    ]
    assert invalid_json_cases == ["h-n-authority-08"]
    literal_error_issues = [
        (case["id"], issue["code"])
        for case in report["cases"]
        for issue in case["literal"]["final"]["verification_issues"]
        if issue["severity"] == "error"
    ]
    assert literal_error_issues == [
        ("h-fact-02", "fact_without_confirmation_evidence"),
        ("h-fact-03", "fact_without_confirmation_evidence"),
        ("h-fact-04", "fact_without_confirmation_evidence"),
        ("h-fact-05", "fact_without_confirmation_evidence"),
    ]


def test_cli_refuses_to_overwrite_a_live_destination(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "existing.json"
    output.write_text("preserve", encoding="utf-8")

    assert main(
        [
            "--lms",
            str(tmp_path / "missing-lms"),
            "--json-out",
            str(output),
        ]
    ) == 2
    assert output.read_text(encoding="utf-8") == "preserve"
    assert "already exists" in capsys.readouterr().err


def test_loader_rejects_duplicate_keys(
    tmp_path: Path,
    perfect_report: dict[str, Any],
) -> None:
    encoded = json.dumps(perfect_report)
    duplicate = encoded.replace(
        "{",
        (
            '{"schema":'
            f'"{QWEN_PAIRED_REPORT_SCHEMA}",'
        ),
        1,
    )
    path = tmp_path / "duplicate.json"
    path.write_text(duplicate, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate JSON object key"):
        load_qwen_paired_report(path)
