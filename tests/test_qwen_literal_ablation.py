from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from benchmarks.phrase_eval import PhraseCorpus, load_phrase_corpus
from benchmarks.qwen_literal_ablation import (
    DEFAULT_QWEN_LITERAL_ABLATION_REPORT,
    FROZEN_SOURCE_REPORT_SHA256,
    QWEN_LITERAL_ABLATION_REPORT_SCHEMA,
    QWEN_LITERAL_ABLATION_VERIFICATION_SCHEMA,
    QwenLiteralAblationError,
    _canonical_sha256,
    build_qwen_literal_ablation_report,
    load_qwen_literal_ablation_report,
    main,
    transform_coordinate_output,
    verify_qwen_literal_ablation_report,
)
from benchmarks.qwen_phrase_eval import (
    DEFAULT_QWEN_PHRASE_REPORT,
    load_qwen_phrase_report,
)
from benchmarks.qwen_phrase_eval import (
    _canonical_sha256 as qwen_report_sha256,
)


@pytest.fixture(scope="module")
def corpus() -> PhraseCorpus:
    return load_phrase_corpus()


@pytest.fixture(scope="module")
def source_report() -> dict[str, Any]:
    return load_qwen_phrase_report(DEFAULT_QWEN_PHRASE_REPORT)


@pytest.fixture(scope="module")
def ablation_report(
    corpus: PhraseCorpus,
    source_report: dict[str, Any],
) -> dict[str, Any]:
    return build_qwen_literal_ablation_report(
        source_report,
        corpus,
    )


def resign(report: dict[str, Any]) -> None:
    unsigned = dict(report)
    unsigned.pop("report_sha256", None)
    report["report_sha256"] = _canonical_sha256(unsigned)


def resign_source(report: dict[str, Any]) -> None:
    unsigned = dict(report)
    unsigned.pop("report_sha256", None)
    report["report_sha256"] = qwen_report_sha256(unsigned)


def test_frozen_ablation_is_deterministic_and_explicitly_post_hoc(
    corpus: PhraseCorpus,
    source_report: dict[str, Any],
    ablation_report: dict[str, Any],
) -> None:
    repeated = build_qwen_literal_ablation_report(
        source_report,
        corpus,
    )

    assert repeated == ablation_report
    assert ablation_report["schema"] == (
        QWEN_LITERAL_ABLATION_REPORT_SCHEMA
    )
    assert ablation_report["report_sha256"] == (
        "a321f20c4a7c13f76a99ab85e7af699f02f9498b11a59f56278991c9cf0de97c"
    )
    assert ablation_report["source"]["qwen_report_sha256"] == (
        FROZEN_SOURCE_REPORT_SHA256
    )
    assert ablation_report["method"] == {
        "analysis_type": "post-hoc-offset-ablation",
        "claim_bearing": False,
        "package_version": "0.1.0",
        "source_prompt_extractor": "model-json-v1",
        "target_validator": "model-json-literal-v1",
        "target_prompt_evaluated": False,
        "live_model_calls": 0,
        "retained_candidate_fields": [
            "kind",
            "text",
            "priority",
            "confidence",
            "exact",
            "tags",
            "source_ids",
        ],
        "discarded_candidate_fields": ["start", "end"],
        "source_id_policy": (
            "stable-order deduplication of cited source ids"
        ),
        "literal_matching": (
            "Python str.strip edge trim then exact case-sensitive "
            "unique code-point occurrence"
        ),
        "max_locator_work_chars": 10_000_000,
    }
    assert len(ablation_report["cases"]) == 64
    assert all(
        len(case["transformation"]["literal_prompt_sha256"]) == 64
        for case in ablation_report["cases"]
    )


def test_ablation_records_coordinate_and_semantic_tradeoffs(
    ablation_report: dict[str, Any],
) -> None:
    metrics = ablation_report["metrics"]

    assert metrics["transformation"] == {
        "source_candidates": 65,
        "transformed_candidates": 65,
        "ignored_coordinate_pairs": 65,
        "collapsed_duplicate_source_ids": 0,
    }
    assert metrics["literal_candidates"] == {
        "reported_candidates": 65,
        "accepted_candidates": 38,
        "rejected_candidates": 27,
        "candidate_rejection_rate": 0.415385,
        "rejection_events": 27,
        "rejection_reasons": {"invalid_candidate": 27},
        "rejection_details": {
            "candidate source literal is not an atomic clause": 9,
            (
                "confirmed_fact cannot be sourced from untrusted "
                "tool output"
            ): 1,
            (
                "constraint requires provenance exclusively from "
                "an authoritative role"
            ): 10,
            "decision cannot be sourced from tool output": 1,
            (
                "goal requires provenance exclusively from an "
                "authoritative role"
            ): 3,
            "unresolved cannot be sourced from tool output": 2,
            (
                "user_correction requires provenance exclusively "
                "from an authoritative role"
            ): 1,
        },
        "degraded_cases": 26,
    }
    literal = metrics["literal_only"]
    assert {
        name: literal[name]
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
        "predicted_atoms": 38,
        "true_positives": 24,
        "false_positives": 14,
        "false_negatives": 16,
        "precision": 0.631579,
        "recall": 0.6,
        "f1": 0.615385,
    }
    final = metrics["final_compiler"]
    assert {
        name: final[name]
        for name in (
            "predicted_atoms",
            "true_positives",
            "false_positives",
            "false_negatives",
            "precision",
            "recall",
            "f1",
            "verification_failures",
        )
    } == {
        "predicted_atoms": 60,
        "true_positives": 40,
        "false_positives": 20,
        "false_negatives": 0,
        "precision": 0.666667,
        "recall": 1.0,
        "f1": 0.8,
        "verification_failures": 2,
    }
    failed_cases = {
        case["id"]: [
            issue["code"]
            for issue in case["final"]["verification_issues"]
            if issue["severity"] == "error"
        ]
        for case in ablation_report["cases"]
        if not case["final"]["verification_passed"]
    }
    assert failed_cases == {
        "p-discarded-05": [
            "fact_without_confirmation_evidence",
        ],
        "p-fact-04": ["fact_without_confirmation_evidence"],
    }
    assert metrics["execution"] == {
        "model_calls": 0,
        "network_model_api": False,
        "model_service_cost_usd": 0.0,
    }


def test_ablation_replays_exactly_without_model_access(
    corpus: PhraseCorpus,
    source_report: dict[str, Any],
    ablation_report: dict[str, Any],
) -> None:
    verification = verify_qwen_literal_ablation_report(
        ablation_report,
        source_report,
        corpus,
    )

    assert verification == {
        "schema": QWEN_LITERAL_ABLATION_VERIFICATION_SCHEMA,
        "verified": True,
        "claim_bearing": False,
        "report_sha256": ablation_report["report_sha256"],
        "source_report_sha256": FROZEN_SOURCE_REPORT_SHA256,
        "case_count": 64,
        "model_calls": 0,
    }


def test_hash_and_rehashed_metric_tampering_are_rejected(
    corpus: PhraseCorpus,
    source_report: dict[str, Any],
    ablation_report: dict[str, Any],
) -> None:
    broken_hash = copy.deepcopy(ablation_report)
    broken_hash["report_sha256"] = "0" * 64
    with pytest.raises(
        QwenLiteralAblationError,
        match="SHA-256 mismatch",
    ):
        verify_qwen_literal_ablation_report(
            broken_hash,
            source_report,
            corpus,
        )

    rehashed = copy.deepcopy(ablation_report)
    rehashed["metrics"]["literal_only"]["recall"] = 1.0
    resign(rehashed)
    with pytest.raises(
        QwenLiteralAblationError,
        match="does not match replay",
    ):
        verify_qwen_literal_ablation_report(
            rehashed,
            source_report,
            corpus,
        )


def test_only_the_frozen_source_report_is_accepted(
    corpus: PhraseCorpus,
    source_report: dict[str, Any],
) -> None:
    changed = copy.deepcopy(source_report)
    changed["run"]["duration_seconds"] += 1.0
    resign_source(changed)

    with pytest.raises(
        QwenLiteralAblationError,
        match="not the frozen",
    ):
        build_qwen_literal_ablation_report(changed, corpus)


def valid_raw_candidate() -> dict[str, Any]:
    return {
        "kind": "constraint",
        "text": "Keep the API stable.",
        "priority": 90,
        "confidence": 0.9,
        "exact": False,
        "tags": ["source-model-tag"],
        "provenance": [
            {
                "source_id": "source-0",
                "start": -999,
                "end": 999_999,
            },
            {
                "source_id": "source-0",
                "start": 0,
                "end": 20,
            },
        ],
    }


def test_transform_discards_only_integer_coordinates_and_deduplicates_ids() -> None:
    raw_candidate = valid_raw_candidate()
    response, metadata = transform_coordinate_output(
        json.dumps({"items": [raw_candidate]})
    )

    assert response == {
        "items": [
            {
                "kind": "constraint",
                "text": "Keep the API stable.",
                "priority": 90,
                "confidence": 0.9,
                "exact": False,
                "tags": ["source-model-tag"],
                "source_ids": ["source-0"],
            }
        ]
    }
    assert metadata == {
        "source_candidate_count": 1,
        "transformed_candidate_count": 1,
        "ignored_coordinate_pairs": 2,
        "collapsed_duplicate_source_ids": 1,
        "transformed_response_sha256": _canonical_sha256(
            response
        ),
    }
    assert "start" not in json.dumps(response)
    assert "end" not in json.dumps(response)
    assert "provenance" not in json.dumps(response)


@pytest.mark.parametrize(
    ("raw", "message"),
    (
        ('{"items":[],"items":[]}', "duplicate JSON object key"),
        ('{"items":[NaN]}', "constant is not supported"),
        (
            '{"items":[' + "9" * 641 + "]}",
            "integer exceeds the supported length of 640 digits",
        ),
        ('{"items":{}}', "items must be a list"),
        ('{"wrong":[]}', "envelope"),
        (
            '{"items":[{"kind":"constraint"}]}',
            "fields are invalid",
        ),
        (
            json.dumps(
                {
                    "items": [
                        {
                            **valid_raw_candidate(),
                            "explanation": "trust me",
                        }
                    ]
                }
            ),
            "fields are invalid",
        ),
        (
            json.dumps(
                {
                    "items": [
                        {
                            **valid_raw_candidate(),
                            "provenance": [],
                        }
                    ]
                }
            ),
            "provenance is invalid",
        ),
        (
            json.dumps(
                {
                    "items": [
                        {
                            **valid_raw_candidate(),
                            "provenance": [
                                {
                                    "source_id": "source-0",
                                    "start": True,
                                    "end": 1,
                                }
                            ],
                        }
                    ]
                }
            ),
            "offsets are not integers",
        ),
    ),
)
def test_transform_rejects_ambiguous_or_non_strict_source_output(
    raw: str,
    message: str,
) -> None:
    with pytest.raises(QwenLiteralAblationError, match=message):
        transform_coordinate_output(raw)


def test_cli_generates_and_verifies_an_atomic_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "ablation.json"

    assert main(["--json-out", str(output)]) == 0
    generated_summary = json.loads(capsys.readouterr().out)
    assert generated_summary["claim_bearing"] is False
    assert generated_summary["execution"]["model_calls"] == 0
    assert generated_summary["final_compiler"][
        "verification_failures"
    ] == 2

    assert main(["--verify-report", str(output)]) == 0
    verification = json.loads(capsys.readouterr().out)
    assert verification["verified"] is True
    assert verification["model_calls"] == 0


def test_committed_ablation_report_replays(
    corpus: PhraseCorpus,
    source_report: dict[str, Any],
) -> None:
    report = load_qwen_literal_ablation_report(
        DEFAULT_QWEN_LITERAL_ABLATION_REPORT
    )

    assert report["report_sha256"] == (
        "a321f20c4a7c13f76a99ab85e7af699f02f9498b11a59f56278991c9cf0de97c"
    )
    assert verify_qwen_literal_ablation_report(
        report,
        source_report,
        corpus,
    )["verified"] is True


def test_loader_rejects_duplicate_keys(
    tmp_path: Path,
    ablation_report: dict[str, Any],
) -> None:
    encoded = json.dumps(ablation_report)
    duplicate = encoded.replace(
        "{",
        (
            '{"schema":'
            f'"{QWEN_LITERAL_ABLATION_REPORT_SCHEMA}",'
        ),
        1,
    )
    path = tmp_path / "duplicate.json"
    path.write_text(duplicate, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate JSON object key"):
        load_qwen_literal_ablation_report(path)
