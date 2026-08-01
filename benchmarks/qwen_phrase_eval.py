"""Captured-output evaluation for the exact local Qwen Q4 extractor.

The live path invokes one local LM Studio inference at a time. The saved raw
outputs are then replayed through the strict ModelExtractor and full compiler,
so verification never needs to invoke a model again. Model-only extraction and
deterministic recovery are scored separately against the already frozen phrase
corpus.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import re
import statistics
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from context_compiler import (
    CompilationPolicy,
    ContextCompiler,
    LmsQwenCompletion,
    ModelExtractor,
    SourceRecord,
    __version__,
)
from context_compiler.atomic import atomic_write_text
from context_compiler.extractors import ExtractionResult
from context_compiler.local_qwen import QWEN_Q4_VARIANT

from .json_io import StrictJsonLimits, load_strict_json_file
from .phrase_eval import (
    DEFAULT_PHRASE_CORPUS,
    PHRASE_CORPUS_SCHEMA,
    PhraseCase,
    PhraseCorpus,
    _AtomKey,
    _expanded,
    _expected_counter,
    _f1,
    _predicted_counter,
    _rate,
    load_phrase_corpus,
)

QWEN_PHRASE_REPORT_SCHEMA = "ctxc-qwen-phrase-eval-report-0.1"
QWEN_PHRASE_VERIFICATION_SCHEMA = (
    "ctxc-qwen-phrase-eval-verification-0.1"
)

EVALUATION_TIMEOUT_SECONDS = 240.0
EVALUATION_MAX_OUTPUT_CHARS = 200_000
EVALUATION_MAX_CANDIDATES = 32
EVALUATION_CONTEXT_LENGTH = 8_192
DEFAULT_QWEN_PHRASE_REPORT = (
    Path(__file__).parents[1]
    / "docs"
    / "results"
    / "qwen-novel-english-phrases-v1.json"
)
NORMALIZED_QWEN_PHRASE_COMMAND = (
    "<python-executable>",
    "-m",
    "benchmarks.qwen_phrase_eval",
    "--corpus",
    "<frozen-corpus>",
    "--lms",
    "<local-lms-executable>",
    "--json-out",
    "<report-output>",
)

_POLICY = CompilationPolicy(
    token_budget=100_000,
    minimum_compression_ratio=1.0,
)
_REPORT_LIMITS = StrictJsonLimits(
    max_bytes=32 * 1024 * 1024,
    max_line_chars=512 * 1024,
    max_depth=32,
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_GIT_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_EXCEPTION_TYPE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
_SYSTEM_FIELDS = frozenset(
    {
        "model_id",
        "quantization",
        "parallel",
        "context_length",
        "transport",
        "network_model_api",
        "command_line_prompt_exposure",
        "model_service_cost_usd",
        "max_output_chars",
        "max_response_chars",
        "max_candidates",
        "timeout_seconds",
        "per_call_preflight",
        "lms_executable_sha256",
        "lms_cli_version",
    }
)


class QwenPhraseEvaluationError(ValueError):
    """Qwen phrase-evaluation input or evidence is invalid."""


_READABLE_PACKAGE_VERSIONS = frozenset(
    {
        "0.1.0",
        "0.1.1a1",
        "0.1.1a2",
        "0.1.1a3",
        "0.1.1a4",
        "0.1.1a5",
        "0.1.1a6",
        "0.1.1a7",
        "0.1.1a8",
        "0.1.1a9",
        "0.1.1a10",
        "0.1.1a11",
        "0.1.1a12",
        "0.1.1a13",
        "0.1.1a14",
    }
)


def _validate_qwen_package_version(
    value: Any,
    *,
    label: str,
    error_type: type[ValueError] = QwenPhraseEvaluationError,
) -> str:
    if type(value) is not str or value not in _READABLE_PACKAGE_VERSIONS:
        raise error_type(f"{label} is unsupported")
    return value


@dataclass(frozen=True, slots=True)
class CompletionCapture:
    """One model call bound to its exact ModelExtractor prompt."""

    case_id: str
    prompt_sha256: str
    status: str
    raw_output: str | None
    raw_output_sha256: str | None
    error_type: str | None
    latency_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "prompt_sha256": self.prompt_sha256,
            "raw_output": self.raw_output,
            "raw_output_sha256": self.raw_output_sha256,
            "raw_output_chars": (
                len(self.raw_output)
                if self.raw_output is not None
                else None
            ),
            "error_type": self.error_type,
            "latency_seconds": self.latency_seconds,
        }


class _TimedCompletion:
    def __init__(
        self,
        case_id: str,
        complete: Callable[[str], str],
    ) -> None:
        self.case_id = case_id
        self.complete = complete
        self.capture: CompletionCapture | None = None

    def __call__(self, prompt: str) -> str:
        if self.capture is not None:
            raise QwenPhraseEvaluationError(
                "a phrase case attempted more than one live model call"
            )
        prompt_sha256 = hashlib.sha256(
            prompt.encode("utf-8")
        ).hexdigest()
        started = time.perf_counter()
        try:
            output = self.complete(prompt)
            if not isinstance(output, str):
                raise TypeError(
                    "exact local Qwen completion must return a string"
                )
            if len(output) > EVALUATION_MAX_OUTPUT_CHARS:
                raise QwenPhraseEvaluationError(
                    "exact local Qwen output exceeds evaluation limit"
                )
        except Exception as exc:
            self.capture = CompletionCapture(
                case_id=self.case_id,
                prompt_sha256=prompt_sha256,
                status="error",
                raw_output=None,
                raw_output_sha256=None,
                error_type=type(exc).__name__,
                latency_seconds=round(
                    time.perf_counter() - started,
                    6,
                ),
            )
            raise
        self.capture = CompletionCapture(
            case_id=self.case_id,
            prompt_sha256=prompt_sha256,
            status="ok",
            raw_output=output,
            raw_output_sha256=hashlib.sha256(
                output.encode("utf-8")
            ).hexdigest(),
            error_type=None,
            latency_seconds=round(
                time.perf_counter() - started,
                6,
            ),
        )
        return output


class _ReplayCompletion:
    def __init__(self, capture: CompletionCapture) -> None:
        self.capture = capture

    def __call__(self, prompt: str) -> str:
        prompt_sha256 = hashlib.sha256(
            prompt.encode("utf-8")
        ).hexdigest()
        if prompt_sha256 != self.capture.prompt_sha256:
            raise QwenPhraseEvaluationError(
                f"captured prompt mismatch for case {self.capture.case_id!r}"
            )
        if self.capture.status == "error":
            error_type = self.capture.error_type
            if error_type is None or _EXCEPTION_TYPE.fullmatch(error_type) is None:
                raise QwenPhraseEvaluationError(
                    "captured completion error type is invalid"
                )
            recorded_error = type(error_type, (RuntimeError,), {})
            raise recorded_error("recorded local completion failure")
        if self.capture.raw_output is None:
            raise QwenPhraseEvaluationError(
                "successful capture is missing raw output"
            )
        return self.capture.raw_output


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
        raise QwenPhraseEvaluationError(
            "Qwen phrase evidence is not canonical finite JSON"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _object(
    value: Any,
    *,
    fields: frozenset[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise QwenPhraseEvaluationError(f"{label} must be an object")
    actual = set(value)
    if actual != fields:
        missing = sorted(fields - actual)
        unknown = sorted(actual - fields)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        raise QwenPhraseEvaluationError(
            f"{label} fields are invalid: {'; '.join(details)}"
        )
    return value


def _nonempty_string(
    value: Any,
    *,
    label: str,
    max_chars: int = 4_096,
) -> str:
    if not isinstance(value, str) or not value:
        raise QwenPhraseEvaluationError(
            f"{label} must be a non-empty string"
        )
    if len(value) > max_chars:
        raise QwenPhraseEvaluationError(
            f"{label} exceeds {max_chars} characters"
        )
    return value


def _nonnegative_float(value: Any, *, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise QwenPhraseEvaluationError(
            f"{label} must be finite and non-negative"
        )
    return float(value)


def _source_for_case(case: PhraseCase) -> SourceRecord:
    return SourceRecord.create(
        id=f"phrase:{case.id}",
        sequence=0,
        role=case.role,
        content=case.content,
    )


def _model_extractor(
    complete: Callable[[str], str],
) -> ModelExtractor:
    return ModelExtractor(
        complete,
        model_id=QWEN_Q4_VARIANT,
        max_response_chars=EVALUATION_MAX_OUTPUT_CHARS,
        max_candidates=EVALUATION_MAX_CANDIDATES,
    )


def _model_prompt_sha256(source: SourceRecord) -> str:
    return _extractor_prompt_sha256(
        source,
        extractor_factory=_model_extractor,
    )


def _extractor_prompt_sha256(
    source: SourceRecord,
    *,
    extractor_factory: Callable[
        [Callable[[str], str]],
        ModelExtractor,
    ],
) -> str:
    observed: list[str] = []

    def capture_prompt(prompt: str) -> str:
        observed.append(prompt)
        return '{"items":[]}'

    extractor_factory(capture_prompt).extract([source])
    if len(observed) != 1:
        raise QwenPhraseEvaluationError(
            "extractor did not produce exactly one prompt"
        )
    return hashlib.sha256(observed[0].encode("utf-8")).hexdigest()


def _capture_live_case(
    case: PhraseCase,
    complete: Callable[[str], str],
    *,
    extractor_factory: Callable[
        [Callable[[str], str]],
        ModelExtractor,
    ] = _model_extractor,
) -> CompletionCapture:
    timed = _TimedCompletion(case.id, complete)
    ContextCompiler(
        extractor=extractor_factory(timed),
        policy=_POLICY,
    ).compile([_source_for_case(case)])
    if timed.capture is None:
        raise QwenPhraseEvaluationError(
            f"case {case.id!r} did not invoke the model exactly once"
        )
    return timed.capture


def _primary_extraction(
    source: SourceRecord,
    capture: CompletionCapture,
    *,
    extractor_factory: Callable[
        [Callable[[str], str]],
        ModelExtractor,
    ] = _model_extractor,
) -> tuple[ExtractionResult, str | None]:
    try:
        result = extractor_factory(
            _ReplayCompletion(capture)
        ).extract([source])
    except Exception as exc:
        return ExtractionResult(), type(exc).__name__
    return result, None


def _counter_for_items(items: Sequence[Any]) -> Counter[_AtomKey]:
    return _predicted_counter(SimpleNamespace(items=items))


def _case_score(
    expected: Counter[_AtomKey],
    predicted: Counter[_AtomKey],
) -> tuple[Counter[_AtomKey], Counter[_AtomKey], Counter[_AtomKey]]:
    matched = expected & predicted
    return matched, predicted - expected, expected - predicted


def _analysis_for_capture(
    case: PhraseCase,
    capture: CompletionCapture,
    *,
    extractor_factory: Callable[
        [Callable[[str], str]],
        ModelExtractor,
    ] = _model_extractor,
) -> tuple[
    dict[str, Any],
    Counter[_AtomKey],
    Counter[_AtomKey],
    Counter[_AtomKey],
]:
    source = _source_for_case(case)
    if _extractor_prompt_sha256(
        source,
        extractor_factory=extractor_factory,
    ) != capture.prompt_sha256:
        raise QwenPhraseEvaluationError(
            f"captured prompt mismatch for case {case.id!r}"
        )
    expected = _expected_counter(case, source_id=source.id)
    primary, primary_error_type = _primary_extraction(
        source,
        capture,
        extractor_factory=extractor_factory,
    )
    primary_predicted = _counter_for_items(primary.items)
    primary_matched, primary_fp, primary_fn = _case_score(
        expected,
        primary_predicted,
    )

    memory = ContextCompiler(
        extractor=extractor_factory(_ReplayCompletion(capture)),
        policy=_POLICY,
    ).compile([source])
    final_predicted = _predicted_counter(memory)
    final_matched, final_fp, final_fn = _case_score(
        expected,
        final_predicted,
    )
    recovered_items = [
        item for item in memory.items if "verifier-recovered" in item.tags
    ]
    recovered = _counter_for_items(recovered_items)
    recovered_matched = expected & recovered
    expected_after_model_miss = (
        expected - primary_predicted
    ) & final_predicted

    raw_candidate_count = primary.metadata.get("candidates")
    if (
        isinstance(raw_candidate_count, bool)
        or not isinstance(raw_candidate_count, int)
        or raw_candidate_count < 0
    ):
        raw_candidate_count = None
    candidate_rejections = (
        raw_candidate_count - len(primary.items)
        if raw_candidate_count is not None
        else None
    )
    if candidate_rejections is not None and candidate_rejections < 0:
        raise QwenPhraseEvaluationError(
            "accepted candidates exceed the recorded candidate count"
        )

    metadata = memory.compiler_metadata
    primary_degradation = metadata.get("primary_degradation")
    primary_failure = metadata.get("primary_failure")
    case_report = {
        "id": case.id,
        "role": case.role,
        "capture": capture.to_dict(),
        "expected": _expanded(expected),
        "primary": {
            "candidate_count": raw_candidate_count,
            "accepted_count": len(primary.items),
            "candidate_rejection_count": candidate_rejections,
            "rejection_event_count": len(primary.rejected),
            "rejections": primary.rejected,
            "direct_error_type": primary_error_type,
            "degraded": (
                primary.metadata.get("degraded") is True
                or primary_error_type is not None
            ),
            "failure_reason": primary.metadata.get("failure_reason"),
            "compiler_failure": (
                dict(primary_failure)
                if isinstance(primary_failure, Mapping)
                else None
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
            "true_positive_count": sum(recovered_matched.values()),
            "expected_after_model_miss_count": sum(
                expected_after_model_miss.values()
            ),
            "expected_after_model_miss": _expanded(
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
    return case_report, expected, primary_predicted, final_predicted


def _by_kind(
    expected: Counter[_AtomKey],
    predicted: Counter[_AtomKey],
) -> dict[str, Any]:
    matched = expected & predicted
    kinds = sorted(
        {key.kind for key in expected}
        | {key.kind for key in predicted}
    )
    result: dict[str, Any] = {}
    for kind in kinds:
        expected_count = sum(
            count for key, count in expected.items() if key.kind == kind
        )
        predicted_count = sum(
            count for key, count in predicted.items() if key.kind == kind
        )
        matched_count = sum(
            count for key, count in matched.items() if key.kind == kind
        )
        precision = _rate(matched_count, predicted_count)
        recall = _rate(matched_count, expected_count)
        result[kind] = {
            "expected": expected_count,
            "predicted": predicted_count,
            "true_positives": matched_count,
            "false_positives": predicted_count - matched_count,
            "false_negatives": expected_count - matched_count,
            "precision": precision,
            "recall": recall,
            "f1": _f1(precision, recall),
        }
    return result


def _quality_metrics(
    expected: Counter[_AtomKey],
    predicted: Counter[_AtomKey],
    *,
    case_reports: Sequence[dict[str, Any]],
    section: str,
) -> dict[str, Any]:
    matched = expected & predicted
    true_positives = sum(matched.values())
    expected_count = sum(expected.values())
    predicted_count = sum(predicted.values())
    precision = _rate(true_positives, predicted_count)
    recall = _rate(true_positives, expected_count)
    positive_cases = [
        case for case in case_reports if case["expected"]
    ]
    negative_cases = [
        case for case in case_reports if not case["expected"]
    ]
    exact_matches = sum(
        bool(case[section]["exact_match"]) for case in case_reports
    )
    positive_exact = sum(
        bool(case[section]["exact_match"]) for case in positive_cases
    )
    negative_clean = sum(
        not case[section]["predicted"]
        and (
            section != "final"
            or case["final"]["verification_passed"]
        )
        for case in negative_cases
    )
    result = {
        "expected_atoms": expected_count,
        "predicted_atoms": predicted_count,
        "true_positives": true_positives,
        "false_positives": predicted_count - true_positives,
        "false_negatives": expected_count - true_positives,
        "precision": precision,
        "recall": recall,
        "f1": _f1(precision, recall),
        "case_exact_matches": exact_matches,
        "case_exact_match_rate": _rate(
            exact_matches,
            len(case_reports),
        ),
        "positive_case_exact_matches": positive_exact,
        "positive_case_exact_match_rate": _rate(
            positive_exact,
            len(positive_cases),
        ),
        "negative_cases_without_predictions": negative_clean,
        "negative_case_accuracy": _rate(
            negative_clean,
            len(negative_cases),
        ),
        "by_kind": _by_kind(expected, predicted),
    }
    if section == "final":
        result["verification_failures"] = sum(
            not case["final"]["verification_passed"]
            for case in case_reports
        )
    return result


def _latency_metrics(
    captures: Sequence[CompletionCapture],
) -> dict[str, Any]:
    values = [capture.latency_seconds for capture in captures]
    ordered = sorted(values)
    p95_index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return {
        "model_calls": len(values),
        "total_seconds": round(sum(values), 6),
        "mean_seconds": round(statistics.fmean(values), 6),
        "median_seconds": round(statistics.median(values), 6),
        "p95_seconds": ordered[p95_index],
        "minimum_seconds": ordered[0],
        "maximum_seconds": ordered[-1],
    }


def _evaluate_captures(
    corpus: PhraseCorpus,
    captures: Sequence[CompletionCapture],
    *,
    extractor_factory: Callable[
        [Callable[[str], str]],
        ModelExtractor,
    ] = _model_extractor,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if len(captures) != len(corpus.cases):
        raise QwenPhraseEvaluationError(
            "completion capture count does not match phrase corpus"
        )
    case_reports: list[dict[str, Any]] = []
    total_expected: Counter[_AtomKey] = Counter()
    total_primary: Counter[_AtomKey] = Counter()
    total_final: Counter[_AtomKey] = Counter()
    for case, capture in zip(corpus.cases, captures, strict=True):
        if capture.case_id != case.id:
            raise QwenPhraseEvaluationError(
                "completion captures are not in frozen corpus order"
            )
        report, expected, primary, final = _analysis_for_capture(
            case,
            capture,
            extractor_factory=extractor_factory,
        )
        case_reports.append(report)
        total_expected.update(expected)
        total_primary.update(primary)
        total_final.update(final)

    candidate_count = sum(
        case["primary"]["candidate_count"] or 0
        for case in case_reports
    )
    accepted_count = sum(
        case["primary"]["accepted_count"] for case in case_reports
    )
    candidate_rejections = sum(
        case["primary"]["candidate_rejection_count"] or 0
        for case in case_reports
    )
    rejection_events = sum(
        case["primary"]["rejection_event_count"]
        for case in case_reports
    )
    rejection_reasons = Counter(
        rejection["reason"]
        for case in case_reports
        for rejection in case["primary"]["rejections"]
    )
    error_types = Counter(
        capture.error_type
        for capture in captures
        if capture.error_type is not None
    )
    model_metrics = _quality_metrics(
        total_expected,
        total_primary,
        case_reports=case_reports,
        section="primary",
    )
    final_metrics = _quality_metrics(
        total_expected,
        total_final,
        case_reports=case_reports,
        section="final",
    )
    recovery_added = sum(
        case["recovery"]["added_count"] for case in case_reports
    )
    recovery_true_positives = sum(
        case["recovery"]["true_positive_count"]
        for case in case_reports
    )
    expected_after_miss = sum(
        case["recovery"]["expected_after_model_miss_count"]
        for case in case_reports
    )
    metrics = {
        "completion": {
            "successful_calls": sum(
                capture.status == "ok" for capture in captures
            ),
            "failed_calls": sum(
                capture.status == "error" for capture in captures
            ),
            "error_types": {
                name: error_types[name] for name in sorted(error_types)
            },
        },
        "candidates": {
            "reported_candidates": candidate_count,
            "accepted_candidates": accepted_count,
            "rejected_candidates": candidate_rejections,
            "candidate_rejection_rate": (
                round(candidate_rejections / candidate_count, 6)
                if candidate_count
                else None
            ),
            "rejection_events": rejection_events,
            "rejection_reasons": {
                reason: rejection_reasons[reason]
                for reason in sorted(rejection_reasons)
            },
            "degraded_cases": sum(
                case["primary"]["degraded"] for case in case_reports
            ),
        },
        "model_only": model_metrics,
        "deterministic_recovery": {
            "added_atoms": recovery_added,
            "true_positive_atoms": recovery_true_positives,
            "expected_atoms_added_after_model_miss": expected_after_miss,
            "cases_with_added_atoms": sum(
                case["recovery"]["added_count"] > 0
                for case in case_reports
            ),
            "cases_with_true_positive_atoms": sum(
                case["recovery"]["true_positive_count"] > 0
                for case in case_reports
            ),
            "recall_gain": round(
                final_metrics["recall"] - model_metrics["recall"],
                6,
            ),
        },
        "final_compiler": final_metrics,
        "latency": _latency_metrics(captures),
        "cost": {
            "model_service_cost_usd": 0.0,
            "cost_per_case_usd": 0.0,
        },
    }
    return case_reports, metrics


def _validate_system(document: Any) -> dict[str, Any]:
    system = _object(
        document,
        fields=_SYSTEM_FIELDS,
        label="Qwen phrase system",
    )
    expected = {
        "model_id": QWEN_Q4_VARIANT,
        "quantization": "Q4_K_M",
        "parallel": 1,
        "context_length": EVALUATION_CONTEXT_LENGTH,
        "transport": "lm-studio-cli",
        "network_model_api": False,
        "command_line_prompt_exposure": True,
        "model_service_cost_usd": 0.0,
        "max_output_chars": EVALUATION_MAX_OUTPUT_CHARS,
        "max_response_chars": EVALUATION_MAX_OUTPUT_CHARS,
        "max_candidates": EVALUATION_MAX_CANDIDATES,
        "timeout_seconds": EVALUATION_TIMEOUT_SECONDS,
        "per_call_preflight": True,
    }
    for name, value in expected.items():
        actual = system[name]
        if isinstance(value, bool):
            matches = isinstance(actual, bool) and actual is value
        elif isinstance(value, int):
            matches = (
                not isinstance(actual, bool)
                and isinstance(actual, int)
                and actual == value
            )
        elif isinstance(value, float):
            matches = (
                not isinstance(actual, bool)
                and isinstance(actual, (int, float))
                and math.isfinite(float(actual))
                and float(actual) == value
            )
        else:
            matches = type(actual) is type(value) and actual == value
        if not matches:
            raise QwenPhraseEvaluationError(
                f"Qwen phrase system {name} is not the frozen value"
            )
    executable_digest = system["lms_executable_sha256"]
    if (
        not isinstance(executable_digest, str)
        or _SHA256.fullmatch(executable_digest) is None
    ):
        raise QwenPhraseEvaluationError(
            "Qwen phrase lms executable digest is invalid"
        )
    _nonempty_string(
        system["lms_cli_version"],
        label="Qwen phrase lms_cli_version",
        max_chars=512,
    )
    return system


def exact_qwen_system(
    preflight: Mapping[str, Any],
    *,
    lms_executable_sha256: str,
    lms_cli_version: str,
) -> dict[str, Any]:
    """Build and validate the frozen exact-Qwen evaluation identity."""

    system = {
        "model_id": preflight.get("model_id"),
        "quantization": preflight.get("quantization"),
        "parallel": preflight.get("parallel"),
        "context_length": preflight.get("context_length"),
        "transport": preflight.get("transport"),
        "network_model_api": preflight.get("network_model_api"),
        "command_line_prompt_exposure": preflight.get(
            "command_line_prompt_exposure"
        ),
        "model_service_cost_usd": 0.0,
        "max_output_chars": EVALUATION_MAX_OUTPUT_CHARS,
        "max_response_chars": EVALUATION_MAX_OUTPUT_CHARS,
        "max_candidates": EVALUATION_MAX_CANDIDATES,
        "timeout_seconds": EVALUATION_TIMEOUT_SECONDS,
        "per_call_preflight": True,
        "lms_executable_sha256": lms_executable_sha256,
        "lms_cli_version": lms_cli_version,
    }
    return _validate_system(system)


def _validate_run(document: Any) -> dict[str, Any]:
    fields = frozenset(
        {
            "package_version",
            "repository_commit",
            "repository_dirty",
            "python_version",
            "platform",
            "started_at",
            "duration_seconds",
            "normalized_command",
            "sequential_case_execution",
        }
    )
    run = _object(document, fields=fields, label="Qwen phrase run")
    _validate_qwen_package_version(
        run["package_version"],
        label="Qwen phrase package_version",
    )
    commit = run["repository_commit"]
    if not isinstance(commit, str) or _GIT_OBJECT_ID.fullmatch(commit) is None:
        raise QwenPhraseEvaluationError(
            "Qwen phrase repository_commit is invalid"
        )
    if not isinstance(run["repository_dirty"], bool):
        raise QwenPhraseEvaluationError(
            "Qwen phrase repository_dirty must be a boolean"
        )
    for name in ("python_version", "platform"):
        _nonempty_string(
            run[name],
            label=f"Qwen phrase {name}",
            max_chars=512,
        )
    started_at = _nonempty_string(
        run["started_at"],
        label="Qwen phrase started_at",
        max_chars=128,
    )
    try:
        parsed = datetime.fromisoformat(started_at)
    except ValueError as exc:
        raise QwenPhraseEvaluationError(
            "Qwen phrase started_at is invalid"
        ) from exc
    if parsed.tzinfo is None:
        raise QwenPhraseEvaluationError(
            "Qwen phrase started_at must include a timezone"
        )
    _nonnegative_float(
        run["duration_seconds"],
        label="Qwen phrase duration_seconds",
    )
    _validate_command(run["normalized_command"])
    if run["sequential_case_execution"] is not True:
        raise QwenPhraseEvaluationError(
            "Qwen phrase cases must execute sequentially"
        )
    return run


def _validate_command(value: Any) -> list[str]:
    if not isinstance(value, list) or value != list(
        NORMALIZED_QWEN_PHRASE_COMMAND
    ):
        raise QwenPhraseEvaluationError(
            "Qwen phrase normalized_command is invalid"
        )
    return list(value)


def run_qwen_phrase_evaluation(
    corpus: PhraseCorpus,
    complete: Callable[[str], str],
    *,
    system: Mapping[str, Any],
    command: Sequence[str],
    repository_commit: str,
    repository_dirty: bool,
) -> dict[str, Any]:
    """Invoke the exact local model sequentially and build replayable evidence."""

    if not isinstance(corpus, PhraseCorpus):
        raise TypeError("corpus must be a PhraseCorpus value")
    if not callable(complete):
        raise TypeError("complete must be callable")
    validated_system = _validate_system(dict(system))
    if not isinstance(repository_commit, str):
        raise TypeError("repository_commit must be a string")
    if _GIT_OBJECT_ID.fullmatch(repository_commit) is None:
        raise QwenPhraseEvaluationError(
            "repository_commit must be a canonical Git object id"
        )
    if not isinstance(repository_dirty, bool):
        raise TypeError("repository_dirty must be a boolean")
    if isinstance(command, (str, bytes)):
        raise TypeError("command must be a sequence of strings")
    command_list = _validate_command(list(command))
    started_at = datetime.now(UTC).isoformat()
    started = time.perf_counter()
    captures = [
        _capture_live_case(case, complete) for case in corpus.cases
    ]
    case_reports, metrics = _evaluate_captures(corpus, captures)
    duration_seconds = round(time.perf_counter() - started, 6)
    run = {
        "package_version": __version__,
        "repository_commit": repository_commit,
        "repository_dirty": repository_dirty,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "started_at": started_at,
        "duration_seconds": duration_seconds,
        "normalized_command": command_list,
        "sequential_case_execution": True,
    }
    _validate_run(run)
    report: dict[str, Any] = {
        "schema": QWEN_PHRASE_REPORT_SCHEMA,
        "corpus": {
            "schema": PHRASE_CORPUS_SCHEMA,
            "scope": corpus.scope,
            "corpus_sha256": corpus.corpus_sha256,
            "case_count": len(corpus.cases),
            "positive_case_count": sum(
                bool(case.expected) for case in corpus.cases
            ),
            "negative_case_count": sum(
                not case.expected for case in corpus.cases
            ),
        },
        "system": validated_system,
        "run": run,
        "metrics": metrics,
        "cases": case_reports,
    }
    report["report_sha256"] = _canonical_sha256(report)
    return report


def _decode_capture(
    document: Any,
    *,
    case_id: str,
) -> CompletionCapture:
    fields = frozenset(
        {
            "status",
            "prompt_sha256",
            "raw_output",
            "raw_output_sha256",
            "raw_output_chars",
            "error_type",
            "latency_seconds",
        }
    )
    capture = _object(
        document,
        fields=fields,
        label=f"Qwen phrase capture {case_id!r}",
    )
    prompt_sha256 = capture["prompt_sha256"]
    if (
        not isinstance(prompt_sha256, str)
        or _SHA256.fullmatch(prompt_sha256) is None
    ):
        raise QwenPhraseEvaluationError(
            f"capture {case_id!r} prompt digest is invalid"
        )
    latency = _nonnegative_float(
        capture["latency_seconds"],
        label=f"capture {case_id!r} latency_seconds",
    )
    status = capture["status"]
    if status == "ok":
        raw_output = capture["raw_output"]
        if (
            not isinstance(raw_output, str)
            or len(raw_output) > EVALUATION_MAX_OUTPUT_CHARS
        ):
            raise QwenPhraseEvaluationError(
                f"capture {case_id!r} raw output is invalid"
            )
        output_chars = capture["raw_output_chars"]
        if (
            isinstance(output_chars, bool)
            or not isinstance(output_chars, int)
            or output_chars != len(raw_output)
        ):
            raise QwenPhraseEvaluationError(
                f"capture {case_id!r} output length is invalid"
            )
        output_sha256 = capture["raw_output_sha256"]
        if (
            not isinstance(output_sha256, str)
            or output_sha256
            != hashlib.sha256(raw_output.encode("utf-8")).hexdigest()
        ):
            raise QwenPhraseEvaluationError(
                f"capture {case_id!r} output digest is invalid"
            )
        if capture["error_type"] is not None:
            raise QwenPhraseEvaluationError(
                f"capture {case_id!r} success has an error type"
            )
        return CompletionCapture(
            case_id=case_id,
            prompt_sha256=prompt_sha256,
            status="ok",
            raw_output=raw_output,
            raw_output_sha256=output_sha256,
            error_type=None,
            latency_seconds=latency,
        )
    if status != "error":
        raise QwenPhraseEvaluationError(
            f"capture {case_id!r} status is invalid"
        )
    error_type = capture["error_type"]
    if (
        not isinstance(error_type, str)
        or _EXCEPTION_TYPE.fullmatch(error_type) is None
        or capture["raw_output"] is not None
        or capture["raw_output_sha256"] is not None
        or capture["raw_output_chars"] is not None
    ):
        raise QwenPhraseEvaluationError(
            f"capture {case_id!r} error evidence is invalid"
        )
    return CompletionCapture(
        case_id=case_id,
        prompt_sha256=prompt_sha256,
        status="error",
        raw_output=None,
        raw_output_sha256=None,
        error_type=error_type,
        latency_seconds=latency,
    )


def verify_qwen_phrase_report(
    document: Any,
    corpus: PhraseCorpus,
) -> dict[str, Any]:
    """Validate, self-hash, and replay captured outputs without a model call."""

    fields = frozenset(
        {
            "schema",
            "corpus",
            "system",
            "run",
            "metrics",
            "cases",
            "report_sha256",
        }
    )
    report = _object(
        document,
        fields=fields,
        label="Qwen phrase report",
    )
    if report["schema"] != QWEN_PHRASE_REPORT_SCHEMA:
        raise QwenPhraseEvaluationError(
            "Qwen phrase report schema is unsupported"
        )
    report_sha256 = report["report_sha256"]
    if (
        not isinstance(report_sha256, str)
        or _SHA256.fullmatch(report_sha256) is None
    ):
        raise QwenPhraseEvaluationError(
            "Qwen phrase report_sha256 must be lowercase SHA-256"
        )
    unsigned = dict(report)
    unsigned.pop("report_sha256")
    if _canonical_sha256(unsigned) != report_sha256:
        raise QwenPhraseEvaluationError(
            "Qwen phrase report SHA-256 mismatch"
        )
    expected_corpus = {
        "schema": PHRASE_CORPUS_SCHEMA,
        "scope": corpus.scope,
        "corpus_sha256": corpus.corpus_sha256,
        "case_count": len(corpus.cases),
        "positive_case_count": sum(
            bool(case.expected) for case in corpus.cases
        ),
        "negative_case_count": sum(
            not case.expected for case in corpus.cases
        ),
    }
    if report["corpus"] != expected_corpus:
        raise QwenPhraseEvaluationError(
            "Qwen phrase report corpus identity is invalid"
        )
    _validate_system(report["system"])
    run = _validate_run(report["run"])
    raw_cases = report["cases"]
    if (
        not isinstance(raw_cases, list)
        or len(raw_cases) != len(corpus.cases)
    ):
        raise QwenPhraseEvaluationError(
            "Qwen phrase report case count is invalid"
        )
    case_fields = {
        "id",
        "role",
        "capture",
        "expected",
        "primary",
        "recovery",
        "final",
    }
    captures: list[CompletionCapture] = []
    for expected_case, raw_case in zip(
        corpus.cases,
        raw_cases,
        strict=True,
    ):
        case = _object(
            raw_case,
            fields=frozenset(case_fields),
            label=f"Qwen phrase case {expected_case.id!r}",
        )
        if (
            case["id"] != expected_case.id
            or case["role"] != expected_case.role
        ):
            raise QwenPhraseEvaluationError(
                "Qwen phrase report cases are not in frozen corpus order"
            )
        captures.append(
            _decode_capture(
                case["capture"],
                case_id=expected_case.id,
            )
        )
    replayed_cases, replayed_metrics = _evaluate_captures(
        corpus,
        captures,
    )
    if raw_cases != replayed_cases:
        raise QwenPhraseEvaluationError(
            "Qwen phrase case analysis does not match captured-output replay"
        )
    if report["metrics"] != replayed_metrics:
        raise QwenPhraseEvaluationError(
            "Qwen phrase metrics do not match captured-output replay"
        )
    if (
        run["duration_seconds"] + 0.001
        < replayed_metrics["latency"]["total_seconds"]
    ):
        raise QwenPhraseEvaluationError(
            "Qwen phrase run duration is shorter than its model calls"
        )
    return {
        "schema": QWEN_PHRASE_VERIFICATION_SCHEMA,
        "verified": True,
        "corpus_sha256": corpus.corpus_sha256,
        "report_sha256": report_sha256,
        "case_count": len(corpus.cases),
        "model_id": report["system"]["model_id"],
        "model_calls": replayed_metrics["latency"]["model_calls"],
        "network_model_api": False,
        "model_service_cost_usd": 0.0,
    }


def load_qwen_phrase_report(path: str | Path) -> dict[str, Any]:
    """Strictly load a bounded captured-output report."""

    loaded = load_strict_json_file(
        path,
        limits=_REPORT_LIMITS,
        label="Qwen phrase evaluation report",
    )
    if not isinstance(loaded.value, dict):
        raise QwenPhraseEvaluationError(
            "Qwen phrase evaluation report must be an object"
        )
    return loaded.value


def _repository_state() -> tuple[str, bool]:
    repository = Path(__file__).resolve().parents[1]
    try:
        commit_run = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        status_run = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise QwenPhraseEvaluationError(
            "could not capture repository state"
        ) from exc
    commit = commit_run.stdout.strip()
    if _GIT_OBJECT_ID.fullmatch(commit) is None:
        raise QwenPhraseEvaluationError(
            "repository did not return a canonical commit id"
        )
    return commit, bool(status_run.stdout.strip())


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _lms_version(path: Path) -> str:
    creation_flags = (
        subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        if sys.platform == "win32"
        else 0
    )
    try:
        completed = subprocess.run(
            [str(path), "--version"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise QwenPhraseEvaluationError(
            "could not read the LM Studio CLI version"
        ) from exc
    version = _nonempty_string(
        completed.stdout.strip(),
        label="LM Studio CLI version",
        max_chars=512,
    )
    if any(character in "\r\n" or ord(character) < 0x20 for character in version):
        raise QwenPhraseEvaluationError(
            "LM Studio CLI version must be one printable line"
        )
    return version


def _summary(report: Mapping[str, Any]) -> dict[str, Any]:
    metrics = report["metrics"]
    return {
        "schema": report["schema"],
        "corpus_sha256": report["corpus"]["corpus_sha256"],
        "report_sha256": report["report_sha256"],
        "case_count": report["corpus"]["case_count"],
        "model_id": report["system"]["model_id"],
        "model_only": metrics["model_only"],
        "candidates": metrics["candidates"],
        "deterministic_recovery": metrics["deterministic_recovery"],
        "final_compiler": metrics["final_compiler"],
        "latency": metrics["latency"],
        "cost": metrics["cost"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_PHRASE_CORPUS,
        help="frozen self-hashed phrase corpus",
    )
    parser.add_argument(
        "--lms",
        type=Path,
        help="path to the local LM Studio CLI for a live exact-Qwen run",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        help="required atomic output path for a live captured-output report",
    )
    parser.add_argument(
        "--verify-report",
        type=Path,
        help="strictly verify and replay a saved report without model access",
    )
    args = parser.parse_args(argv)
    if args.verify_report is not None:
        if args.lms is not None or args.json_out is not None:
            parser.error(
                "--verify-report cannot be combined with --lms or --json-out"
            )
    elif args.lms is None or args.json_out is None:
        parser.error("a live run requires both --lms and --json-out")

    try:
        corpus = load_phrase_corpus(args.corpus)
        if args.verify_report is not None:
            verification = verify_qwen_phrase_report(
                load_qwen_phrase_report(args.verify_report),
                corpus,
            )
            sys.stdout.write(
                json.dumps(verification, indent=2, ensure_ascii=True)
                + "\n"
            )
            return 0

        lms_path = args.lms.expanduser().resolve()
        completion = LmsQwenCompletion(
            lms_path,
            timeout_seconds=EVALUATION_TIMEOUT_SECONDS,
            max_output_chars=EVALUATION_MAX_OUTPUT_CHARS,
        )
        preflight = completion.preflight()
        system = exact_qwen_system(
            preflight,
            lms_executable_sha256=_file_sha256(lms_path),
            lms_cli_version=_lms_version(lms_path),
        )
        repository_commit, repository_dirty = _repository_state()
        report = run_qwen_phrase_evaluation(
            corpus,
            completion,
            system=system,
            command=NORMALIZED_QWEN_PHRASE_COMMAND,
            repository_commit=repository_commit,
            repository_dirty=repository_dirty,
        )
        rendered = json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
        atomic_write_text(args.json_out, rendered + "\n")
        sys.stdout.write(
            json.dumps(_summary(report), indent=2, ensure_ascii=True)
            + "\n"
        )
        return 0
    except (
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        sys.stderr.write(f"qwen phrase evaluation: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
