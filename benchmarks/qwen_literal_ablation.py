"""Deterministic post-hoc ablation of model-generated provenance offsets.

This analyzer never calls a model. It verifies the frozen exact-Qwen
captured-output report, retains each decoded candidate's text, kind, optional
fields, and cited source ids, discards only start/end values, and replays the
transformed response through LiteralModelExtractor and the full compiler.

The result is explicitly post-hoc because the source corpus and model outputs
were observed before LiteralModelExtractor was designed.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from context_compiler import (
    CompilationPolicy,
    ContextCompiler,
    LiteralModelExtractor,
    SourceRecord,
    __version__,
)
from context_compiler.atomic import atomic_write_text
from context_compiler.extractors import ExtractionResult
from context_compiler.local_qwen import QWEN_Q4_VARIANT

from .json_io import StrictJsonLimits, load_strict_json_file
from .phrase_eval import (
    DEFAULT_PHRASE_CORPUS,
    PhraseCase,
    PhraseCorpus,
    _AtomKey,
    _expanded,
    _expected_counter,
    _predicted_counter,
    load_phrase_corpus,
)
from .qwen_phrase_eval import (
    DEFAULT_QWEN_PHRASE_REPORT,
    EVALUATION_MAX_CANDIDATES,
    EVALUATION_MAX_OUTPUT_CHARS,
    QWEN_PHRASE_REPORT_SCHEMA,
    _quality_metrics,
    load_qwen_phrase_report,
    verify_qwen_phrase_report,
)

QWEN_LITERAL_ABLATION_REPORT_SCHEMA = (
    "ctxc-qwen-literal-offset-ablation-report-0.1"
)
QWEN_LITERAL_ABLATION_VERIFICATION_SCHEMA = (
    "ctxc-qwen-literal-offset-ablation-verification-0.1"
)
FROZEN_SOURCE_REPORT_SHA256 = (
    "db2e054537e366056a8fd482f1a8e17b8fa0d02163a08ea79ef3c682af79c171"
)
DEFAULT_QWEN_LITERAL_ABLATION_REPORT = (
    Path(__file__).parents[1]
    / "docs"
    / "results"
    / "qwen-literal-offset-ablation-v1.json"
)
ABLATION_MAX_LOCATOR_WORK_CHARS = 10_000_000
_MAX_CAPTURED_JSON_INTEGER_DIGITS = 640

_REPORT_LIMITS = StrictJsonLimits(
    max_bytes=16 * 1024 * 1024,
    max_line_chars=512 * 1024,
    max_depth=32,
)
_POLICY = CompilationPolicy(
    token_budget=100_000,
    minimum_compression_ratio=1.0,
)
_ORIGINAL_CANDIDATE_KEYS = frozenset(
    {
        "kind",
        "text",
        "priority",
        "confidence",
        "exact",
        "tags",
        "provenance",
    }
)
_ORIGINAL_REQUIRED_KEYS = frozenset({"kind", "text", "provenance"})
_ORIGINAL_PROVENANCE_KEYS = frozenset({"source_id", "start", "end"})
_REPORT_FIELDS = frozenset(
    {
        "schema",
        "source",
        "method",
        "metrics",
        "cases",
        "report_sha256",
    }
)


class QwenLiteralAblationError(ValueError):
    """The frozen offset-ablation input or evidence is invalid."""


def _canonical_sha256(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError) as exc:
        raise QwenLiteralAblationError(
            "literal ablation evidence is not canonical finite JSON"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _strict_object(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise QwenLiteralAblationError(
                f"duplicate JSON object key: {key}"
            )
        result[key] = value
    return result


def _finite_float(value: str) -> float:
    decoded = float(value)
    if not math.isfinite(decoded):
        raise QwenLiteralAblationError(
            "captured JSON number must be finite"
        )
    return decoded


def _bounded_int(value: str) -> int:
    digits = value[1:] if value.startswith("-") else value
    if len(digits) > _MAX_CAPTURED_JSON_INTEGER_DIGITS:
        raise QwenLiteralAblationError(
            "captured JSON integer exceeds the supported length of "
            f"{_MAX_CAPTURED_JSON_INTEGER_DIGITS} digits"
        )
    return int(value)


def _reject_constant(value: str) -> None:
    raise QwenLiteralAblationError(
        f"captured JSON constant is not supported: {value}"
    )


def _exact_fields(
    value: Any,
    *,
    fields: frozenset[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise QwenLiteralAblationError(f"{label} must be an object")
    actual = set(value)
    if actual != fields:
        missing = sorted(fields - actual)
        unknown = sorted(actual - fields)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        raise QwenLiteralAblationError(
            f"{label} fields are invalid: {'; '.join(details)}"
        )
    return value


def _decode_source_output(raw_output: str) -> list[Any]:
    if not isinstance(raw_output, str):
        raise TypeError("captured raw output must be a string")
    if len(raw_output) > EVALUATION_MAX_OUTPUT_CHARS:
        raise QwenLiteralAblationError(
            "captured raw output exceeds the frozen character limit"
        )
    try:
        decoded = json.loads(
            raw_output,
            object_pairs_hook=_strict_object,
            parse_float=_finite_float,
            parse_int=_bounded_int,
            parse_constant=_reject_constant,
        )
    except (RecursionError, TypeError, ValueError) as exc:
        if isinstance(exc, QwenLiteralAblationError):
            raise
        raise QwenLiteralAblationError(
            "captured raw output is not strict JSON"
        ) from exc
    envelope = _exact_fields(
        decoded,
        fields=frozenset({"items"}),
        label="captured model envelope",
    )
    items = envelope["items"]
    if not isinstance(items, list):
        raise QwenLiteralAblationError(
            "captured model items must be a list"
        )
    if len(items) > EVALUATION_MAX_CANDIDATES:
        raise QwenLiteralAblationError(
            "captured model items exceed the frozen candidate limit"
        )
    return items


def transform_coordinate_output(
    raw_output: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Discard only coordinate values from a strict captured response."""

    transformed: list[dict[str, Any]] = []
    ignored_coordinate_pairs = 0
    collapsed_duplicate_source_ids = 0
    for index, candidate_value in enumerate(
        _decode_source_output(raw_output)
    ):
        if not isinstance(candidate_value, dict):
            raise QwenLiteralAblationError(
                f"captured candidate {index} must be an object"
            )
        candidate_keys = set(candidate_value)
        missing = sorted(_ORIGINAL_REQUIRED_KEYS - candidate_keys)
        unknown = sorted(candidate_keys - _ORIGINAL_CANDIDATE_KEYS)
        if missing or unknown:
            details: list[str] = []
            if missing:
                details.append("missing " + ", ".join(missing))
            if unknown:
                details.append("unknown " + ", ".join(unknown))
            raise QwenLiteralAblationError(
                f"captured candidate {index} fields are invalid: "
                + "; ".join(details)
            )
        raw_provenance = candidate_value["provenance"]
        if not isinstance(raw_provenance, list) or not raw_provenance:
            raise QwenLiteralAblationError(
                f"captured candidate {index} provenance is invalid"
            )
        source_ids: list[str] = []
        seen_source_ids: set[str] = set()
        for span_index, raw_span in enumerate(raw_provenance):
            span = _exact_fields(
                raw_span,
                fields=_ORIGINAL_PROVENANCE_KEYS,
                label=(
                    f"captured candidate {index} provenance "
                    f"{span_index}"
                ),
            )
            source_id = span["source_id"]
            start = span["start"]
            end = span["end"]
            if not isinstance(source_id, str) or not source_id:
                raise QwenLiteralAblationError(
                    f"captured candidate {index} source_id is invalid"
                )
            if (
                isinstance(start, bool)
                or not isinstance(start, int)
                or isinstance(end, bool)
                or not isinstance(end, int)
            ):
                raise QwenLiteralAblationError(
                    f"captured candidate {index} offsets are not integers"
                )
            ignored_coordinate_pairs += 1
            if source_id in seen_source_ids:
                collapsed_duplicate_source_ids += 1
                continue
            seen_source_ids.add(source_id)
            source_ids.append(source_id)
        literal_candidate = {
            key: copy.deepcopy(value)
            for key, value in candidate_value.items()
            if key != "provenance"
        }
        literal_candidate["source_ids"] = source_ids
        transformed.append(literal_candidate)
    response = {"items": transformed}
    metadata = {
        "source_candidate_count": len(transformed),
        "transformed_candidate_count": len(transformed),
        "ignored_coordinate_pairs": ignored_coordinate_pairs,
        "collapsed_duplicate_source_ids": (
            collapsed_duplicate_source_ids
        ),
        "transformed_response_sha256": _canonical_sha256(response),
    }
    return response, metadata


def _source_for_case(case: PhraseCase) -> SourceRecord:
    return SourceRecord.create(
        id=f"phrase:{case.id}",
        sequence=0,
        role=case.role,
        content=case.content,
    )


def _literal_extractor(
    complete: Any,
) -> LiteralModelExtractor:
    return LiteralModelExtractor(
        complete,
        model_id=QWEN_Q4_VARIANT,
        max_response_chars=EVALUATION_MAX_OUTPUT_CHARS,
        max_candidates=EVALUATION_MAX_CANDIDATES,
        max_locator_work_chars=ABLATION_MAX_LOCATOR_WORK_CHARS,
    )


def _counter_for_items(items: Sequence[Any]) -> Counter[_AtomKey]:
    return _predicted_counter(SimpleNamespace(items=items))


def _case_score(
    expected: Counter[_AtomKey],
    predicted: Counter[_AtomKey],
) -> tuple[
    Counter[_AtomKey],
    Counter[_AtomKey],
    Counter[_AtomKey],
]:
    matched = expected & predicted
    return matched, predicted - expected, expected - predicted


def _extract_literal_response(
    source: SourceRecord,
    response: dict[str, Any],
) -> tuple[ExtractionResult, str]:
    prompts: list[str] = []

    def complete(prompt: str) -> dict[str, Any]:
        prompts.append(prompt)
        return copy.deepcopy(response)

    result = _literal_extractor(complete).extract([source])
    if len(prompts) != 1:
        raise QwenLiteralAblationError(
            "literal extractor did not produce exactly one prompt"
        )
    return result, hashlib.sha256(
        prompts[0].encode("utf-8")
    ).hexdigest()


def _compile_literal_response(
    source: SourceRecord,
    response: dict[str, Any],
) -> tuple[Any, str]:
    prompts: list[str] = []

    def complete(prompt: str) -> dict[str, Any]:
        prompts.append(prompt)
        return copy.deepcopy(response)

    memory = ContextCompiler(
        extractor=_literal_extractor(complete),
        policy=_POLICY,
    ).compile([source])
    if len(prompts) != 1:
        raise QwenLiteralAblationError(
            "literal compiler did not produce exactly one prompt"
        )
    return memory, hashlib.sha256(
        prompts[0].encode("utf-8")
    ).hexdigest()


def _analyze_case(
    case: PhraseCase,
    source_case: Mapping[str, Any],
) -> tuple[
    dict[str, Any],
    Counter[_AtomKey],
    Counter[_AtomKey],
    Counter[_AtomKey],
]:
    if source_case.get("id") != case.id:
        raise QwenLiteralAblationError(
            "source report cases are not in frozen corpus order"
        )
    capture = source_case.get("capture")
    if (
        not isinstance(capture, Mapping)
        or capture.get("status") != "ok"
        or not isinstance(capture.get("raw_output"), str)
        or not isinstance(capture.get("raw_output_sha256"), str)
    ):
        raise QwenLiteralAblationError(
            f"source capture for {case.id!r} is not successful"
        )
    raw_output = capture["raw_output"]
    source_output_sha256 = hashlib.sha256(
        raw_output.encode("utf-8")
    ).hexdigest()
    if source_output_sha256 != capture["raw_output_sha256"]:
        raise QwenLiteralAblationError(
            f"source output digest mismatch for {case.id!r}"
        )
    response, transformation = transform_coordinate_output(
        raw_output
    )
    source = _source_for_case(case)
    expected = _expected_counter(case, source_id=source.id)
    primary, prompt_sha256 = _extract_literal_response(
        source,
        response,
    )
    primary_predicted = _counter_for_items(primary.items)
    primary_matched, primary_fp, primary_fn = _case_score(
        expected,
        primary_predicted,
    )
    memory, compiler_prompt_sha256 = _compile_literal_response(
        source,
        response,
    )
    if compiler_prompt_sha256 != prompt_sha256:
        raise QwenLiteralAblationError(
            "literal extraction prompt is not deterministic"
        )
    final_predicted = _predicted_counter(memory)
    final_matched, final_fp, final_fn = _case_score(
        expected,
        final_predicted,
    )
    recovered_items = [
        item
        for item in memory.items
        if "verifier-recovered" in item.tags
    ]
    recovered = _counter_for_items(recovered_items)
    recovered_matched = expected & recovered
    expected_after_model_miss = (
        expected - primary_predicted
    ) & final_predicted
    candidate_count = primary.metadata.get("candidates")
    if (
        isinstance(candidate_count, bool)
        or not isinstance(candidate_count, int)
        or candidate_count != transformation[
            "transformed_candidate_count"
        ]
    ):
        raise QwenLiteralAblationError(
            "literal extractor candidate count is inconsistent"
        )
    candidate_rejection_count = (
        candidate_count - len(primary.items)
    )
    if candidate_rejection_count < 0:
        raise QwenLiteralAblationError(
            "literal accepted count exceeds transformed candidates"
        )
    primary_degradation = memory.compiler_metadata.get(
        "primary_degradation"
    )
    report = {
        "id": case.id,
        "role": case.role,
        "source_capture": {
            "raw_output_sha256": source_output_sha256,
            "raw_output_chars": len(raw_output),
        },
        "transformation": {
            **transformation,
            "literal_prompt_sha256": prompt_sha256,
        },
        "expected": _expanded(expected),
        "primary": {
            "candidate_count": candidate_count,
            "accepted_count": len(primary.items),
            "candidate_rejection_count": (
                candidate_rejection_count
            ),
            "rejection_event_count": len(primary.rejected),
            "rejections": primary.rejected,
            "degraded": primary.metadata.get("degraded") is True,
            "failure_reason": primary.metadata.get(
                "failure_reason"
            ),
            "compiler_degradation": (
                dict(primary_degradation)
                if isinstance(primary_degradation, Mapping)
                else None
            ),
            "predicted": _expanded(primary_predicted),
            "true_positive_count": sum(primary_matched.values()),
            "false_positive_count": sum(primary_fp.values()),
            "false_negative_count": sum(primary_fn.values()),
            "false_positives": _expanded(primary_fp),
            "false_negatives": _expanded(primary_fn),
            "exact_match": not primary_fp and not primary_fn,
        },
        "recovery": {
            "added_count": sum(recovered.values()),
            "added": _expanded(recovered),
            "true_positive_count": sum(
                recovered_matched.values()
            ),
            "expected_after_primary_miss_count": sum(
                expected_after_model_miss.values()
            ),
            "expected_after_primary_miss": _expanded(
                expected_after_model_miss
            ),
        },
        "final": {
            "verification_passed": memory.verification.passed,
            "verification_issues": [
                {
                    "code": issue.code,
                    "severity": issue.severity.value,
                    "item_id": issue.item_id,
                    "source_id": issue.source_id,
                }
                for issue in memory.verification.issues
            ],
            "predicted": _expanded(final_predicted),
            "true_positive_count": sum(final_matched.values()),
            "false_positive_count": sum(final_fp.values()),
            "false_negative_count": sum(final_fn.values()),
            "false_positives": _expanded(final_fp),
            "false_negatives": _expanded(final_fn),
            "exact_match": (
                not final_fp
                and not final_fn
                and memory.verification.passed
            ),
        },
    }
    return report, expected, primary_predicted, final_predicted


def _selected_quality(
    metrics: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        name: metrics[name]
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


def build_qwen_literal_ablation_report(
    source_report: Any,
    corpus: PhraseCorpus,
) -> dict[str, Any]:
    """Build deterministic post-hoc evidence without any model call."""

    if not isinstance(source_report, dict):
        raise TypeError("source report must be an object")
    if not isinstance(corpus, PhraseCorpus):
        raise TypeError("corpus must be a PhraseCorpus value")
    source_verification = verify_qwen_phrase_report(
        source_report,
        corpus,
    )
    if source_verification["report_sha256"] != (
        FROZEN_SOURCE_REPORT_SHA256
    ):
        raise QwenLiteralAblationError(
            "source report is not the frozen exact-Qwen report"
        )
    raw_cases = source_report["cases"]
    if (
        not isinstance(raw_cases, list)
        or len(raw_cases) != len(corpus.cases)
    ):
        raise QwenLiteralAblationError(
            "source report case count is invalid"
        )
    case_reports: list[dict[str, Any]] = []
    total_expected: Counter[_AtomKey] = Counter()
    total_primary: Counter[_AtomKey] = Counter()
    total_final: Counter[_AtomKey] = Counter()
    for case, source_case in zip(
        corpus.cases,
        raw_cases,
        strict=True,
    ):
        report, expected, primary, final = _analyze_case(
            case,
            source_case,
        )
        case_reports.append(report)
        total_expected.update(expected)
        total_primary.update(primary)
        total_final.update(final)
    primary_quality = _quality_metrics(
        total_expected,
        total_primary,
        case_reports=case_reports,
        section="primary",
    )
    final_quality = _quality_metrics(
        total_expected,
        total_final,
        case_reports=case_reports,
        section="final",
    )
    source_candidates = sum(
        case["transformation"]["source_candidate_count"]
        for case in case_reports
    )
    transformed_candidates = sum(
        case["transformation"]["transformed_candidate_count"]
        for case in case_reports
    )
    accepted_candidates = sum(
        case["primary"]["accepted_count"]
        for case in case_reports
    )
    rejected_candidates = sum(
        case["primary"]["candidate_rejection_count"]
        for case in case_reports
    )
    rejection_reasons = Counter(
        rejection["reason"]
        for case in case_reports
        for rejection in case["primary"]["rejections"]
    )
    rejection_details = Counter(
        rejection.get("detail", "")
        for case in case_reports
        for rejection in case["primary"]["rejections"]
    )
    coordinate_quality = source_report["metrics"]["model_only"]
    coordinate_candidates = source_report["metrics"]["candidates"]
    metrics = {
        "transformation": {
            "source_candidates": source_candidates,
            "transformed_candidates": transformed_candidates,
            "ignored_coordinate_pairs": sum(
                case["transformation"][
                    "ignored_coordinate_pairs"
                ]
                for case in case_reports
            ),
            "collapsed_duplicate_source_ids": sum(
                case["transformation"][
                    "collapsed_duplicate_source_ids"
                ]
                for case in case_reports
            ),
        },
        "literal_candidates": {
            "reported_candidates": transformed_candidates,
            "accepted_candidates": accepted_candidates,
            "rejected_candidates": rejected_candidates,
            "candidate_rejection_rate": (
                round(
                    rejected_candidates / transformed_candidates,
                    6,
                )
                if transformed_candidates
                else None
            ),
            "rejection_events": sum(
                case["primary"]["rejection_event_count"]
                for case in case_reports
            ),
            "rejection_reasons": {
                reason: rejection_reasons[reason]
                for reason in sorted(rejection_reasons)
            },
            "rejection_details": {
                detail: rejection_details[detail]
                for detail in sorted(rejection_details)
            },
            "degraded_cases": sum(
                case["primary"]["degraded"]
                for case in case_reports
            ),
        },
        "literal_only": primary_quality,
        "deterministic_recovery": {
            "added_atoms": sum(
                case["recovery"]["added_count"]
                for case in case_reports
            ),
            "true_positive_atoms": sum(
                case["recovery"]["true_positive_count"]
                for case in case_reports
            ),
            "expected_atoms_added_after_primary_miss": sum(
                case["recovery"][
                    "expected_after_primary_miss_count"
                ]
                for case in case_reports
            ),
            "cases_with_added_atoms": sum(
                case["recovery"]["added_count"] > 0
                for case in case_reports
            ),
            "cases_with_true_positive_atoms": sum(
                case["recovery"]["true_positive_count"] > 0
                for case in case_reports
            ),
            "recall_gain": round(
                final_quality["recall"]
                - primary_quality["recall"],
                6,
            ),
        },
        "final_compiler": final_quality,
        "coordinate_baseline": {
            "candidates": {
                name: coordinate_candidates[name]
                for name in (
                    "reported_candidates",
                    "accepted_candidates",
                    "rejected_candidates",
                    "candidate_rejection_rate",
                    "degraded_cases",
                )
            },
            "model_only": _selected_quality(
                coordinate_quality
            ),
        },
        "delta_from_coordinate_baseline": {
            "accepted_candidates": (
                accepted_candidates
                - coordinate_candidates["accepted_candidates"]
            ),
            "true_positives": (
                primary_quality["true_positives"]
                - coordinate_quality["true_positives"]
            ),
            "false_positives": (
                primary_quality["false_positives"]
                - coordinate_quality["false_positives"]
            ),
            "false_negatives": (
                primary_quality["false_negatives"]
                - coordinate_quality["false_negatives"]
            ),
            "precision": round(
                primary_quality["precision"]
                - coordinate_quality["precision"],
                6,
            ),
            "recall": round(
                primary_quality["recall"]
                - coordinate_quality["recall"],
                6,
            ),
            "f1": round(
                primary_quality["f1"]
                - coordinate_quality["f1"],
                6,
            ),
        },
        "execution": {
            "model_calls": 0,
            "network_model_api": False,
            "model_service_cost_usd": 0.0,
        },
    }
    report: dict[str, Any] = {
        "schema": QWEN_LITERAL_ABLATION_REPORT_SCHEMA,
        "source": {
            "corpus_sha256": corpus.corpus_sha256,
            "case_count": len(corpus.cases),
            "qwen_report_schema": QWEN_PHRASE_REPORT_SCHEMA,
            "qwen_report_sha256": source_report[
                "report_sha256"
            ],
            "qwen_repository_commit": source_report["run"][
                "repository_commit"
            ],
            "model_id": source_report["system"]["model_id"],
        },
        "method": {
            "analysis_type": "post-hoc-offset-ablation",
            "claim_bearing": False,
            "package_version": __version__,
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
                "Python str.strip edge trim then exact "
                "case-sensitive unique code-point occurrence"
            ),
            "max_locator_work_chars": (
                ABLATION_MAX_LOCATOR_WORK_CHARS
            ),
        },
        "metrics": metrics,
        "cases": case_reports,
    }
    report["report_sha256"] = _canonical_sha256(report)
    return report


def verify_qwen_literal_ablation_report(
    document: Any,
    source_report: Any,
    corpus: PhraseCorpus,
) -> dict[str, Any]:
    """Self-hash and deterministically regenerate an ablation report."""

    report = _exact_fields(
        document,
        fields=_REPORT_FIELDS,
        label="Qwen literal ablation report",
    )
    if report["schema"] != QWEN_LITERAL_ABLATION_REPORT_SCHEMA:
        raise QwenLiteralAblationError(
            "Qwen literal ablation report schema is unsupported"
        )
    report_sha256 = report["report_sha256"]
    if (
        not isinstance(report_sha256, str)
        or len(report_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in report_sha256
        )
    ):
        raise QwenLiteralAblationError(
            "Qwen literal ablation report hash is invalid"
        )
    unsigned = dict(report)
    unsigned.pop("report_sha256")
    if _canonical_sha256(unsigned) != report_sha256:
        raise QwenLiteralAblationError(
            "Qwen literal ablation report SHA-256 mismatch"
        )
    regenerated = build_qwen_literal_ablation_report(
        source_report,
        corpus,
    )
    if report != regenerated:
        raise QwenLiteralAblationError(
            "Qwen literal ablation report does not match replay"
        )
    return {
        "schema": QWEN_LITERAL_ABLATION_VERIFICATION_SCHEMA,
        "verified": True,
        "claim_bearing": False,
        "report_sha256": report_sha256,
        "source_report_sha256": FROZEN_SOURCE_REPORT_SHA256,
        "case_count": len(corpus.cases),
        "model_calls": 0,
    }


def load_qwen_literal_ablation_report(
    path: str | Path,
) -> dict[str, Any]:
    loaded = load_strict_json_file(
        path,
        limits=_REPORT_LIMITS,
        label="Qwen literal ablation report",
    )
    if not isinstance(loaded.value, dict):
        raise QwenLiteralAblationError(
            "Qwen literal ablation report must be an object"
        )
    return loaded.value


def _summary(report: Mapping[str, Any]) -> dict[str, Any]:
    metrics = report["metrics"]
    return {
        "schema": report["schema"],
        "report_sha256": report["report_sha256"],
        "source_report_sha256": report["source"][
            "qwen_report_sha256"
        ],
        "case_count": report["source"]["case_count"],
        "claim_bearing": False,
        "literal_candidates": metrics["literal_candidates"],
        "literal_only": _selected_quality(
            metrics["literal_only"]
        ),
        "deterministic_recovery": metrics[
            "deterministic_recovery"
        ],
        "final_compiler": {
            **_selected_quality(metrics["final_compiler"]),
            "verification_failures": metrics["final_compiler"][
                "verification_failures"
            ],
        },
        "delta_from_coordinate_baseline": metrics[
            "delta_from_coordinate_baseline"
        ],
        "execution": metrics["execution"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_PHRASE_CORPUS,
        help="frozen phrase corpus",
    )
    parser.add_argument(
        "--source-report",
        type=Path,
        default=DEFAULT_QWEN_PHRASE_REPORT,
        help="frozen exact-Qwen captured-output report",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        help="optional atomic destination for a generated report",
    )
    parser.add_argument(
        "--verify-report",
        type=Path,
        help="strictly regenerate and verify a saved ablation report",
    )
    args = parser.parse_args(argv)
    if args.verify_report is not None and args.json_out is not None:
        parser.error(
            "--verify-report cannot be combined with --json-out"
        )
    try:
        corpus = load_phrase_corpus(args.corpus)
        source_report = load_qwen_phrase_report(
            args.source_report
        )
        if args.verify_report is not None:
            verification = verify_qwen_literal_ablation_report(
                load_qwen_literal_ablation_report(
                    args.verify_report
                ),
                source_report,
                corpus,
            )
            sys.stdout.write(
                json.dumps(
                    verification,
                    indent=2,
                    ensure_ascii=True,
                )
                + "\n"
            )
            return 0
        report = build_qwen_literal_ablation_report(
            source_report,
            corpus,
        )
        if args.json_out is not None:
            rendered = json.dumps(
                report,
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
            )
            atomic_write_text(args.json_out, rendered + "\n")
        sys.stdout.write(
            json.dumps(
                _summary(report),
                indent=2,
                ensure_ascii=True,
            )
            + "\n"
        )
        return 0
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        sys.stderr.write(f"Qwen literal ablation: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
