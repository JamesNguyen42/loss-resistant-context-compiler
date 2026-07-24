"""Strict, resource-bounded verification for saved LRCBench JSON reports."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean
from typing import Any

from .json_io import StrictJsonError, StrictJsonLimits, load_strict_json_file
from .lrcbench import (
    BENCHMARK_VERSION,
    BUNDLED_SYSTEMS,
    CANDIDATE_PRODUCER_SCHEMA,
    CANDIDATE_SCHEMA,
    CORPUS_PRODUCER_SCHEMA,
    CORPUS_SCHEMA,
    LEGACY_REPORT_SCHEMA,
    REPORT_SCHEMA,
    TOKENIZER_ID,
    BenchmarkConfig,
    BenchmarkRunMetadata,
    ComponentRevision,
    _canonical_sha256,
    dataset_digest,
    estimate_tokens,
    generate_histories,
)

_MIB = 1024 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SYSTEM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_GAIN_BASES = frozenset(
    {
        "critical-semantic-loss-reduction",
        "memory-quality-efficiency",
        "critical-semantic-loss-reduction+memory-quality-efficiency",
    }
)
_RATE_FIELDS = frozenset(
    {
        "critical_atom_recall",
        "exact_literal_recall",
        "provenance_validity",
        "unresolved_to_fact_rate",
        "unsupported_critical_claim_rate",
        "authority_accuracy",
        "authority_violation_rate",
        "semantic_support_accuracy",
        "unsupported_claim_rate",
        "stale_claim_rate",
        "history_perfect_rate",
        "budget_compliance_rate",
        "quality_score",
    }
)
_COUNT_FIELDS = frozenset({"source_tokens", "active_tokens", "histories"})
_HISTORY_COUNT_FIELDS = frozenset(
    {
        "critical_recalled",
        "critical_total",
        "exact_recalled",
        "exact_total",
        "valid_claims",
        "claim_total",
        "unresolved_promotions",
        "unresolved_total",
        "unsupported_critical_claims",
        "authority_negative_total",
        "authority_violations",
        "authority_claim_total",
        "semantically_supported_claims",
        "unsupported_claims",
        "stale_claims",
        "inactive_atom_total",
        "source_tokens",
        "active_tokens",
    }
)
_SYSTEM_FIELDS = {
    "system",
    *_RATE_FIELDS,
    "corpus_compression_ratio",
    "memory_quality_efficiency",
    *_COUNT_FIELDS,
}
_HISTORY_FIELDS = {
    "case_id",
    *_HISTORY_COUNT_FIELDS,
    "budget_compliant",
    "compression_ratio",
    "perfect",
    "critical_atom_recall",
    "exact_literal_recall",
    "provenance_validity",
    "unresolved_to_fact_rate",
    "unsupported_critical_claim_rate",
    "authority_accuracy",
    "authority_violation_rate",
    "semantic_support_accuracy",
    "unsupported_claim_rate",
    "stale_claim_rate",
    "quality_score",
}
_COMPARISON_FIELDS = {
    "system",
    "external",
    "decision",
    "gain_basis",
    "critical_semantic_loss_reduction",
    "memory_quality_efficiency_gain",
    "critical_loss_margin_lower",
    "memory_quality_efficiency_margin_lower",
    "reasons",
}
_LEGACY_CERTIFICATE_FIELDS = {
    "issued",
    "candidate",
    "strongest_baseline",
    "candidate_quality",
    "baseline_quality",
    "gain_basis",
    "critical_semantic_loss_reduction",
    "memory_quality_efficiency_gain",
    "critical_loss_margin_lower",
    "memory_quality_efficiency_margin_lower",
    "bootstrap_lower_quantile",
    "evidence_sha256",
    "scope",
    "compared_baselines",
    "external_baselines",
    "external_wins",
    "external_required_wins",
    "external_majority_passed",
    "external_manifests",
    "comparisons",
    "reasons",
    "claim",
}
_CERTIFICATE_FIELDS = _LEGACY_CERTIFICATE_FIELDS | {"external_protocol"}
_EXTERNAL_PROTOCOL_FIELDS = {
    "protocol_id",
    "protocol_sha256",
    "document_sha256",
    "synthetic_dataset_sha256",
    "registered_systems",
}
_RUN_METADATA_FIELDS = {
    "started_at",
    "duration_seconds",
    "repository_commit",
    "repository_dirty",
    "package_version",
    "python_version",
    "platform",
    "command",
    "tokenizer_id",
    "model_id",
    "schema_versions",
    "baseline_revisions",
    "model_service_cost_usd",
    "failures",
}
_TOP_LEVEL_FIELDS = {
    "report_schema",
    "benchmark",
    "corpus_schema",
    "candidate_schema",
    "config",
    "dataset_sha256",
    "systems",
    "certificate",
    "run_metadata",
    "report_sha256",
}


class BenchmarkReportError(ValueError):
    """A saved benchmark report is malformed, inconsistent, or too large."""


@dataclass(frozen=True, slots=True)
class BenchmarkReportLimits:
    """Resource limits applied before a report is accepted or regenerated."""

    max_input_bytes: int = 128 * _MIB
    max_line_chars: int = 8 * _MIB
    max_json_depth: int = 64
    max_systems: int = 1_024
    max_histories_per_system: int = 100_000
    max_collection_items: int = 100_000
    max_generated_source_events: int = 100_000
    max_generated_noise_lines: int = 1_000_000

    def __post_init__(self) -> None:
        for name in (
            "max_input_bytes",
            "max_line_chars",
            "max_json_depth",
            "max_systems",
            "max_histories_per_system",
            "max_collection_items",
            "max_generated_source_events",
            "max_generated_noise_lines",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value <= 0:
                raise ValueError(f"{name} must be positive")


DEFAULT_BENCHMARK_REPORT_LIMITS = BenchmarkReportLimits()


@dataclass(frozen=True, slots=True)
class VerifiedBenchmarkReport:
    """Security-relevant summary returned after complete report verification."""

    report_sha256: str
    evidence_sha256: str
    dataset_sha256: str
    benchmark: str
    systems: tuple[str, ...]
    histories: int
    per_history_included: bool
    certificate_issued: bool
    certificate_scope: str
    repository_commit: str | None
    repository_dirty: bool | None
    model_id: str
    model_service_cost_usd: float | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _object(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise BenchmarkReportError(f"{context} must be a JSON object")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], context: str) -> None:
    missing = sorted(expected - value.keys())
    unknown = sorted(value.keys() - expected)
    if missing:
        raise BenchmarkReportError(f"{context} is missing fields: {', '.join(missing)}")
    if unknown:
        raise BenchmarkReportError(f"{context} has unknown fields: {', '.join(unknown)}")


def _array(
    value: object,
    context: str,
    *,
    limit: int,
    allow_tuple: bool = True,
) -> list[Any] | tuple[Any, ...]:
    expected_types = (list, tuple) if allow_tuple else (list,)
    if not isinstance(value, expected_types):
        raise BenchmarkReportError(f"{context} must be a JSON array")
    if len(value) > limit:
        raise BenchmarkReportError(f"{context} exceeds {limit} entries")
    return value


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise BenchmarkReportError(f"{context} must be a non-empty string")
    return value


def _system_name(value: object, context: str) -> str:
    decoded = _string(value, context)
    if _SYSTEM_RE.fullmatch(decoded) is None:
        raise BenchmarkReportError(f"{context} is not a valid system identifier")
    return decoded


def _boolean(value: object, context: str) -> bool:
    if not isinstance(value, bool):
        raise BenchmarkReportError(f"{context} must be a boolean")
    return value


def _integer(
    value: object,
    context: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BenchmarkReportError(f"{context} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        suffix = (
            f"between {minimum} and {maximum}"
            if maximum is not None
            else f"at least {minimum}"
        )
        raise BenchmarkReportError(f"{context} must be {suffix}")
    return value


def _number(
    value: object,
    context: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BenchmarkReportError(f"{context} must be numeric")
    decoded = float(value)
    if not math.isfinite(decoded):
        raise BenchmarkReportError(f"{context} must be finite")
    if minimum is not None and decoded < minimum:
        raise BenchmarkReportError(f"{context} must be at least {minimum}")
    if maximum is not None and decoded > maximum:
        raise BenchmarkReportError(f"{context} must be at most {maximum}")
    return decoded


def _optional_number(value: object, context: str) -> float | None:
    return None if value is None else _number(value, context)


def _sha256(value: object, context: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise BenchmarkReportError(f"{context} must be a lowercase SHA-256 digest")
    return value


def _string_array(
    value: object,
    context: str,
    *,
    limits: BenchmarkReportLimits,
    unique: bool = False,
) -> tuple[str, ...]:
    raw = _array(value, context, limit=limits.max_collection_items)
    decoded = tuple(_string(item, f"{context}[{index}]") for index, item in enumerate(raw))
    if unique and len(decoded) != len(set(decoded)):
        raise BenchmarkReportError(f"{context} must not contain duplicates")
    return decoded


def _close(actual: object, expected: float, context: str) -> None:
    decoded = _number(actual, context)
    if not math.isclose(decoded, expected, rel_tol=1e-12, abs_tol=1e-12):
        raise BenchmarkReportError(
            f"{context} is inconsistent (recorded {decoded!r}, expected {expected!r})"
        )


def _optional_close(actual: object, expected: object, context: str) -> None:
    if actual is None or expected is None:
        if actual is not expected:
            raise BenchmarkReportError(f"{context} has inconsistent nullability")
        return
    _close(actual, _number(expected, context), context)


def _ratio(numerator: int, denominator: int, *, empty: float) -> float:
    return numerator / denominator if denominator else empty


def _harmonic(values: list[float]) -> float:
    if not values or any(value <= 0.0 for value in values):
        return 0.0
    return len(values) / sum(1.0 / value for value in values)


def _validate_history_metrics(
    value: object,
    context: str,
    *,
    config: BenchmarkConfig,
) -> dict[str, Any]:
    history = _object(value, context)
    _exact_keys(history, _HISTORY_FIELDS, context)
    _string(history["case_id"], f"{context}.case_id")
    counts = {
        name: _integer(history[name], f"{context}.{name}") for name in _HISTORY_COUNT_FIELDS
    }
    if counts["active_tokens"] < 1:
        raise BenchmarkReportError(f"{context}.active_tokens must be positive")
    for name in _RATE_FIELDS - {"history_perfect_rate", "budget_compliance_rate"}:
        _number(history[name], f"{context}.{name}", minimum=0.0, maximum=1.0)
    _number(history["compression_ratio"], f"{context}.compression_ratio", minimum=0.0)
    _boolean(history["budget_compliant"], f"{context}.budget_compliant")
    _boolean(history["perfect"], f"{context}.perfect")

    if counts["critical_recalled"] > counts["critical_total"]:
        raise BenchmarkReportError(f"{context}.critical_recalled exceeds its total")
    if counts["exact_recalled"] > counts["exact_total"]:
        raise BenchmarkReportError(f"{context}.exact_recalled exceeds its total")
    for numerator, denominator in (
        ("valid_claims", "claim_total"),
        ("semantically_supported_claims", "claim_total"),
        ("unsupported_claims", "claim_total"),
        ("unresolved_promotions", "unresolved_total"),
        ("unsupported_critical_claims", "authority_negative_total"),
        ("authority_violations", "authority_claim_total"),
        ("stale_claims", "inactive_atom_total"),
    ):
        if counts[numerator] > counts[denominator]:
            raise BenchmarkReportError(f"{context}.{numerator} exceeds {denominator}")
    if counts["authority_claim_total"] != counts["claim_total"]:
        raise BenchmarkReportError(
            f"{context}.authority_claim_total must equal claim_total"
        )
    if (
        counts["unsupported_claims"]
        != counts["claim_total"] - counts["semantically_supported_claims"]
    ):
        raise BenchmarkReportError(
            f"{context}.unsupported_claims is inconsistent with supported claims"
        )

    exact_compression = counts["source_tokens"] / max(1, counts["active_tokens"])
    expected_rates = {
        "critical_atom_recall": _ratio(
            counts["critical_recalled"], counts["critical_total"], empty=1.0
        ),
        "exact_literal_recall": _ratio(
            counts["exact_recalled"], counts["exact_total"], empty=1.0
        ),
        "provenance_validity": _ratio(
            counts["valid_claims"], counts["claim_total"], empty=0.0
        ),
        "unresolved_to_fact_rate": _ratio(
            counts["unresolved_promotions"], counts["unresolved_total"], empty=0.0
        ),
        "unsupported_critical_claim_rate": _ratio(
            counts["unsupported_critical_claims"],
            counts["authority_negative_total"],
            empty=0.0,
        ),
        "authority_accuracy": 1.0
        - _ratio(
            counts["unsupported_critical_claims"],
            counts["authority_negative_total"],
            empty=0.0,
        ),
        "authority_violation_rate": _ratio(
            counts["authority_violations"], counts["authority_claim_total"], empty=0.0
        ),
        "semantic_support_accuracy": _ratio(
            counts["semantically_supported_claims"], counts["claim_total"], empty=0.0
        ),
        "unsupported_claim_rate": _ratio(
            counts["unsupported_claims"], counts["claim_total"], empty=0.0
        ),
        "stale_claim_rate": _ratio(
            counts["stale_claims"], counts["inactive_atom_total"], empty=0.0
        ),
        "compression_ratio": round(exact_compression, 6),
    }
    expected_quality = _harmonic(
        [
            expected_rates["critical_atom_recall"],
            expected_rates["exact_literal_recall"],
            expected_rates["provenance_validity"],
            1.0 - expected_rates["unresolved_to_fact_rate"],
            expected_rates["authority_accuracy"],
            1.0 - expected_rates["authority_violation_rate"],
            expected_rates["semantic_support_accuracy"],
            1.0 - expected_rates["stale_claim_rate"],
        ]
    )
    expected_rates["quality_score"] = expected_quality
    for name, expected in expected_rates.items():
        _close(history[name], expected, f"{context}.{name}")
    expected_budget_compliant = counts["active_tokens"] <= config.token_budget
    if history["budget_compliant"] != expected_budget_compliant:
        raise BenchmarkReportError(f"{context}.budget_compliant is inconsistent")
    expected_perfect = (
        expected_rates["critical_atom_recall"] == 1.0
        and expected_rates["exact_literal_recall"] == 1.0
        and expected_rates["provenance_validity"] == 1.0
        and expected_rates["unresolved_to_fact_rate"] == 0.0
        and expected_rates["unsupported_critical_claim_rate"] == 0.0
        and expected_rates["authority_violation_rate"] == 0.0
        and expected_rates["unsupported_claim_rate"] == 0.0
        and expected_rates["stale_claim_rate"] == 0.0
        and expected_budget_compliant
        and exact_compression >= config.minimum_compression
    )
    if history["perfect"] != expected_perfect:
        raise BenchmarkReportError(f"{context}.perfect is inconsistent")
    return history


def _validate_aggregate_consistency(
    aggregate: dict[str, Any],
    histories: list[dict[str, Any]],
    context: str,
) -> None:
    source_tokens = sum(history["source_tokens"] for history in histories)
    active_tokens = sum(history["active_tokens"] for history in histories)
    if aggregate["source_tokens"] != source_tokens:
        raise BenchmarkReportError(f"{context}.source_tokens does not match per_history")
    if aggregate["active_tokens"] != active_tokens:
        raise BenchmarkReportError(f"{context}.active_tokens does not match per_history")

    def combined_ratio(numerator: str, denominator: str, *, empty: float) -> float:
        top = sum(history[numerator] for history in histories)
        bottom = sum(history[denominator] for history in histories)
        return top / bottom if bottom else empty

    expected = {
        "critical_atom_recall": fmean(
            history["critical_atom_recall"] for history in histories
        ),
        "exact_literal_recall": fmean(
            history["exact_literal_recall"] for history in histories
        ),
        "provenance_validity": combined_ratio(
            "valid_claims", "claim_total", empty=0.0
        ),
        "unresolved_to_fact_rate": combined_ratio(
            "unresolved_promotions", "unresolved_total", empty=0.0
        ),
        "unsupported_critical_claim_rate": combined_ratio(
            "unsupported_critical_claims", "authority_negative_total", empty=0.0
        ),
        "authority_accuracy": 1.0
        - combined_ratio(
            "unsupported_critical_claims", "authority_negative_total", empty=0.0
        ),
        "authority_violation_rate": combined_ratio(
            "authority_violations", "authority_claim_total", empty=0.0
        ),
        "semantic_support_accuracy": combined_ratio(
            "semantically_supported_claims", "claim_total", empty=0.0
        ),
        "unsupported_claim_rate": combined_ratio(
            "unsupported_claims", "claim_total", empty=0.0
        ),
        "stale_claim_rate": combined_ratio(
            "stale_claims", "inactive_atom_total", empty=0.0
        ),
        "history_perfect_rate": fmean(float(history["perfect"]) for history in histories),
        "budget_compliance_rate": fmean(
            float(history["budget_compliant"]) for history in histories
        ),
        "corpus_compression_ratio": source_tokens / max(1, active_tokens),
        "quality_score": fmean(history["quality_score"] for history in histories),
        "memory_quality_efficiency": fmean(
            history["quality_score"] / max(1, history["active_tokens"])
            for history in histories
        ),
    }
    for name, expected_value in expected.items():
        _close(aggregate[name], expected_value, f"{context}.{name}")


def _validate_system(
    value: object,
    *,
    index: int,
    config: BenchmarkConfig,
    expected_history_sources: tuple[tuple[str, int], ...],
    limits: BenchmarkReportLimits,
) -> tuple[dict[str, Any], tuple[str, ...] | None]:
    context = f"report.systems[{index}]"
    aggregate = _object(value, context)
    allowed = _SYSTEM_FIELDS | {"per_history"}
    missing = sorted(_SYSTEM_FIELDS - aggregate.keys())
    unknown = sorted(aggregate.keys() - allowed)
    if missing:
        raise BenchmarkReportError(f"{context} is missing fields: {', '.join(missing)}")
    if unknown:
        raise BenchmarkReportError(f"{context} has unknown fields: {', '.join(unknown)}")
    _system_name(aggregate["system"], f"{context}.system")
    for name in _RATE_FIELDS:
        _number(aggregate[name], f"{context}.{name}", minimum=0.0, maximum=1.0)
    _number(
        aggregate["corpus_compression_ratio"],
        f"{context}.corpus_compression_ratio",
        minimum=0.0,
    )
    _number(
        aggregate["memory_quality_efficiency"],
        f"{context}.memory_quality_efficiency",
        minimum=0.0,
    )
    for name in _COUNT_FIELDS:
        _integer(aggregate[name], f"{context}.{name}")
    if aggregate["histories"] != config.histories:
        raise BenchmarkReportError(
            f"{context}.histories must equal config.histories ({config.histories})"
        )
    expected_source_tokens = sum(tokens for _case_id, tokens in expected_history_sources)
    if aggregate["source_tokens"] != expected_source_tokens:
        raise BenchmarkReportError(
            f"{context}.source_tokens does not match the regenerated corpus"
        )
    if aggregate["active_tokens"] < config.histories:
        raise BenchmarkReportError(
            f"{context}.active_tokens must include at least one token per history"
        )
    _close(
        aggregate["corpus_compression_ratio"],
        expected_source_tokens / aggregate["active_tokens"],
        f"{context}.corpus_compression_ratio",
    )
    if "per_history" not in aggregate:
        return aggregate, None

    raw_histories = _array(
        aggregate["per_history"],
        f"{context}.per_history",
        limit=limits.max_histories_per_system,
    )
    if len(raw_histories) != config.histories:
        raise BenchmarkReportError(
            f"{context}.per_history must contain {config.histories} histories"
        )
    histories = [
        _validate_history_metrics(
            history,
            f"{context}.per_history[{history_index}]",
            config=config,
        )
        for history_index, history in enumerate(raw_histories)
    ]
    case_ids = tuple(history["case_id"] for history in histories)
    if len(case_ids) != len(set(case_ids)):
        raise BenchmarkReportError(f"{context}.per_history has duplicate case ids")
    for history, (expected_case_id, expected_tokens) in zip(
        histories,
        expected_history_sources,
        strict=True,
    ):
        if history["case_id"] != expected_case_id:
            raise BenchmarkReportError(
                f"{context}.per_history case ids do not match the regenerated corpus"
            )
        if history["source_tokens"] != expected_tokens:
            raise BenchmarkReportError(
                f"{context}.per_history source tokens do not match the regenerated corpus"
            )
    _validate_aggregate_consistency(aggregate, histories, context)
    return aggregate, case_ids


def _component_revisions(
    value: object,
    context: str,
    *,
    limits: BenchmarkReportLimits,
) -> tuple[ComponentRevision, ...]:
    raw_revisions = _array(value, context, limit=limits.max_collection_items)
    revisions: list[ComponentRevision] = []
    for index, raw_revision in enumerate(raw_revisions):
        item_context = f"{context}[{index}]"
        revision = _object(raw_revision, item_context)
        _exact_keys(revision, {"name", "revision"}, item_context)
        try:
            revisions.append(
                ComponentRevision(
                    name=_string(revision["name"], f"{item_context}.name"),
                    revision=_string(revision["revision"], f"{item_context}.revision"),
                )
            )
        except (TypeError, ValueError) as exc:
            raise BenchmarkReportError(f"{item_context} is invalid: {exc}") from exc
    names = [revision.name for revision in revisions]
    if len(names) != len(set(names)):
        raise BenchmarkReportError(f"{context} has duplicate component names")
    return tuple(revisions)


def _validate_run_metadata(
    value: object,
    *,
    report_schema: str,
    certificate_reasons: tuple[str, ...],
    compared_baselines: tuple[str, ...],
    external_baselines: tuple[str, ...],
    limits: BenchmarkReportLimits,
) -> BenchmarkRunMetadata:
    context = "report.run_metadata"
    metadata = _object(value, context)
    _exact_keys(metadata, _RUN_METADATA_FIELDS, context)
    command = _string_array(metadata["command"], f"{context}.command", limits=limits)
    failures = _string_array(
        metadata["failures"],
        f"{context}.failures",
        limits=limits,
        unique=True,
    )
    schema_versions = _component_revisions(
        metadata["schema_versions"],
        f"{context}.schema_versions",
        limits=limits,
    )
    baseline_revisions = _component_revisions(
        metadata["baseline_revisions"],
        f"{context}.baseline_revisions",
        limits=limits,
    )
    try:
        decoded = BenchmarkRunMetadata(
            started_at=metadata["started_at"],
            duration_seconds=metadata["duration_seconds"],
            repository_commit=metadata["repository_commit"],
            repository_dirty=metadata["repository_dirty"],
            package_version=metadata["package_version"],
            python_version=metadata["python_version"],
            platform=metadata["platform"],
            command=command,
            tokenizer_id=metadata["tokenizer_id"],
            model_id=metadata["model_id"],
            schema_versions=schema_versions,
            baseline_revisions=baseline_revisions,
            model_service_cost_usd=metadata["model_service_cost_usd"],
            failures=failures,
        )
    except (TypeError, ValueError) as exc:
        raise BenchmarkReportError(f"{context} is invalid: {exc}") from exc

    expected_schemas = {
        "report": report_schema,
        "corpus": CORPUS_SCHEMA,
        "candidate": CANDIDATE_SCHEMA,
        "corpus_producer": CORPUS_PRODUCER_SCHEMA,
        "candidate_producer": CANDIDATE_PRODUCER_SCHEMA,
    }
    actual_schemas = {item.name: item.revision for item in decoded.schema_versions}
    if actual_schemas != expected_schemas:
        raise BenchmarkReportError(
            f"{context}.schema_versions does not match the report envelope"
        )
    expected_baselines = {"compiler", *compared_baselines}
    actual_baselines = {item.name for item in decoded.baseline_revisions}
    if actual_baselines != expected_baselines:
        raise BenchmarkReportError(
            f"{context}.baseline_revisions does not cover the comparison set"
        )
    if decoded.tokenizer_id != TOKENIZER_ID:
        raise BenchmarkReportError(
            f"{context}.tokenizer_id must be {TOKENIZER_ID!r}"
        )
    missing_reasons = [reason for reason in certificate_reasons if reason not in failures]
    if missing_reasons:
        raise BenchmarkReportError(
            f"{context}.failures omits certificate reasons"
        )
    if not external_baselines and (
        decoded.model_id != "deterministic-no-model"
        or decoded.model_service_cost_usd != 0.0
    ):
        raise BenchmarkReportError(
            f"{context} must record the deterministic no-model identity and zero cost"
        )
    if external_baselines and decoded.model_id == "deterministic-no-model":
        raise BenchmarkReportError(
            f"{context}.model_id cannot claim no model for an external comparison"
        )
    return decoded


def _validate_comparison(
    value: object,
    *,
    index: int,
    limits: BenchmarkReportLimits,
) -> dict[str, Any]:
    context = f"report.certificate.comparisons[{index}]"
    comparison = _object(value, context)
    _exact_keys(comparison, _COMPARISON_FIELDS, context)
    _system_name(comparison["system"], f"{context}.system")
    _boolean(comparison["external"], f"{context}.external")
    decision = _string(comparison["decision"], f"{context}.decision")
    if decision not in {"invalid", "win", "loss", "tie"}:
        raise BenchmarkReportError(f"{context}.decision is unsupported")
    gain_basis = comparison["gain_basis"]
    if gain_basis is not None and gain_basis not in _GAIN_BASES:
        raise BenchmarkReportError(f"{context}.gain_basis is unsupported")
    for name in (
        "critical_semantic_loss_reduction",
        "memory_quality_efficiency_gain",
        "critical_loss_margin_lower",
        "memory_quality_efficiency_margin_lower",
    ):
        _optional_number(comparison[name], f"{context}.{name}")
    _string_array(comparison["reasons"], f"{context}.reasons", limits=limits)
    return comparison


def _validate_certificate(
    value: object,
    *,
    report_schema: str,
    dataset_sha256: str,
    config: BenchmarkConfig,
    system_summaries: dict[str, dict[str, Any]],
    limits: BenchmarkReportLimits,
) -> tuple[dict[str, Any], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    context = "report.certificate"
    certificate = _object(value, context)
    expected_fields = (
        _CERTIFICATE_FIELDS
        if report_schema == REPORT_SCHEMA
        else _LEGACY_CERTIFICATE_FIELDS
    )
    _exact_keys(certificate, expected_fields, context)
    issued = _boolean(certificate["issued"], f"{context}.issued")
    candidate = _system_name(certificate["candidate"], f"{context}.candidate")
    strongest = _system_name(
        certificate["strongest_baseline"],
        f"{context}.strongest_baseline",
    )
    if candidate not in system_summaries:
        raise BenchmarkReportError(f"{context}.candidate is absent from systems")
    if candidate != "compiler":
        raise BenchmarkReportError(f"{context}.candidate must be 'compiler'")
    if strongest not in system_summaries:
        raise BenchmarkReportError(f"{context}.strongest_baseline is absent from systems")
    candidate_quality = _number(
        certificate["candidate_quality"],
        f"{context}.candidate_quality",
        minimum=0.0,
        maximum=1.0,
    )
    baseline_quality = _number(
        certificate["baseline_quality"],
        f"{context}.baseline_quality",
        minimum=0.0,
        maximum=1.0,
    )
    _close(
        candidate_quality,
        float(system_summaries[candidate]["quality_score"]),
        f"{context}.candidate_quality",
    )
    _close(
        baseline_quality,
        float(system_summaries[strongest]["quality_score"]),
        f"{context}.baseline_quality",
    )
    gain_basis = certificate["gain_basis"]
    if gain_basis is not None:
        _string(gain_basis, f"{context}.gain_basis")
    for name in (
        "critical_semantic_loss_reduction",
        "memory_quality_efficiency_gain",
        "critical_loss_margin_lower",
        "memory_quality_efficiency_margin_lower",
    ):
        _optional_number(certificate[name], f"{context}.{name}")
    bootstrap_quantile = _number(
        certificate["bootstrap_lower_quantile"],
        f"{context}.bootstrap_lower_quantile",
        minimum=0.0,
        maximum=0.5,
    )
    _close(
        bootstrap_quantile,
        float(config.bootstrap_lower_quantile),
        f"{context}.bootstrap_lower_quantile",
    )
    _sha256(certificate["evidence_sha256"], f"{context}.evidence_sha256")
    scope = _string(certificate["scope"], f"{context}.scope")
    compared = _string_array(
        certificate["compared_baselines"],
        f"{context}.compared_baselines",
        limits=limits,
        unique=True,
    )
    external = _string_array(
        certificate["external_baselines"],
        f"{context}.external_baselines",
        limits=limits,
        unique=True,
    )
    if compared != tuple(sorted(compared)):
        raise BenchmarkReportError(f"{context}.compared_baselines must be sorted")
    if external != tuple(sorted(external)):
        raise BenchmarkReportError(f"{context}.external_baselines must be sorted")
    if not set(external) <= set(compared):
        raise BenchmarkReportError(f"{context}.external_baselines is not a subset")
    if strongest not in compared:
        raise BenchmarkReportError(f"{context}.strongest_baseline is not compared")
    required_local_baselines = set(BUNDLED_SYSTEMS) - {"compiler"}
    if not required_local_baselines <= set(compared):
        raise BenchmarkReportError(f"{context}.compared_baselines omits bundled baselines")
    if candidate in compared:
        raise BenchmarkReportError(f"{context}.candidate cannot also be a baseline")
    if report_schema == LEGACY_REPORT_SCHEMA:
        if external:
            raise BenchmarkReportError(
                f"{context} legacy schema cannot carry external-inclusive claims"
            )
    else:
        raw_protocol = certificate["external_protocol"]
        if not external:
            if raw_protocol is not None:
                raise BenchmarkReportError(
                    f"{context}.external_protocol requires external baselines"
                )
        else:
            protocol_context = f"{context}.external_protocol"
            protocol = _object(raw_protocol, protocol_context)
            _exact_keys(
                protocol,
                _EXTERNAL_PROTOCOL_FIELDS,
                protocol_context,
            )
            protocol_id = _string(
                protocol["protocol_id"],
                f"{protocol_context}.protocol_id",
            )
            if (
                _SYSTEM_RE.fullmatch(protocol_id) is None
                or protocol_id != protocol_id.lower()
            ):
                raise BenchmarkReportError(
                    f"{protocol_context}.protocol_id is invalid"
                )
            _sha256(
                protocol["protocol_sha256"],
                f"{protocol_context}.protocol_sha256",
            )
            _sha256(
                protocol["document_sha256"],
                f"{protocol_context}.document_sha256",
            )
            synthetic_dataset_sha256 = _sha256(
                protocol["synthetic_dataset_sha256"],
                f"{protocol_context}.synthetic_dataset_sha256",
            )
            if synthetic_dataset_sha256 != dataset_sha256:
                raise BenchmarkReportError(
                    f"{protocol_context}.synthetic_dataset_sha256 does not "
                    "match the report dataset"
                )
            registered = _string_array(
                protocol["registered_systems"],
                f"{protocol_context}.registered_systems",
                limits=limits,
                unique=True,
            )
            if (
                len(registered) < 4
                or registered != tuple(sorted(registered))
                or any(system != system.lower() for system in registered)
            ):
                raise BenchmarkReportError(
                    f"{protocol_context}.registered_systems must contain at "
                    "least four unique lowercase sorted systems"
                )
            if registered != external:
                raise BenchmarkReportError(
                    f"{protocol_context}.registered_systems does not match "
                    "external_baselines"
                )

    raw_comparisons = _array(
        certificate["comparisons"],
        f"{context}.comparisons",
        limit=limits.max_systems,
    )
    comparisons = [
        _validate_comparison(item, index=index, limits=limits)
        for index, item in enumerate(raw_comparisons)
    ]
    comparison_names = tuple(item["system"] for item in comparisons)
    if comparison_names != compared:
        raise BenchmarkReportError(f"{context}.comparisons does not match compared_baselines")
    for comparison in comparisons:
        if comparison["external"] != (comparison["system"] in external):
            raise BenchmarkReportError(
                f"{context}.comparisons has inconsistent external membership"
            )
        if comparison["decision"] == "win" and comparison["gain_basis"] is None:
            raise BenchmarkReportError(
                f"{context}.comparisons has a win without a gain basis"
            )

    strongest_expected = max(
        (
            summary
            for name, summary in system_summaries.items()
            if name != candidate
        ),
        key=lambda summary: (
            summary["critical_atom_recall"],
            summary["exact_literal_recall"],
            summary["quality_score"],
            summary["system"],
        ),
    )["system"]
    if strongest != strongest_expected:
        raise BenchmarkReportError(f"{context}.strongest_baseline is inconsistent")
    frontier = next(
        comparison for comparison in comparisons if comparison["system"] == strongest
    )
    for name in (
        "critical_semantic_loss_reduction",
        "memory_quality_efficiency_gain",
        "critical_loss_margin_lower",
        "memory_quality_efficiency_margin_lower",
    ):
        _optional_close(
            certificate[name],
            frontier[name],
            f"{context}.{name}",
        )

    external_wins = _integer(certificate["external_wins"], f"{context}.external_wins")
    required_wins = _integer(
        certificate["external_required_wins"],
        f"{context}.external_required_wins",
    )
    expected_wins = sum(
        comparison["decision"] == "win"
        for comparison in comparisons
        if comparison["external"]
    )
    expected_required = len(external) // 2 + 1 if external else 0
    if external_wins != expected_wins or required_wins != expected_required:
        raise BenchmarkReportError(f"{context} has inconsistent external win accounting")
    majority = certificate["external_majority_passed"]
    expected_majority = external_wins >= required_wins if external else None
    if majority is not None:
        _boolean(majority, f"{context}.external_majority_passed")
    if majority != expected_majority:
        raise BenchmarkReportError(f"{context}.external_majority_passed is inconsistent")
    expected_scope = "external-inclusive" if external else "local-bundled-only"
    if scope != expected_scope:
        raise BenchmarkReportError(
            f"{context}.scope must be {expected_scope!r} for this comparison set"
        )
    if external:
        winning_bases = {
            comparison["gain_basis"]
            for comparison in comparisons
            if comparison["external"]
            and comparison["decision"] == "win"
            and comparison["gain_basis"] is not None
        }
        expected_gain_basis = "+".join(sorted(winning_bases)) or None
    else:
        expected_gain_basis = frontier["gain_basis"]
    if gain_basis != expected_gain_basis:
        raise BenchmarkReportError(f"{context}.gain_basis is inconsistent")

    raw_manifests = _array(
        certificate["external_manifests"],
        f"{context}.external_manifests",
        limit=limits.max_systems,
    )
    manifest_systems: list[str] = []
    for index, raw_manifest in enumerate(raw_manifests):
        item_context = f"{context}.external_manifests[{index}]"
        manifest = _object(raw_manifest, item_context)
        _exact_keys(
            manifest,
            {"system", "manifest_sha256", "failure_reason"},
            item_context,
        )
        system = _system_name(manifest["system"], f"{item_context}.system")
        if system not in external:
            raise BenchmarkReportError(f"{item_context}.system is not externally compared")
        manifest_systems.append(system)
        _sha256(manifest["manifest_sha256"], f"{item_context}.manifest_sha256")
        if manifest["failure_reason"] is not None:
            _string(manifest["failure_reason"], f"{item_context}.failure_reason")
    if manifest_systems != sorted(set(manifest_systems)):
        raise BenchmarkReportError(
            f"{context}.external_manifests must be uniquely sorted by system"
        )

    reasons = _string_array(
        certificate["reasons"],
        f"{context}.reasons",
        limits=limits,
    )
    if issued != (not reasons):
        raise BenchmarkReportError(f"{context}.issued is inconsistent with reasons")
    _string(certificate["claim"], f"{context}.claim")
    return certificate, compared, external, reasons


def _evidence_document(
    *,
    report: dict[str, Any],
    system_summaries: dict[str, dict[str, Any]],
    certificate: dict[str, Any],
) -> dict[str, object]:
    evidence: dict[str, object] = {
        "benchmark": report["benchmark"],
        "config": report["config"],
        "dataset": report["dataset_sha256"],
        "scope": certificate["scope"],
        "candidate": system_summaries[certificate["candidate"]],
        "systems": [
            system_summaries[name] for name in sorted(system_summaries)
        ],
        "comparisons": certificate["comparisons"],
        "external_comparison_set": certificate["external_baselines"],
        "external_wins": certificate["external_wins"],
        "external_required_wins": certificate["external_required_wins"],
        "external_run_manifests": certificate["external_manifests"],
    }
    if report["report_schema"] == REPORT_SCHEMA:
        evidence["external_protocol"] = certificate["external_protocol"]
    return evidence


def _certificate_evidence_sha256(value: object) -> str:
    """Match the schema-selected LRCBench evidence serialization exactly."""

    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def verify_benchmark_report(
    payload: object,
    *,
    source_label: str = "<report>",
    limits: BenchmarkReportLimits | None = None,
) -> VerifiedBenchmarkReport:
    """Verify a decoded current or retained local legacy report."""

    resolved_limits = limits or DEFAULT_BENCHMARK_REPORT_LIMITS
    if not isinstance(resolved_limits, BenchmarkReportLimits):
        raise TypeError("limits must be a BenchmarkReportLimits value")
    report = _object(payload, source_label)
    _exact_keys(report, _TOP_LEVEL_FIELDS, source_label)
    report_digest = _sha256(report["report_sha256"], f"{source_label}.report_sha256")
    unsigned = dict(report)
    unsigned.pop("report_sha256")
    try:
        actual_report_digest = _canonical_sha256(unsigned)
    except (TypeError, ValueError) as exc:
        raise BenchmarkReportError(
            f"{source_label} is not canonical finite JSON"
        ) from exc
    if actual_report_digest != report_digest:
        raise BenchmarkReportError(f"{source_label}.report_sha256 mismatch")

    report_schema = _string(
        report["report_schema"],
        f"{source_label}.report_schema",
    )
    if report_schema not in {REPORT_SCHEMA, LEGACY_REPORT_SCHEMA}:
        raise BenchmarkReportError(
            f"{source_label}.report_schema must be {REPORT_SCHEMA!r} or "
            f"{LEGACY_REPORT_SCHEMA!r}"
        )
    expected_identifiers = {
        "benchmark": BENCHMARK_VERSION,
        "corpus_schema": CORPUS_SCHEMA,
        "candidate_schema": CANDIDATE_SCHEMA,
    }
    for name, expected in expected_identifiers.items():
        if report[name] != expected:
            raise BenchmarkReportError(f"{source_label}.{name} must be {expected!r}")
    recorded_dataset_digest = _sha256(
        report["dataset_sha256"],
        f"{source_label}.dataset_sha256",
    )

    raw_config = _object(report["config"], f"{source_label}.config")
    expected_config_fields = set(BenchmarkConfig.__dataclass_fields__)
    _exact_keys(raw_config, expected_config_fields, f"{source_label}.config")
    try:
        config = BenchmarkConfig(**raw_config)
    except (TypeError, ValueError) as exc:
        raise BenchmarkReportError(f"{source_label}.config is invalid: {exc}") from exc
    source_events = config.histories * config.messages_per_history
    noise_lines = source_events * config.noise_lines_per_message
    if source_events > resolved_limits.max_generated_source_events:
        raise BenchmarkReportError(
            f"{source_label}.config would generate more than "
            f"{resolved_limits.max_generated_source_events} source events"
        )
    if noise_lines > resolved_limits.max_generated_noise_lines:
        raise BenchmarkReportError(
            f"{source_label}.config would generate more than "
            f"{resolved_limits.max_generated_noise_lines} noise lines"
        )
    generated_histories = generate_histories(config)
    actual_dataset_digest = dataset_digest(generated_histories, config)
    if actual_dataset_digest != recorded_dataset_digest:
        raise BenchmarkReportError(f"{source_label}.dataset_sha256 mismatch")
    expected_history_sources = tuple(
        (
            case.id,
            estimate_tokens("\n".join(source.content for source in case.sources)),
        )
        for case in generated_histories
    )

    raw_systems = _array(
        report["systems"],
        f"{source_label}.systems",
        limit=resolved_limits.max_systems,
    )
    if not raw_systems:
        raise BenchmarkReportError(f"{source_label}.systems cannot be empty")
    system_summaries: dict[str, dict[str, Any]] = {}
    history_ids: tuple[str, ...] | None = None
    history_presence: set[bool] = set()
    source_token_totals: set[int] = set()
    for index, raw_system in enumerate(raw_systems):
        system, case_ids = _validate_system(
            raw_system,
            index=index,
            config=config,
            expected_history_sources=expected_history_sources,
            limits=resolved_limits,
        )
        name = system["system"]
        if name in system_summaries:
            raise BenchmarkReportError(f"{source_label}.systems has duplicate {name!r}")
        summary = dict(system)
        summary.pop("per_history", None)
        system_summaries[name] = summary
        history_presence.add(case_ids is not None)
        source_token_totals.add(system["source_tokens"])
        if case_ids is not None:
            if history_ids is None:
                history_ids = case_ids
            elif case_ids != history_ids:
                raise BenchmarkReportError(
                    f"{source_label}.systems has inconsistent per_history case order"
                )
    if len(history_presence) != 1:
        raise BenchmarkReportError(
            f"{source_label}.systems must either all include or all omit per_history"
        )
    if len(source_token_totals) != 1:
        raise BenchmarkReportError(
            f"{source_label}.systems has inconsistent source token totals"
        )
    missing_bundled = set(BUNDLED_SYSTEMS) - system_summaries.keys()
    if missing_bundled:
        raise BenchmarkReportError(
            f"{source_label}.systems omits bundled systems: "
            + ", ".join(sorted(missing_bundled))
        )
    expected_system_order = (
        *BUNDLED_SYSTEMS,
        *sorted(system_summaries.keys() - set(BUNDLED_SYSTEMS)),
    )
    if tuple(system_summaries) != expected_system_order:
        raise BenchmarkReportError(
            f"{source_label}.systems is not in canonical system order"
        )

    certificate, compared, external, reasons = _validate_certificate(
        report["certificate"],
        report_schema=report_schema,
        dataset_sha256=recorded_dataset_digest,
        config=config,
        system_summaries=system_summaries,
        limits=resolved_limits,
    )
    expected_system_names = {"compiler", *compared} - set(external)
    if not expected_system_names <= system_summaries.keys():
        raise BenchmarkReportError(
            f"{source_label}.systems omits one or more non-external comparisons"
        )
    unexpected_systems = system_summaries.keys() - {"compiler", *compared}
    if unexpected_systems:
        raise BenchmarkReportError(
            f"{source_label}.systems has unregistered systems: "
            + ", ".join(sorted(unexpected_systems))
        )
    evidence_digest = _sha256(
        certificate["evidence_sha256"],
        f"{source_label}.certificate.evidence_sha256",
    )
    actual_evidence_digest = _certificate_evidence_sha256(
        _evidence_document(
            report=report,
            system_summaries=system_summaries,
            certificate=certificate,
        )
    )
    if actual_evidence_digest != evidence_digest:
        raise BenchmarkReportError(
            f"{source_label}.certificate.evidence_sha256 mismatch"
        )

    metadata = _validate_run_metadata(
        report["run_metadata"],
        report_schema=report_schema,
        certificate_reasons=reasons,
        compared_baselines=compared,
        external_baselines=external,
        limits=resolved_limits,
    )
    return VerifiedBenchmarkReport(
        report_sha256=report_digest,
        evidence_sha256=evidence_digest,
        dataset_sha256=recorded_dataset_digest,
        benchmark=BENCHMARK_VERSION,
        systems=tuple(system_summaries),
        histories=config.histories,
        per_history_included=history_ids is not None,
        certificate_issued=certificate["issued"],
        certificate_scope=certificate["scope"],
        repository_commit=metadata.repository_commit,
        repository_dirty=metadata.repository_dirty,
        model_id=metadata.model_id,
        model_service_cost_usd=(
            None
            if metadata.model_service_cost_usd is None
            else float(metadata.model_service_cost_usd)
        ),
    )


def load_benchmark_report(
    path: str | Path,
    *,
    limits: BenchmarkReportLimits | None = None,
) -> VerifiedBenchmarkReport:
    """Load and verify a current or retained local legacy report."""

    resolved_limits = limits or DEFAULT_BENCHMARK_REPORT_LIMITS
    if not isinstance(resolved_limits, BenchmarkReportLimits):
        raise TypeError("limits must be a BenchmarkReportLimits value")
    report_path = Path(path)
    try:
        document = load_strict_json_file(
            report_path,
            limits=StrictJsonLimits(
                max_bytes=resolved_limits.max_input_bytes,
                max_line_chars=resolved_limits.max_line_chars,
                max_depth=resolved_limits.max_json_depth,
            ),
            label="benchmark report",
        )
    except StrictJsonError as exc:
        raise BenchmarkReportError(str(exc)) from exc
    return verify_benchmark_report(
        document.value,
        source_label=str(report_path),
        limits=resolved_limits,
    )
