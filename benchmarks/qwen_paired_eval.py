"""Pre-result paired evaluation of coordinate and unique-literal extraction.

The live path uses exactly one local Qwen Q4 inference at a time. Each frozen
held-out case receives both prompts, with mode order alternating by case index.
There are no retries. Saved outputs are replayed deterministically, so offline
verification never calls a model.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from context_compiler import (
    LiteralModelExtractor,
    LmsQwenCompletion,
    __version__,
)
from context_compiler.atomic import atomic_write_text
from context_compiler.local_qwen import QWEN_Q4_VARIANT

from .json_io import StrictJsonLimits, load_strict_json_file
from .phrase_eval import (
    PHRASE_CORPUS_SCHEMA,
    PhraseCorpus,
    load_phrase_corpus,
)
from .qwen_phrase_eval import (
    EVALUATION_MAX_CANDIDATES,
    EVALUATION_MAX_OUTPUT_CHARS,
    EVALUATION_TIMEOUT_SECONDS,
    CompletionCapture,
    _canonical_sha256,
    _capture_live_case,
    _decode_capture,
    _evaluate_captures,
    _file_sha256,
    _lms_version,
    _model_extractor,
    _nonnegative_float,
    _object,
    _repository_state,
    _validate_qwen_package_version,
    _validate_system,
    exact_qwen_system,
)

QWEN_PAIRED_REPORT_SCHEMA = (
    "ctxc-qwen-paired-extractor-eval-report-0.1"
)
QWEN_PAIRED_VERIFICATION_SCHEMA = (
    "ctxc-qwen-paired-extractor-eval-verification-0.1"
)
DEFAULT_HELDOUT_LITERAL_CORPUS = (
    Path(__file__).with_name("data")
    / "heldout_literal_phrases_v1.json"
)
DEFAULT_HELDOUT_LITERAL_CORPUS_SHA256 = (
    "ab5eff1220ad4fb663886dc7d237552c83e602cdee531e7d377d64423ca0e45d"
)
DEFAULT_QWEN_PAIRED_REPORT = (
    Path(__file__).parents[1]
    / "docs"
    / "results"
    / "qwen-heldout-paired-extractors-v1.json"
)
NORMALIZED_QWEN_PAIRED_COMMAND = (
    "<python-executable>",
    "-m",
    "benchmarks.qwen_paired_eval",
    "--corpus",
    "<frozen-corpus>",
    "--lms",
    "<local-lms-executable>",
    "--json-out",
    "<report-output>",
)
LITERAL_MAX_LOCATOR_WORK_CHARS = 10_000_000

_REPORT_LIMITS = StrictJsonLimits(
    max_bytes=64 * 1024 * 1024,
    max_line_chars=512 * 1024,
    max_depth=40,
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_GIT_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_REPORT_FIELDS = frozenset(
    {
        "schema",
        "corpus",
        "protocol",
        "system",
        "run",
        "metrics",
        "cases",
        "report_sha256",
    }
)
_CASE_FIELDS = frozenset(
    {
        "id",
        "role",
        "expected",
        "coordinate",
        "literal",
    }
)
_MODE_CASE_FIELDS = frozenset(
    {
        "capture",
        "primary",
        "recovery",
        "final",
    }
)
_QUALITY_FIELDS = (
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
_DELTA_FIELDS = (
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


class QwenPairedEvaluationError(ValueError):
    """Paired-evaluation input, protocol, or evidence is invalid."""


def qwen_paired_protocol() -> dict[str, Any]:
    """Return a fresh copy of the frozen pre-result analysis protocol."""

    return {
        "analysis_type": (
            "pre-result-heldout-paired-extractor-evaluation"
        ),
        "claim_scope": "local-heldout-diagnostic-only",
        "external_comparison": False,
        "target_outputs_observed_before_freeze": False,
        "coordinate_extractor": "model-json-v1",
        "coordinate_response_schema": (
            "model-extraction.schema.json"
        ),
        "literal_extractor": "model-json-literal-v1",
        "literal_response_schema": (
            "model-extraction-literal.schema.json"
        ),
        "literal_max_locator_work_chars": (
            LITERAL_MAX_LOCATOR_WORK_CHARS
        ),
        "case_call_order": {
            "even_index": ["coordinate", "literal"],
            "odd_index": ["literal", "coordinate"],
        },
        "calls_per_case": 2,
        "samples_per_case_mode": 1,
        "case_concurrency": 1,
        "retries_per_call": 0,
        "sampling_control": (
            "LM Studio CLI exposes no seed or temperature flag; "
            "retain one captured draw per case and mode"
        ),
        "weak_results_retained": True,
        "quality_threshold": None,
        "post_result_validator_tuning_permitted": False,
    }


def _literal_extractor(
    complete: Callable[[str], str],
) -> LiteralModelExtractor:
    return LiteralModelExtractor(
        complete,
        model_id=QWEN_Q4_VARIANT,
        max_response_chars=EVALUATION_MAX_OUTPUT_CHARS,
        max_candidates=EVALUATION_MAX_CANDIDATES,
        max_locator_work_chars=LITERAL_MAX_LOCATOR_WORK_CHARS,
    )


def _capture_pairs(
    corpus: PhraseCorpus,
    complete: Callable[[str], str],
) -> tuple[list[CompletionCapture], list[CompletionCapture]]:
    coordinate: list[CompletionCapture] = []
    literal: list[CompletionCapture] = []
    for index, case in enumerate(corpus.cases):
        if index % 2 == 0:
            coordinate_capture = _capture_live_case(
                case,
                complete,
                extractor_factory=_model_extractor,
            )
            literal_capture = _capture_live_case(
                case,
                complete,
                extractor_factory=_literal_extractor,
            )
        else:
            literal_capture = _capture_live_case(
                case,
                complete,
                extractor_factory=_literal_extractor,
            )
            coordinate_capture = _capture_live_case(
                case,
                complete,
                extractor_factory=_model_extractor,
            )
        coordinate.append(coordinate_capture)
        literal.append(literal_capture)
    return coordinate, literal


def _mode_case(case: Mapping[str, Any]) -> dict[str, Any]:
    return {
        name: case[name]
        for name in (
            "capture",
            "primary",
            "recovery",
            "final",
        )
    }


def _pair_case_reports(
    coordinate: Sequence[Mapping[str, Any]],
    literal: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if len(coordinate) != len(literal):
        raise QwenPairedEvaluationError(
            "coordinate and literal case counts differ"
        )
    paired: list[dict[str, Any]] = []
    for coordinate_case, literal_case in zip(
        coordinate,
        literal,
        strict=True,
    ):
        identity = {
            name: coordinate_case[name]
            for name in ("id", "role", "expected")
        }
        if identity != {
            name: literal_case[name]
            for name in ("id", "role", "expected")
        }:
            raise QwenPairedEvaluationError(
                "coordinate and literal case identities differ"
            )
        paired.append(
            {
                **identity,
                "coordinate": _mode_case(coordinate_case),
                "literal": _mode_case(literal_case),
            }
        )
    return paired


def _selected_quality(
    metrics: Mapping[str, Any],
) -> dict[str, Any]:
    return {name: metrics[name] for name in _QUALITY_FIELDS}


def _quality_delta(
    coordinate: Mapping[str, Any],
    literal: Mapping[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in _DELTA_FIELDS:
        coordinate_value = coordinate[name]
        literal_value = literal[name]
        if (
            isinstance(coordinate_value, bool)
            or not isinstance(coordinate_value, (int, float))
            or not math.isfinite(float(coordinate_value))
            or isinstance(literal_value, bool)
            or not isinstance(literal_value, (int, float))
            or not math.isfinite(float(literal_value))
        ):
            raise QwenPairedEvaluationError(
                f"paired quality metric {name!r} must be finite numeric"
            )
        delta = literal_value - coordinate_value
        result[name] = (
            round(delta, 6)
            if isinstance(delta, float)
            else delta
        )
    return result


def _paired_outcomes(
    cases: Sequence[Mapping[str, Any]],
    *,
    section: str,
) -> dict[str, int]:
    both_exact = 0
    coordinate_only = 0
    literal_only = 0
    neither_exact = 0
    for case in cases:
        coordinate_exact = bool(
            case["coordinate"][section]["exact_match"]
        )
        literal_exact = bool(
            case["literal"][section]["exact_match"]
        )
        if coordinate_exact and literal_exact:
            both_exact += 1
        elif coordinate_exact:
            coordinate_only += 1
        elif literal_exact:
            literal_only += 1
        else:
            neither_exact += 1
    return {
        "both_exact": both_exact,
        "coordinate_only_exact": coordinate_only,
        "literal_only_exact": literal_only,
        "neither_exact": neither_exact,
    }


def _comparison_metrics(
    coordinate: Mapping[str, Any],
    literal: Mapping[str, Any],
    cases: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    coordinate_candidates = coordinate["candidates"]
    literal_candidates = literal["candidates"]
    coordinate_final = coordinate["final_compiler"]
    literal_final = literal["final_compiler"]
    return {
        "literal_minus_coordinate": {
            "model_only": _quality_delta(
                coordinate["model_only"],
                literal["model_only"],
            ),
            "final_compiler": _quality_delta(
                coordinate_final,
                literal_final,
            ),
            "accepted_candidates": (
                literal_candidates["accepted_candidates"]
                - coordinate_candidates["accepted_candidates"]
            ),
            "rejected_candidates": (
                literal_candidates["rejected_candidates"]
                - coordinate_candidates["rejected_candidates"]
            ),
            "degraded_cases": (
                literal_candidates["degraded_cases"]
                - coordinate_candidates["degraded_cases"]
            ),
            "verification_failures": (
                literal_final["verification_failures"]
                - coordinate_final["verification_failures"]
            ),
        },
        "paired_primary_exactness": _paired_outcomes(
            cases,
            section="primary",
        ),
        "paired_final_exactness": _paired_outcomes(
            cases,
            section="final",
        ),
    }


def _evaluate_pairs(
    corpus: PhraseCorpus,
    coordinate_captures: Sequence[CompletionCapture],
    literal_captures: Sequence[CompletionCapture],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    coordinate_cases, coordinate_metrics = _evaluate_captures(
        corpus,
        coordinate_captures,
        extractor_factory=_model_extractor,
    )
    literal_cases, literal_metrics = _evaluate_captures(
        corpus,
        literal_captures,
        extractor_factory=_literal_extractor,
    )
    paired_cases = _pair_case_reports(
        coordinate_cases,
        literal_cases,
    )
    expected_calls = len(corpus.cases) * 2
    actual_calls = (
        coordinate_metrics["latency"]["model_calls"]
        + literal_metrics["latency"]["model_calls"]
    )
    if actual_calls != expected_calls:
        raise QwenPairedEvaluationError(
            "paired evaluation model-call count is inconsistent"
        )
    metrics = {
        "coordinate": coordinate_metrics,
        "literal": literal_metrics,
        "comparison": _comparison_metrics(
            coordinate_metrics,
            literal_metrics,
            paired_cases,
        ),
        "execution": {
            "expected_model_calls": expected_calls,
            "recorded_model_calls": actual_calls,
            "case_concurrency": 1,
            "case_call_order": {
                "even_index": ["coordinate", "literal"],
                "odd_index": ["literal", "coordinate"],
            },
            "samples_per_case_mode": 1,
            "retries_per_call": 0,
            "network_model_api": False,
            "model_service_cost_usd": 0.0,
        },
    }
    return paired_cases, metrics


def _corpus_identity(corpus: PhraseCorpus) -> dict[str, Any]:
    return {
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


def _validate_protocol(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise QwenPairedEvaluationError(
            "Qwen paired protocol must be an object"
        )
    expected = qwen_paired_protocol()
    if value != expected:
        raise QwenPairedEvaluationError(
            "Qwen paired protocol does not match the frozen plan"
        )
    return dict(value)


def _validate_command(value: Any) -> list[str]:
    if not isinstance(value, list) or value != list(
        NORMALIZED_QWEN_PAIRED_COMMAND
    ):
        raise QwenPairedEvaluationError(
            "Qwen paired normalized_command is invalid"
        )
    return list(value)


def _validate_run(value: Any) -> dict[str, Any]:
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
            "sequential_call_execution",
        }
    )
    run = _object(
        value,
        fields=fields,
        label="Qwen paired run",
    )
    _validate_qwen_package_version(
        run["package_version"],
        label="Qwen paired package version",
        error_type=QwenPairedEvaluationError,
    )
    repository_commit = run["repository_commit"]
    if (
        not isinstance(repository_commit, str)
        or _GIT_OBJECT_ID.fullmatch(repository_commit) is None
    ):
        raise QwenPairedEvaluationError(
            "Qwen paired repository commit is invalid"
        )
    if run["repository_dirty"] is not False:
        raise QwenPairedEvaluationError(
            "Qwen paired live evidence requires a clean revision"
        )
    for name in ("python_version", "platform", "started_at"):
        if not isinstance(run[name], str) or not run[name]:
            raise QwenPairedEvaluationError(
                f"Qwen paired {name} is invalid"
            )
    try:
        started_at = datetime.fromisoformat(run["started_at"])
    except ValueError as exc:
        raise QwenPairedEvaluationError(
            "Qwen paired started_at is invalid"
        ) from exc
    if started_at.tzinfo is None:
        raise QwenPairedEvaluationError(
            "Qwen paired started_at must include a timezone"
        )
    _nonnegative_float(
        run["duration_seconds"],
        label="Qwen paired duration_seconds",
    )
    _validate_command(run["normalized_command"])
    if run["sequential_call_execution"] is not True:
        raise QwenPairedEvaluationError(
            "Qwen paired calls must execute sequentially"
        )
    return run


def run_qwen_paired_evaluation(
    corpus: PhraseCorpus,
    complete: Callable[[str], str],
    *,
    system: Mapping[str, Any],
    command: Sequence[str],
    repository_commit: str,
    repository_dirty: bool,
) -> dict[str, Any]:
    """Capture two sequential prompts per held-out case and score both modes."""

    if not isinstance(corpus, PhraseCorpus):
        raise TypeError("corpus must be a PhraseCorpus value")
    if corpus.corpus_sha256 != DEFAULT_HELDOUT_LITERAL_CORPUS_SHA256:
        raise QwenPairedEvaluationError(
            "live paired evaluation requires the frozen held-out corpus"
        )
    if not callable(complete):
        raise TypeError("complete must be callable")
    validated_system = _validate_system(dict(system))
    if (
        not isinstance(repository_commit, str)
        or _GIT_OBJECT_ID.fullmatch(repository_commit) is None
    ):
        raise QwenPairedEvaluationError(
            "repository_commit must be a canonical Git object id"
        )
    if repository_dirty is not False:
        raise QwenPairedEvaluationError(
            "live paired evaluation requires a clean repository"
        )
    if isinstance(command, (str, bytes)):
        raise TypeError("command must be a sequence of strings")
    command_list = _validate_command(list(command))

    started_at = datetime.now(UTC).isoformat()
    started = time.perf_counter()
    coordinate_captures, literal_captures = _capture_pairs(
        corpus,
        complete,
    )
    case_reports, metrics = _evaluate_pairs(
        corpus,
        coordinate_captures,
        literal_captures,
    )
    duration_seconds = round(time.perf_counter() - started, 6)
    run = {
        "package_version": __version__,
        "repository_commit": repository_commit,
        "repository_dirty": False,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "started_at": started_at,
        "duration_seconds": duration_seconds,
        "normalized_command": command_list,
        "sequential_call_execution": True,
    }
    _validate_run(run)
    report: dict[str, Any] = {
        "schema": QWEN_PAIRED_REPORT_SCHEMA,
        "corpus": _corpus_identity(corpus),
        "protocol": qwen_paired_protocol(),
        "system": validated_system,
        "run": run,
        "metrics": metrics,
        "cases": case_reports,
    }
    report["report_sha256"] = _canonical_sha256(report)
    return report


def _decode_mode_capture(
    value: Any,
    *,
    case_id: str,
    mode: str,
) -> CompletionCapture:
    mode_case = _object(
        value,
        fields=_MODE_CASE_FIELDS,
        label=f"Qwen paired {mode} case {case_id!r}",
    )
    return _decode_capture(
        mode_case["capture"],
        case_id=case_id,
    )


def verify_qwen_paired_report(
    document: Any,
    corpus: PhraseCorpus,
) -> dict[str, Any]:
    """Self-hash and replay both prompt modes without model access."""

    report = _object(
        document,
        fields=_REPORT_FIELDS,
        label="Qwen paired report",
    )
    if report["schema"] != QWEN_PAIRED_REPORT_SCHEMA:
        raise QwenPairedEvaluationError(
            "Qwen paired report schema is unsupported"
        )
    report_sha256 = report["report_sha256"]
    if (
        not isinstance(report_sha256, str)
        or _SHA256.fullmatch(report_sha256) is None
    ):
        raise QwenPairedEvaluationError(
            "Qwen paired report hash is invalid"
        )
    unsigned = dict(report)
    unsigned.pop("report_sha256")
    if _canonical_sha256(unsigned) != report_sha256:
        raise QwenPairedEvaluationError(
            "Qwen paired report SHA-256 mismatch"
        )
    if corpus.corpus_sha256 != DEFAULT_HELDOUT_LITERAL_CORPUS_SHA256:
        raise QwenPairedEvaluationError(
            "Qwen paired verifier requires the frozen held-out corpus"
        )
    if report["corpus"] != _corpus_identity(corpus):
        raise QwenPairedEvaluationError(
            "Qwen paired report corpus identity is invalid"
        )
    _validate_protocol(report["protocol"])
    _validate_system(report["system"])
    run = _validate_run(report["run"])
    raw_cases = report["cases"]
    if (
        not isinstance(raw_cases, list)
        or len(raw_cases) != len(corpus.cases)
    ):
        raise QwenPairedEvaluationError(
            "Qwen paired report case count is invalid"
        )
    coordinate_captures: list[CompletionCapture] = []
    literal_captures: list[CompletionCapture] = []
    for expected_case, raw_case in zip(
        corpus.cases,
        raw_cases,
        strict=True,
    ):
        case = _object(
            raw_case,
            fields=_CASE_FIELDS,
            label=f"Qwen paired case {expected_case.id!r}",
        )
        if (
            case["id"] != expected_case.id
            or case["role"] != expected_case.role
        ):
            raise QwenPairedEvaluationError(
                "Qwen paired cases are not in frozen corpus order"
            )
        coordinate_captures.append(
            _decode_mode_capture(
                case["coordinate"],
                case_id=expected_case.id,
                mode="coordinate",
            )
        )
        literal_captures.append(
            _decode_mode_capture(
                case["literal"],
                case_id=expected_case.id,
                mode="literal",
            )
        )
    replayed_cases, replayed_metrics = _evaluate_pairs(
        corpus,
        coordinate_captures,
        literal_captures,
    )
    if raw_cases != replayed_cases:
        raise QwenPairedEvaluationError(
            "Qwen paired case analysis does not match replay"
        )
    if report["metrics"] != replayed_metrics:
        raise QwenPairedEvaluationError(
            "Qwen paired metrics do not match replay"
        )
    total_latency = (
        replayed_metrics["coordinate"]["latency"][
            "total_seconds"
        ]
        + replayed_metrics["literal"]["latency"]["total_seconds"]
    )
    if run["duration_seconds"] + 0.001 < total_latency:
        raise QwenPairedEvaluationError(
            "Qwen paired run duration is shorter than its model calls"
        )
    return {
        "schema": QWEN_PAIRED_VERIFICATION_SCHEMA,
        "verified": True,
        "corpus_sha256": corpus.corpus_sha256,
        "report_sha256": report_sha256,
        "case_count": len(corpus.cases),
        "model_id": report["system"]["model_id"],
        "model_calls": replayed_metrics["execution"][
            "recorded_model_calls"
        ],
        "case_concurrency": 1,
        "network_model_api": False,
        "model_service_cost_usd": 0.0,
    }


def load_qwen_paired_report(
    path: str | Path,
) -> dict[str, Any]:
    loaded = load_strict_json_file(
        path,
        limits=_REPORT_LIMITS,
        label="Qwen paired evaluation report",
    )
    if not isinstance(loaded.value, dict):
        raise QwenPairedEvaluationError(
            "Qwen paired evaluation report must be an object"
        )
    return loaded.value


def _mode_summary(metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "model_only": _selected_quality(metrics["model_only"]),
        "candidates": metrics["candidates"],
        "deterministic_recovery": metrics[
            "deterministic_recovery"
        ],
        "final_compiler": {
            **_selected_quality(metrics["final_compiler"]),
            "verification_failures": metrics["final_compiler"][
                "verification_failures"
            ],
        },
        "completion": metrics["completion"],
        "latency": metrics["latency"],
        "cost": metrics["cost"],
    }


def _summary(report: Mapping[str, Any]) -> dict[str, Any]:
    metrics = report["metrics"]
    return {
        "schema": report["schema"],
        "corpus_sha256": report["corpus"]["corpus_sha256"],
        "report_sha256": report["report_sha256"],
        "case_count": report["corpus"]["case_count"],
        "model_id": report["system"]["model_id"],
        "coordinate": _mode_summary(metrics["coordinate"]),
        "literal": _mode_summary(metrics["literal"]),
        "comparison": metrics["comparison"],
        "execution": metrics["execution"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_HELDOUT_LITERAL_CORPUS,
        help="frozen self-hashed held-out phrase corpus",
    )
    parser.add_argument(
        "--lms",
        type=Path,
        help="path to the local LM Studio CLI for a live exact-Qwen run",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        help="new atomic destination for the paired captured-output report",
    )
    parser.add_argument(
        "--verify-report",
        type=Path,
        help="strictly replay a saved paired report without model access",
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
            verification = verify_qwen_paired_report(
                load_qwen_paired_report(args.verify_report),
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

        output_path = args.json_out.expanduser().resolve()
        if output_path.exists():
            raise QwenPairedEvaluationError(
                "live paired report destination already exists"
            )
        repository_commit, repository_dirty = _repository_state()
        if repository_dirty:
            raise QwenPairedEvaluationError(
                "live paired evaluation requires a clean repository"
            )
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
        report = run_qwen_paired_evaluation(
            corpus,
            completion,
            system=system,
            command=NORMALIZED_QWEN_PAIRED_COMMAND,
            repository_commit=repository_commit,
            repository_dirty=False,
        )
        rendered = json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
        atomic_write_text(
            output_path,
            rendered + "\n",
            overwrite=False,
        )
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
        sys.stderr.write(f"Qwen paired evaluation: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
