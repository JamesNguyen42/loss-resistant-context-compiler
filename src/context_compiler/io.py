"""Input/output helpers for portable context artifacts."""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import stat
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TextIO

from .extractors import RuleBasedExtractor
from .limits import (
    ArtifactLimitError,
    ArtifactLimits,
    SourceLimitError,
    SourceLimits,
    add_source_size,
    resolve_artifact_limits,
    resolve_source_limits,
    source_value_size,
    validate_artifact_value,
)
from .models import (
    ADDITIVE_SAFETY_EXTRACTOR_FAILED_MESSAGE,
    COMPILATION_METRICS_SCHEMA,
    PRIMARY_EXTRACTOR_DEGRADED_MESSAGE,
    PRIMARY_EXTRACTOR_FAILED_MESSAGE,
    SCHEMA_VERSION,
    CompilationMetrics,
    IssueSeverity,
    MemoryItem,
    MemoryKind,
    MemoryStatus,
    SourceRecord,
    VerificationIssue,
    render_typed_memory,
    source_digest,
)
from .verifier import verify_memory

_ARTIFACT_FIELDS = frozenset(
    {
        "schema_version",
        "ledger_complete",
        "compiled_at",
        "source_digest",
        "source_count",
        "items",
        "selected_item_ids",
        "verification",
        "compression",
        "compiler_metadata",
        "artifact_sha256",
    }
)
_PATH_OPEN_ATTEMPTS = 3
_ITEM_FIELDS = frozenset(
    {
        "id",
        "kind",
        "text",
        "status",
        "priority",
        "confidence",
        "exact",
        "protected",
        "tags",
        "supersedes",
        "conflicts_with",
        "metadata",
        "provenance",
    }
)
_PROVENANCE_FIELDS = frozenset(
    {"source_id", "start", "end", "quote", "quote_sha256"}
)
_VERIFICATION_FIELDS = frozenset(
    {
        "passed",
        "protected_candidates",
        "protected_retained",
        "protected_recall",
        "provenance_valid",
        "provenance_total",
        "provenance_validity",
        "recovered_items",
        "issues",
    }
)
_VERIFICATION_ISSUE_FIELDS = frozenset(
    {"code", "severity", "message", "item_id", "source_id"}
)
_COMPRESSION_FIELDS = frozenset(
    {
        "source_chars",
        "active_chars",
        "source_tokens_estimate",
        "active_tokens_estimate",
        "compression_ratio",
        "token_budget",
        "budget_overflow",
        "target_met",
    }
)
_POLICY_FIELDS = frozenset(
    {
        "token_budget",
        "minimum_compression_ratio",
        "chars_per_token",
        "token_counter",
        "include_superseded",
        "include_discarded",
    }
)
_SOURCE_LIMIT_FIELDS = frozenset(
    {
        "max_input_bytes",
        "max_records",
        "max_line_chars",
        "max_record_bytes",
        "max_total_record_bytes",
        "max_json_depth",
    }
)
_COMPILATION_METRICS_FIELDS = frozenset(
    {
        "schema",
        "source_records",
        "primary_extracted_items",
        "recovery_candidate_items",
        "certification_candidate_items",
        "recovery_added_items",
        "resolved_items",
        "selected_items",
        "active_items",
        "superseded_items",
        "discarded_items",
        "conflicting_items",
        "detected_conflicts",
        "protected_items",
        "protected_selected_items",
        "protected_prompt_tokens",
        "protected_budget_overflow",
        "verification_error_count",
        "verification_warning_count",
        "verification_info_count",
        "compile_duration_seconds",
    }
)


def _shape_issue(
    issues: list[dict[str, Any]],
    code: str,
    message: str,
    *,
    item_id: str | None = None,
) -> None:
    issues.append({"code": code, "item_id": item_id, "message": message})


def _is_integer(value: Any, *, minimum: int = 0, maximum: int | None = None) -> bool:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        return False
    return maximum is None or value <= maximum


def _is_finite_number(
    value: Any,
    *,
    minimum: float,
    maximum: float | None = None,
) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        converted = float(value)
    except (OverflowError, ValueError):
        return False
    if not math.isfinite(converted) or converted < minimum:
        return False
    return maximum is None or converted <= maximum


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _check_exact_fields(
    value: dict[str, Any],
    expected: frozenset[str],
    *,
    label: str,
    code: str,
    issues: list[dict[str, Any]],
    item_id: str | None = None,
) -> None:
    missing = sorted(expected - value.keys())
    unknown = sorted((key for key in value if key not in expected), key=repr)
    if missing:
        _shape_issue(
            issues,
            code,
            f"{label} is missing required fields: {', '.join(missing)}.",
            item_id=item_id,
        )
    if unknown:
        _shape_issue(
            issues,
            code,
            f"{label} contains unknown fields: {', '.join(map(repr, unknown))}.",
            item_id=item_id,
        )


def _validate_provenance_shape(
    value: Any,
    *,
    item_id: str | None,
    index: int,
    issues: list[dict[str, Any]],
) -> None:
    label = f"Memory item provenance[{index}]"
    if not isinstance(value, dict):
        _shape_issue(
            issues,
            "invalid_provenance_shape",
            f"{label} must be a JSON object.",
            item_id=item_id,
        )
        return
    _check_exact_fields(
        value,
        _PROVENANCE_FIELDS,
        label=label,
        code="invalid_provenance_shape",
        issues=issues,
        item_id=item_id,
    )
    if not isinstance(value.get("source_id"), str) or not value.get("source_id"):
        _shape_issue(
            issues,
            "invalid_provenance_shape",
            f"{label}.source_id must be a non-empty string.",
            item_id=item_id,
        )
    start = value.get("start")
    end = value.get("end")
    if not _is_integer(start):
        _shape_issue(
            issues,
            "invalid_provenance_shape",
            f"{label}.start must be a non-negative integer.",
            item_id=item_id,
        )
    if not _is_integer(end):
        _shape_issue(
            issues,
            "invalid_provenance_shape",
            f"{label}.end must be a non-negative integer.",
            item_id=item_id,
        )
    if _is_integer(start) and _is_integer(end) and end < start:
        _shape_issue(
            issues,
            "invalid_provenance_shape",
            f"{label}.end cannot precede start.",
            item_id=item_id,
        )
    if not isinstance(value.get("quote"), str):
        _shape_issue(
            issues,
            "invalid_provenance_shape",
            f"{label}.quote must be a string.",
            item_id=item_id,
        )
    if not _is_sha256(value.get("quote_sha256")):
        _shape_issue(
            issues,
            "invalid_provenance_shape",
            f"{label}.quote_sha256 must be a lowercase SHA-256 digest.",
            item_id=item_id,
        )


def _validate_item_shape(
    value: Any,
    *,
    index: int,
    issues: list[dict[str, Any]],
) -> None:
    if not isinstance(value, dict):
        _shape_issue(
            issues,
            "invalid_item_shape",
            f"Artifact items[{index}] must be a JSON object.",
        )
        return
    raw_id = value.get("id")
    item_id = raw_id if isinstance(raw_id, str) else None
    label = f"Artifact items[{index}]"
    _check_exact_fields(
        value,
        _ITEM_FIELDS,
        label=label,
        code="invalid_item_shape",
        issues=issues,
        item_id=item_id,
    )
    if not isinstance(raw_id, str) or not raw_id:
        _shape_issue(
            issues,
            "invalid_item_shape",
            f"{label}.id must be a non-empty string.",
            item_id=item_id,
        )
    if not isinstance(value.get("text"), str) or not value.get("text", "").strip():
        _shape_issue(
            issues,
            "invalid_item_shape",
            f"{label}.text must be a non-empty string.",
            item_id=item_id,
        )
    if not _is_integer(value.get("priority"), maximum=100):
        _shape_issue(
            issues,
            "invalid_item_shape",
            f"{label}.priority must be an integer from 0 through 100.",
            item_id=item_id,
        )
    if not _is_finite_number(value.get("confidence"), minimum=0, maximum=1):
        _shape_issue(
            issues,
            "invalid_item_shape",
            f"{label}.confidence must be a finite number from 0 through 1.",
            item_id=item_id,
        )
    for name in ("exact", "protected"):
        if not isinstance(value.get(name), bool):
            _shape_issue(
                issues,
                "invalid_item_shape",
                f"{label}.{name} must be a boolean.",
                item_id=item_id,
            )
    for name in ("tags", "supersedes", "conflicts_with"):
        collection = value.get(name)
        if not isinstance(collection, list) or not all(
            isinstance(entry, str) for entry in collection
        ):
            _shape_issue(
                issues,
                "invalid_item_shape",
                f"{label}.{name} must be an array of strings.",
                item_id=item_id,
            )
    if not isinstance(value.get("metadata"), dict):
        _shape_issue(
            issues,
            "invalid_item_shape",
            f"{label}.metadata must be a JSON object.",
            item_id=item_id,
        )
    provenance = value.get("provenance")
    if not isinstance(provenance, list) or not provenance:
        _shape_issue(
            issues,
            "invalid_item_shape",
            f"{label}.provenance must be a non-empty array.",
            item_id=item_id,
        )
    else:
        for provenance_index, span in enumerate(provenance):
            _validate_provenance_shape(
                span,
                item_id=item_id,
                index=provenance_index,
                issues=issues,
            )


def _validate_verification_shape(
    value: Any,
    issues: list[dict[str, Any]],
) -> None:
    if not isinstance(value, dict):
        return
    _check_exact_fields(
        value,
        _VERIFICATION_FIELDS,
        label="Artifact verification",
        code="invalid_verification_shape",
        issues=issues,
    )
    if not isinstance(value.get("passed"), bool):
        _shape_issue(
            issues,
            "invalid_verification_shape",
            "Artifact verification.passed must be a boolean.",
        )
    for name in (
        "protected_candidates",
        "protected_retained",
        "provenance_valid",
        "provenance_total",
        "recovered_items",
    ):
        if not _is_integer(value.get(name)):
            _shape_issue(
                issues,
                "invalid_verification_shape",
                f"Artifact verification.{name} must be a non-negative integer.",
            )
    for name in ("protected_recall", "provenance_validity"):
        if not _is_finite_number(value.get(name), minimum=0, maximum=1):
            _shape_issue(
                issues,
                "invalid_verification_shape",
                f"Artifact verification.{name} must be a finite number from 0 through 1.",
            )
    raw_issues = value.get("issues")
    if not isinstance(raw_issues, list):
        _shape_issue(
            issues,
            "invalid_verification_shape",
            "Artifact verification.issues must be a JSON array.",
        )
        return
    for index, raw_issue in enumerate(raw_issues):
        label = f"Artifact verification.issues[{index}]"
        if not isinstance(raw_issue, dict):
            _shape_issue(
                issues,
                "invalid_verification_issue_shape",
                f"{label} must be a JSON object.",
            )
            continue
        _check_exact_fields(
            raw_issue,
            _VERIFICATION_ISSUE_FIELDS,
            label=label,
            code="invalid_verification_issue_shape",
            issues=issues,
        )
        if not isinstance(raw_issue.get("code"), str):
            _shape_issue(
                issues,
                "invalid_verification_issue_shape",
                f"{label}.code must be a string.",
            )
        if raw_issue.get("severity") not in {"error", "warning", "info"}:
            _shape_issue(
                issues,
                "invalid_verification_issue_shape",
                f"{label}.severity must be error, warning, or info.",
            )
        if not isinstance(raw_issue.get("message"), str):
            _shape_issue(
                issues,
                "invalid_verification_issue_shape",
                f"{label}.message must be a string.",
            )
        for name in ("item_id", "source_id"):
            identifier = raw_issue.get(name)
            if identifier is not None and not isinstance(identifier, str):
                _shape_issue(
                    issues,
                    "invalid_verification_issue_shape",
                    f"{label}.{name} must be a string or null.",
                )


def _validate_compression_shape(
    value: Any,
    issues: list[dict[str, Any]],
) -> None:
    if not isinstance(value, dict):
        return
    _check_exact_fields(
        value,
        _COMPRESSION_FIELDS,
        label="Artifact compression",
        code="invalid_compression_shape",
        issues=issues,
    )
    for name in (
        "source_chars",
        "active_chars",
        "source_tokens_estimate",
        "active_tokens_estimate",
        "budget_overflow",
    ):
        if not _is_integer(value.get(name)):
            _shape_issue(
                issues,
                "invalid_compression_shape",
                f"Artifact compression.{name} must be a non-negative integer.",
            )
    if not _is_integer(value.get("token_budget"), minimum=1):
        _shape_issue(
            issues,
            "invalid_compression_shape",
            "Artifact compression.token_budget must be a positive integer.",
        )
    if not _is_finite_number(value.get("compression_ratio"), minimum=0):
        _shape_issue(
            issues,
            "invalid_compression_shape",
            "Artifact compression.compression_ratio must be a finite non-negative number.",
        )
    if not isinstance(value.get("target_met"), bool):
        _shape_issue(
            issues,
            "invalid_compression_shape",
            "Artifact compression.target_met must be a boolean.",
        )


def _validate_policy_shape(
    compiler_metadata: Any,
    issues: list[dict[str, Any]],
) -> None:
    if not isinstance(compiler_metadata, dict):
        return
    policy = compiler_metadata.get("policy")
    if not isinstance(policy, dict):
        _shape_issue(
            issues,
            "invalid_policy_shape",
            "Artifact compiler_metadata.policy must be a JSON object.",
        )
        return
    _check_exact_fields(
        policy,
        _POLICY_FIELDS,
        label="Artifact compiler_metadata.policy",
        code="invalid_policy_shape",
        issues=issues,
    )
    if not _is_integer(policy.get("token_budget"), minimum=1):
        _shape_issue(
            issues,
            "invalid_policy_shape",
            "Artifact compiler_metadata.policy.token_budget must be a positive integer.",
        )
    if not _is_finite_number(
        policy.get("minimum_compression_ratio"),
        minimum=1,
    ):
        _shape_issue(
            issues,
            "invalid_policy_shape",
            (
                "Artifact compiler_metadata.policy.minimum_compression_ratio "
                "must be a finite number of at least 1."
            ),
        )
    if not _is_finite_number(
        policy.get("chars_per_token"), minimum=0
    ) or float(policy["chars_per_token"]) == 0:
        _shape_issue(
            issues,
            "invalid_policy_shape",
            (
                "Artifact compiler_metadata.policy.chars_per_token must be "
                "a finite positive number."
            ),
        )
    if not isinstance(policy.get("token_counter"), str) or not policy.get(
        "token_counter"
    ):
        _shape_issue(
            issues,
            "invalid_policy_shape",
            "Artifact compiler_metadata.policy.token_counter must be a non-empty string.",
        )
    for name in ("include_superseded", "include_discarded"):
        if not isinstance(policy.get(name), bool):
            _shape_issue(
                issues,
                "invalid_policy_shape",
                f"Artifact compiler_metadata.policy.{name} must be a boolean.",
            )


def _validate_source_limits_shape(
    compiler_metadata: Any,
    issues: list[dict[str, Any]],
) -> None:
    if not isinstance(compiler_metadata, dict):
        return
    source_limits = compiler_metadata.get("source_limits")
    if source_limits is None:
        return
    if not isinstance(source_limits, dict):
        _shape_issue(
            issues,
            "invalid_source_limits",
            "Artifact compiler_metadata.source_limits must be a JSON object.",
        )
        return
    _check_exact_fields(
        source_limits,
        _SOURCE_LIMIT_FIELDS,
        label="Artifact compiler_metadata.source_limits",
        code="invalid_source_limits",
        issues=issues,
    )
    for name in sorted(_SOURCE_LIMIT_FIELDS):
        if not _is_integer(source_limits.get(name), minimum=1):
            _shape_issue(
                issues,
                "invalid_source_limits",
                (
                    f"Artifact compiler_metadata.source_limits.{name} "
                    "must be a positive integer."
                ),
            )


def _validate_compilation_metrics_shape(
    compiler_metadata: Any,
    issues: list[dict[str, Any]],
) -> bool:
    """Validate optional versioned metrics without making them mandatory."""

    if not isinstance(compiler_metadata, dict):
        return False
    if "metrics" not in compiler_metadata:
        return True
    metrics = compiler_metadata["metrics"]
    if not isinstance(metrics, dict):
        _shape_issue(
            issues,
            "invalid_compilation_metrics",
            "Artifact compiler_metadata.metrics must be a JSON object.",
        )
        return False
    issue_count = len(issues)
    _check_exact_fields(
        metrics,
        _COMPILATION_METRICS_FIELDS,
        label="Artifact compiler_metadata.metrics",
        code="invalid_compilation_metrics",
        issues=issues,
    )
    if metrics.get("schema") != COMPILATION_METRICS_SCHEMA:
        _shape_issue(
            issues,
            "invalid_compilation_metrics",
            (
                "Artifact compiler_metadata.metrics.schema must be "
                f"{COMPILATION_METRICS_SCHEMA!r}."
            ),
        )
    if len(issues) != issue_count:
        return False
    try:
        CompilationMetrics(
            **{
                key: metrics[key]
                for key in _COMPILATION_METRICS_FIELDS
                if key != "schema"
            }
        )
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        _shape_issue(
            issues,
            "invalid_compilation_metrics",
            f"Artifact compiler_metadata.metrics is invalid: {exc}.",
        )
        return False
    return True


def _validate_artifact_shape(
    artifact: dict[str, Any],
    issues: list[dict[str, Any]],
) -> bool:
    _check_exact_fields(
        artifact,
        _ARTIFACT_FIELDS,
        label="Artifact",
        code="invalid_artifact_shape",
        issues=issues,
    )
    if not isinstance(artifact.get("schema_version"), str):
        _shape_issue(
            issues,
            "invalid_artifact_shape",
            "Artifact schema_version must be a string.",
        )
    if not isinstance(artifact.get("ledger_complete"), bool):
        _shape_issue(
            issues,
            "invalid_artifact_shape",
            "Artifact ledger_complete must be a boolean.",
        )
    compiled_at = artifact.get("compiled_at")
    if not isinstance(compiled_at, str) or not compiled_at.strip():
        _shape_issue(
            issues,
            "invalid_compiled_at",
            "Artifact compiled_at must be a non-empty string.",
        )
    if not _is_sha256(artifact.get("source_digest")):
        _shape_issue(
            issues,
            "invalid_artifact_shape",
            "Artifact source_digest must be a lowercase SHA-256 digest.",
        )
    if not _is_integer(artifact.get("source_count")):
        _shape_issue(
            issues,
            "invalid_artifact_shape",
            "Artifact source_count must be a non-negative integer.",
        )
    if not isinstance(artifact.get("compiler_metadata"), dict):
        _shape_issue(
            issues,
            "invalid_compiler_metadata",
            "Artifact compiler_metadata must be a JSON object.",
        )
    if not _is_sha256(artifact.get("artifact_sha256")):
        _shape_issue(
            issues,
            "invalid_artifact_shape",
            "Artifact artifact_sha256 must be a lowercase SHA-256 digest.",
        )
    raw_items = artifact.get("items")
    if isinstance(raw_items, list):
        for index, raw_item in enumerate(raw_items):
            _validate_item_shape(raw_item, index=index, issues=issues)
    raw_selected = artifact.get("selected_item_ids")
    if isinstance(raw_selected, list) and any(
        not isinstance(item_id, str) or not item_id
        for item_id in raw_selected
    ):
        _shape_issue(
            issues,
            "invalid_selected_item_ids",
            "selected_item_ids must contain only non-empty strings.",
        )
    _validate_verification_shape(artifact.get("verification"), issues)
    _validate_compression_shape(artifact.get("compression"), issues)
    _validate_policy_shape(artifact.get("compiler_metadata"), issues)
    _validate_source_limits_shape(artifact.get("compiler_metadata"), issues)
    return _validate_compilation_metrics_shape(
        artifact.get("compiler_metadata"),
        issues,
    )


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _finite_json_float(value: str) -> float:
    decoded = float(value)
    if not math.isfinite(decoded):
        raise ValueError("JSON number must be finite")
    return decoded


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is forbidden: {value}")


def _validate_json_nesting(
    raw: str,
    *,
    max_depth: int,
    label: str,
    limit_error: type[ValueError],
) -> None:
    depth = 0
    in_string = False
    escaped = False
    for character in raw:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > max_depth:
                raise limit_error(
                    f"{label} exceeds supported nesting depth of {max_depth}"
                )
        elif character in "]}":
            depth = max(0, depth - 1)


def _decode_strict_json(
    raw: str,
    *,
    max_depth: int,
    label: str,
    limit_error: type[ValueError],
) -> Any:
    _validate_json_nesting(
        raw,
        max_depth=max_depth,
        label=label,
        limit_error=limit_error,
    )
    try:
        return json.loads(
            raw,
            object_pairs_hook=_strict_json_object,
            parse_float=_finite_json_float,
            parse_constant=_reject_json_constant,
        )
    except RecursionError as exc:
        raise limit_error(f"{label} exceeds the supported nesting depth") from exc


def _records_from_value(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        if "content" in value:
            return [value]
        for key in ("sources", "events", "messages"):
            candidate = value.get(key)
            if isinstance(candidate, list):
                return candidate
    raise ValueError("expected a JSON list or an object containing sources/events/messages")


def _validate_line_lengths(
    raw: str,
    *,
    max_line_chars: int,
    label: str,
    limit_error: type[ValueError],
) -> None:
    line_number = 1
    line_chars = 0
    previous_was_cr = False
    for character in raw:
        if character == "\r":
            line_number += 1
            line_chars = 0
            previous_was_cr = True
            continue
        if character == "\n":
            if not previous_was_cr:
                line_number += 1
            line_chars = 0
            previous_was_cr = False
            continue
        previous_was_cr = False
        line_chars += 1
        if line_chars > max_line_chars:
            raise limit_error(
                f"{label} line {line_number} exceeds {max_line_chars} characters"
            )


def _read_limited_text(
    stream: TextIO,
    *,
    max_input_bytes: int,
    max_line_chars: int,
    label: str,
    limit_error: type[ValueError],
) -> str:
    chunks: list[str] = []
    total_bytes = 0
    read_size = min(64 * 1024, max_input_bytes + 1)
    while True:
        chunk = stream.read(read_size)
        if not isinstance(chunk, str):
            raise TypeError(f"{label} stream must return text")
        if not chunk:
            break
        try:
            total_bytes += len(chunk.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise ValueError(f"{label} must be valid UTF-8 text") from exc
        if total_bytes > max_input_bytes:
            raise limit_error(f"{label} exceeds {max_input_bytes} UTF-8 bytes")
        chunks.append(chunk)
    raw = "".join(chunks)
    if raw.startswith("\ufeff"):
        raw = raw[1:]
    _validate_line_lengths(
        raw,
        max_line_chars=max_line_chars,
        label=label,
        limit_error=limit_error,
    )
    return raw


def _file_identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _file_content_snapshot(
    value: os.stat_result,
) -> tuple[int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
    )


@contextmanager
def _open_stable_text_path(
    path: str | Path,
    *,
    max_input_bytes: int,
    label: str,
    limit_error: type[ValueError],
    require_single_link: bool = False,
) -> Iterator[TextIO]:
    input_path = Path(path)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    for attempt in range(_PATH_OPEN_ATTEMPTS):
        candidate_stat = input_path.lstat()
        if not stat.S_ISREG(candidate_stat.st_mode):
            raise ValueError(f"{label} path must be a regular file: {input_path}")
        if require_single_link and candidate_stat.st_nlink != 1:
            raise ValueError(f"{label} path must not have hard links: {input_path}")
        try:
            descriptor = os.open(input_path, flags)
        except FileNotFoundError:
            if attempt + 1 < _PATH_OPEN_ATTEMPTS:
                continue
            raise
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ValueError(
                    f"{label} path could not be opened without following links: "
                    f"{input_path}"
                ) from exc
            raise
        try:
            opened_stat = os.fstat(descriptor)
            if not stat.S_ISREG(opened_stat.st_mode):
                raise ValueError(
                    f"{label} path must be a regular file: {input_path}"
                )
            if require_single_link and opened_stat.st_nlink != 1:
                raise ValueError(
                    f"{label} path must not have hard links: {input_path}"
                )
            if _file_identity(candidate_stat) != _file_identity(opened_stat):
                if attempt + 1 < _PATH_OPEN_ATTEMPTS:
                    continue
                raise ValueError(f"{label} path changed while opening: {input_path}")
            if opened_stat.st_size > max_input_bytes:
                raise limit_error(f"{label} exceeds {max_input_bytes} bytes")
            os.set_inheritable(descriptor, False)
            stream = os.fdopen(
                descriptor,
                "r",
                encoding="utf-8",
                newline="",
            )
            descriptor = -1
            with stream:
                yield stream
                final_stat = os.fstat(stream.fileno())
                if _file_content_snapshot(opened_stat) != _file_content_snapshot(
                    final_stat
                ):
                    raise ValueError(
                        f"{label} changed while it was being read: {input_path}"
                    )
            return
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    raise ValueError(f"{label} path changed while opening: {input_path}")


def _read_limited_path_text(
    path: str | Path,
    *,
    max_input_bytes: int,
    max_line_chars: int,
    label: str,
    limit_error: type[ValueError],
    require_single_link: bool = False,
) -> str:
    with _open_stable_text_path(
        path,
        max_input_bytes=max_input_bytes,
        label=label,
        limit_error=limit_error,
        require_single_link=require_single_link,
    ) as stream:
        return _read_limited_text(
            stream,
            max_input_bytes=max_input_bytes,
            max_line_chars=max_line_chars,
            label=label,
            limit_error=limit_error,
        )


def _json_line_records(raw: str, limits: SourceLimits) -> list[Any]:
    records: list[Any] = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        if len(records) >= limits.max_records:
            raise SourceLimitError(
                f"source record count exceeds {limits.max_records} records"
            )
        try:
            records.append(
                _decode_strict_json(
                    line,
                    max_depth=limits.max_json_depth,
                    label="source JSON",
                    limit_error=SourceLimitError,
                )
            )
        except SourceLimitError:
            raise
        except ValueError as exc:
            raise ValueError(f"invalid JSON source record at line {line_number}: {exc}") from exc
    return records


def _convert_source_records(
    records: list[Any],
    limits: SourceLimits,
) -> list[SourceRecord]:
    if len(records) > limits.max_records:
        raise SourceLimitError(
            f"source record count exceeds {limits.max_records} records"
        )
    prepared: list[SourceRecord] = []
    total_size = 0
    for index, record in enumerate(records):
        raw_size = source_value_size(record, limits=limits, index=index)
        source = SourceRecord.from_dict(record, default_sequence=index)
        normalized_size = source_value_size(
            source.to_dict(),
            limits=limits,
            index=index,
        )
        total_size = add_source_size(
            total_size,
            max(raw_size, normalized_size),
            limits=limits,
        )
        prepared.append(source)
    return prepared


def load_sources(
    stream: TextIO,
    *,
    json_lines: bool | None = None,
    limits: SourceLimits | None = None,
) -> list[SourceRecord]:
    """Load OpenAI-style messages or source records from JSON/JSONL."""

    resolved_limits = resolve_source_limits(limits)
    raw = _read_limited_text(
        stream,
        max_input_bytes=resolved_limits.max_input_bytes,
        max_line_chars=resolved_limits.max_line_chars,
        label="source input",
        limit_error=SourceLimitError,
    )
    if not raw.strip():
        return []
    if json_lines is True:
        records = _json_line_records(raw, resolved_limits)
    elif json_lines is False:
        records = _records_from_value(
            _decode_strict_json(
                raw,
                max_depth=resolved_limits.max_json_depth,
                label="source JSON",
                limit_error=SourceLimitError,
            )
        )
    else:
        try:
            records = _records_from_value(
                _decode_strict_json(
                    raw,
                    max_depth=resolved_limits.max_json_depth,
                    label="source JSON",
                    limit_error=SourceLimitError,
                )
            )
        except json.JSONDecodeError:
            records = _json_line_records(raw, resolved_limits)
    return _convert_source_records(records, resolved_limits)


def load_sources_path(
    path: str | Path,
    *,
    limits: SourceLimits | None = None,
) -> list[SourceRecord]:
    resolved_limits = resolve_source_limits(limits)
    source_path = Path(path)
    with _open_stable_text_path(
        source_path,
        max_input_bytes=resolved_limits.max_input_bytes,
        label="source input",
        limit_error=SourceLimitError,
    ) as stream:
        return load_sources(
            stream,
            json_lines=source_path.suffix.casefold() == ".jsonl",
            limits=resolved_limits,
        )


def load_artifact(
    stream: TextIO,
    *,
    limits: ArtifactLimits | None = None,
) -> Any:
    """Load a compiled artifact through strict, resource-bounded JSON."""

    resolved_limits = resolve_artifact_limits(limits)
    raw = _read_limited_text(
        stream,
        max_input_bytes=resolved_limits.max_input_bytes,
        max_line_chars=resolved_limits.max_line_chars,
        label="compiled artifact input",
        limit_error=ArtifactLimitError,
    )
    if not raw.strip():
        raise ValueError("compiled artifact input cannot be empty")
    decoded = _decode_strict_json(
        raw,
        max_depth=resolved_limits.max_json_depth,
        label="compiled artifact JSON",
        limit_error=ArtifactLimitError,
    )
    validate_artifact_value(decoded, limits=resolved_limits)
    return decoded


def load_artifact_path(
    path: str | Path,
    *,
    limits: ArtifactLimits | None = None,
) -> Any:
    resolved_limits = resolve_artifact_limits(limits)
    artifact_path = Path(path)
    with _open_stable_text_path(
        artifact_path,
        max_input_bytes=resolved_limits.max_input_bytes,
        label="compiled artifact input",
        limit_error=ArtifactLimitError,
    ) as stream:
        return load_artifact(stream, limits=resolved_limits)


def validate_artifact_envelope(
    artifact: Any,
    *,
    limits: ArtifactLimits | None = None,
) -> dict[str, Any]:
    """Validate shape, item/selection identity, schema, and artifact self-hash.

    This checks envelope integrity without claiming that the artifact is
    authentic or that its semantic contents replay against trusted sources.
    Use :func:`verify_artifact_dict` for independent source-bound verification.
    """

    resolved_limits = resolve_artifact_limits(limits)
    validate_artifact_value(artifact, limits=resolved_limits)
    if not isinstance(artifact, dict):
        raise TypeError("compiled artifact must be a JSON object")

    issues: list[dict[str, Any]] = []
    _validate_artifact_shape(artifact, issues)

    raw_items = artifact.get("items")
    raw_selected = artifact.get("selected_item_ids")
    if not isinstance(raw_items, list):
        _shape_issue(
            issues,
            "invalid_items_collection",
            "Artifact items must be a JSON array.",
        )
        raw_items = []
    if not isinstance(raw_selected, list):
        _shape_issue(
            issues,
            "invalid_selected_item_ids",
            "Artifact selected_item_ids must be a JSON array.",
        )
        raw_selected = []

    item_ids = [
        item.get("id")
        for item in raw_items
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    if len(item_ids) != len(set(item_ids)):
        _shape_issue(
            issues,
            "duplicate_item_id",
            "Artifact items must have unique ids.",
        )
    selected_ids = [
        item_id for item_id in raw_selected if isinstance(item_id, str)
    ]
    if len(selected_ids) != len(set(selected_ids)):
        _shape_issue(
            issues,
            "duplicate_selected_item_id",
            "Artifact selected_item_ids must be unique.",
        )
    missing_selected = sorted(set(selected_ids) - set(item_ids))
    if missing_selected:
        _shape_issue(
            issues,
            "selected_item_missing",
            "Artifact selected_item_ids must reference retained items.",
        )

    if issues:
        first = issues[0]
        raise ValueError(
            "invalid compiled artifact envelope "
            f"({first['code']}): {first['message']}"
        )

    unsigned_artifact = {
        key: value
        for key, value in artifact.items()
        if key != "artifact_sha256"
    }
    try:
        canonical = json.dumps(
            unsigned_artifact,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (RecursionError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            f"invalid compiled artifact canonical JSON: {exc}"
        ) from exc
    actual_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if artifact["artifact_sha256"] != actual_digest:
        raise ValueError(
            "artifact digest mismatch: content does not match artifact_sha256"
        )
    if artifact["schema_version"] != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported artifact schema version: {artifact['schema_version']!r}"
        )
    return artifact


def dump_sources_jsonl(sources: Iterable[SourceRecord], stream: TextIO) -> None:
    for source in sources:
        stream.write(json.dumps(source.to_dict(), ensure_ascii=False) + "\n")


def verify_artifact_dict(
    artifact: Any,
    sources: list[SourceRecord],
    *,
    token_counter: Callable[[str], int] | None = None,
    token_counter_id: str | None = None,
    source_limits: SourceLimits | None = None,
    artifact_limits: ArtifactLimits | None = None,
) -> dict[str, Any]:
    """Verify a serialized artifact without trusting its own report."""

    resolved_artifact_limits = resolve_artifact_limits(artifact_limits)
    artifact_value_error: str | None = None
    try:
        validate_artifact_value(artifact, limits=resolved_artifact_limits)
    except TypeError as exc:
        # Serialized artifacts cannot contain cycles, non-string object keys,
        # unpaired surrogates, or non-JSON Python values. A direct caller can
        # still supply them; preserve the verifier's failure-report contract
        # while the canonical digest and shape checks fail closed below.
        artifact_value_error = str(exc)
    if not isinstance(sources, list) or not all(
        isinstance(source, SourceRecord) for source in sources
    ):
        raise TypeError("sources must be a list of SourceRecord values")
    resolved_limits = resolve_source_limits(source_limits)
    if len(sources) > resolved_limits.max_records:
        raise SourceLimitError(
            f"source record count exceeds {resolved_limits.max_records} records"
        )
    total_source_size = 0
    for index, source in enumerate(sources):
        record_size = source_value_size(
            source.to_dict(),
            limits=resolved_limits,
            index=index,
        )
        total_source_size = add_source_size(
            total_source_size,
            record_size,
            limits=resolved_limits,
        )

    issues: list[dict[str, Any]] = []
    if artifact_value_error is not None:
        issues.append(
            {
                "code": "invalid_artifact_json",
                "item_id": None,
                "message": artifact_value_error,
            }
        )
    compilation_metrics_valid = True
    if not isinstance(artifact, dict):
        _shape_issue(
            issues,
            "invalid_artifact_shape",
            "Compiled artifact must be a JSON object.",
        )
        artifact = {}
    else:
        compilation_metrics_valid = _validate_artifact_shape(artifact, issues)
    source_ids = [source.id for source in sources]
    source_sequences = [source.sequence for source in sources]
    if len(source_ids) != len(set(source_ids)):
        issues.append(
            {
                "code": "duplicate_source_id",
                "item_id": None,
                "message": "Supplied sources contain duplicate ids.",
            }
        )
    if len(source_sequences) != len(set(source_sequences)):
        issues.append(
            {
                "code": "duplicate_source_sequence",
                "item_id": None,
                "message": "Supplied sources contain duplicate sequence numbers.",
            }
        )
    claimed_artifact_digest = artifact.get("artifact_sha256")
    unsigned_artifact = {
        key: value for key, value in artifact.items() if key != "artifact_sha256"
    }
    actual_artifact_digest: str | None = None
    try:
        canonical_artifact = json.dumps(
            unsigned_artifact,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        actual_artifact_digest = hashlib.sha256(
            canonical_artifact.encode("utf-8")
        ).hexdigest()
    except (RecursionError, TypeError, ValueError, OverflowError) as exc:
        _shape_issue(
            issues,
            "invalid_artifact_json",
            f"Artifact must contain canonical JSON values: {exc}",
        )
    if claimed_artifact_digest != actual_artifact_digest:
        issues.append(
            {
                "code": "artifact_digest_mismatch",
                "item_id": None,
                "message": "Artifact content does not match its canonical SHA-256 digest.",
            }
        )
    expected_digest = source_digest(sources)
    if artifact.get("source_digest") != expected_digest:
        issues.append(
            {
                "code": "source_digest_mismatch",
                "item_id": None,
                "message": "Artifact was not compiled from this exact ordered source set.",
            }
        )

    raw_items = artifact.get("items")
    if not isinstance(raw_items, list):
        issues.append(
            {
                "code": "invalid_items_collection",
                "item_id": None,
                "message": "Artifact items must be a JSON array.",
            }
        )
        raw_items = []

    item_ids: set[str] = set()
    decoded_items: list[MemoryItem] = []
    for raw_item in raw_items:
        try:
            item = MemoryItem.from_dict(raw_item)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            issues.append(
                {
                    "code": "invalid_item",
                    "item_id": str(raw_item.get("id")) if isinstance(raw_item, dict) else None,
                    "message": str(exc),
                }
            )
            continue
        decoded_items.append(item)
        if raw_item.get("protected") is not item.protected:
            issues.append(
                {
                    "code": "protected_flag_mismatch",
                    "item_id": item.id,
                    "message": "Serialized protected flag disagrees with typed semantics.",
                }
            )
        if item.id in item_ids:
            issues.append(
                {"code": "duplicate_item_id", "item_id": item.id, "message": "Duplicate item id."}
            )
        item_ids.add(item.id)

    raw_selected = artifact.get("selected_item_ids")
    if not isinstance(raw_selected, list) or not all(
        isinstance(item_id, str) for item_id in raw_selected
    ):
        issues.append(
            {
                "code": "invalid_selected_item_ids",
                "item_id": None,
                "message": "selected_item_ids must be a JSON array of strings.",
            }
        )
        raw_selected = []
    if len(raw_selected) != len(set(raw_selected)):
        issues.append(
            {
                "code": "duplicate_selected_item_id",
                "item_id": None,
                "message": "selected_item_ids contains duplicates.",
            }
        )
    selected = set(raw_selected)
    missing_selected = selected - item_ids
    for item_id in sorted(missing_selected):
        issues.append(
            {
                "code": "selected_item_missing",
                "item_id": item_id,
                "message": "Selected id is absent from the item ledger.",
            }
        )
    if artifact.get("ledger_complete") is not True:
        issues.append(
            {
                "code": "incomplete_ledger",
                "item_id": None,
                "message": (
                    "This active-only artifact intentionally omits ledger items and cannot "
                    "receive a complete protected-coverage certificate."
                ),
            }
        )
    if artifact.get("schema_version") != "1.0":
        issues.append(
            {
                "code": "unsupported_schema_version",
                "item_id": None,
                "message": f"Unsupported schema version: {artifact.get('schema_version')!r}.",
            }
        )
    if artifact.get("source_count") != len(sources):
        issues.append(
            {
                "code": "source_count_mismatch",
                "item_id": None,
                "message": "Artifact source_count does not match the supplied source set.",
            }
        )

    compression_valid = True
    expected_budget_overflow = 0
    expected_target_met = True
    expected_protected_prompt_tokens: int | None = None
    expected_protected_budget_overflow: int | None = None
    raw_compression = artifact.get("compression")
    compiler_metadata = artifact.get("compiler_metadata")
    compilation_metrics_present = (
        isinstance(compiler_metadata, dict)
        and "metrics" in compiler_metadata
    )
    raw_metrics = (
        compiler_metadata.get("metrics")
        if isinstance(compiler_metadata, dict)
        else None
    )
    policy = (
        compiler_metadata.get("policy")
        if isinstance(compiler_metadata, dict)
        else None
    )
    if not isinstance(raw_compression, dict) or not isinstance(policy, dict):
        compression_valid = False
        issues.append(
            {
                "code": "invalid_compression_report",
                "item_id": None,
                "message": "Compression report and compiler policy metadata are required.",
            }
        )
    else:
        counter_name = policy.get("token_counter")
        custom_name = (
            f"custom:{token_counter_id.strip()}"
            if isinstance(token_counter_id, str) and token_counter_id.strip()
            else None
        )
        replay_counter = None
        if counter_name == "character-estimate-v1":
            replay_counter = None
        elif token_counter is not None and custom_name == counter_name:
            replay_counter = token_counter
        else:
            compression_valid = False
            issues.append(
                {
                    "code": "unverifiable_token_counter",
                    "item_id": None,
                    "message": (
                        "Independent verification requires the custom token counter "
                        "and its matching stable id."
                    ),
                }
            )

    if (
        isinstance(raw_compression, dict)
        and isinstance(policy, dict)
        and compression_valid
    ):
        chars_per_token = policy.get("chars_per_token")
        token_budget = policy.get("token_budget")
        minimum_ratio = policy.get("minimum_compression_ratio")
        valid_policy = (
            _is_finite_number(chars_per_token, minimum=0)
            and float(chars_per_token) > 0
            and _is_integer(token_budget, minimum=1)
            and _is_finite_number(minimum_ratio, minimum=1)
        )
        if not valid_policy:
            compression_valid = False
            issues.append(
                {
                    "code": "invalid_compression_policy",
                    "item_id": None,
                    "message": "Compression policy metadata has invalid numeric values.",
                }
            )
        else:
            ordered_sources = sorted(sources, key=lambda source: source.sequence)
            source_text = "\n".join(source.content for source in ordered_sources)
            active_prompt = render_typed_memory(decoded_items, raw_selected)

            def estimate_tokens(text: str) -> int:
                if replay_counter is None:
                    return max(1, math.ceil(len(text) / float(chars_per_token)))
                value = replay_counter(text)
                if isinstance(value, bool) or not isinstance(value, int):
                    raise TypeError("custom token counter must return an integer")
                if value < 0:
                    raise ValueError("custom token counter returned a negative count")
                return value

            try:
                source_tokens = estimate_tokens(source_text)
                active_tokens = estimate_tokens(active_prompt)
                protected_selected = [
                    item
                    for item in decoded_items
                    if item.id in selected and item.protected
                ]
                expected_protected_prompt_tokens = (
                    estimate_tokens(
                        render_typed_memory(
                            protected_selected,
                            [item.id for item in protected_selected],
                        )
                    )
                    if protected_selected
                    else 0
                )
                expected_protected_budget_overflow = max(
                    0,
                    expected_protected_prompt_tokens - token_budget,
                )
            except (OverflowError, TypeError, ValueError) as exc:
                compression_valid = False
                issues.append(
                    {
                        "code": "invalid_token_counter_result",
                        "item_id": None,
                        "message": str(exc),
                    }
                )
            else:
                raw_ratio = source_tokens / max(1, active_tokens)
                ratio = round(raw_ratio, 4)
                expected_budget_overflow = max(0, active_tokens - token_budget)
                expected_target_met = raw_ratio >= float(minimum_ratio)
                expected_compression = {
                    "source_chars": sum(len(source.content) for source in ordered_sources),
                    "active_chars": len(active_prompt),
                    "source_tokens_estimate": source_tokens,
                    "active_tokens_estimate": active_tokens,
                    "compression_ratio": ratio,
                    "token_budget": token_budget,
                    "budget_overflow": expected_budget_overflow,
                    "target_met": expected_target_met,
                }
                mismatched_fields = [
                    key
                    for key, expected in expected_compression.items()
                    if raw_compression.get(key) != expected
                ]
                if mismatched_fields:
                    compression_valid = False
                    issues.append(
                        {
                            "code": "compression_report_mismatch",
                            "item_id": None,
                            "message": (
                                "Compression report differs from canonical rendering: "
                                + ", ".join(mismatched_fields)
                            ),
                        }
                    )

    replay_issues: list[VerificationIssue] = []
    if isinstance(compiler_metadata, dict):
        for metadata_key, code, message, required_fields in (
            (
                "primary_failure",
                "primary_extractor_failed",
                PRIMARY_EXTRACTOR_FAILED_MESSAGE,
                {"extractor", "exception_type"},
            ),
            (
                "primary_degradation",
                "primary_extractor_degraded",
                PRIMARY_EXTRACTOR_DEGRADED_MESSAGE,
                {"extractor", "reason"},
            ),
            (
                "additive_safety_failure",
                "additive_safety_extractor_failed",
                ADDITIVE_SAFETY_EXTRACTOR_FAILED_MESSAGE,
                {"extractor", "exception_type"},
            ),
        ):
            failure = compiler_metadata.get(metadata_key)
            if failure is None:
                continue
            if (
                not isinstance(failure, dict)
                or set(failure) != required_fields
                or not all(
                    isinstance(failure.get(name), str) and failure[name]
                    for name in required_fields
                )
            ):
                issues.append(
                    {
                        "code": "invalid_compiler_metadata",
                        "item_id": None,
                        "message": (
                            f"compiler_metadata.{metadata_key} must contain "
                            "its required non-empty string fields."
                        ),
                    }
                )
                continue
            replay_issues.append(
                VerificationIssue(
                    code=code,
                    severity=IssueSeverity.WARNING,
                    message=message,
                )
            )

    protected_candidates = RuleBasedExtractor(protected_only=True).extract(sources).items
    recovery_candidate_count: int | None = None
    if compilation_metrics_present and compilation_metrics_valid:
        recovery_candidate_count = len(RuleBasedExtractor().extract(sources).items)
    replayed_recovered = sum(
        "verifier-recovered" in item.tags for item in decoded_items
    )
    core = verify_memory(
        sources=sources,
        items=decoded_items,
        selected_item_ids=raw_selected,
        protected_candidates=protected_candidates,
        recovered_items=replayed_recovered,
        budget_overflow=expected_budget_overflow,
        compression_target_met=expected_target_met,
        initial_issues=replay_issues,
    )
    embedded_verification = artifact.get("verification")
    if not isinstance(embedded_verification, dict):
        issues.append(
            {
                "code": "invalid_embedded_verification",
                "item_id": None,
                "message": "Artifact must contain its compilation-time verification report.",
            }
        )
    else:
        expected_verification = core.to_dict()
        comparable_keys = (
            "passed",
            "protected_candidates",
            "protected_retained",
            "protected_recall",
            "provenance_valid",
            "provenance_total",
            "provenance_validity",
            "recovered_items",
            "issues",
        )
        if any(
            embedded_verification.get(key) != expected_verification[key]
            for key in comparable_keys
        ):
            issues.append(
                {
                    "code": "embedded_verification_mismatch",
                    "item_id": None,
                    "message": "Embedded verification report does not match independent replay.",
                }
            )
        recovered = embedded_verification.get("recovered_items")
        if (
            isinstance(recovered, bool)
            or not isinstance(recovered, int)
            or not 0 <= recovered <= len(decoded_items)
        ):
            issues.append(
                {
                    "code": "invalid_recovered_item_count",
                    "item_id": None,
                    "message": "Recovered item count is outside its valid range.",
                }
            )
    if (
        compilation_metrics_present
        and compilation_metrics_valid
        and isinstance(raw_metrics, dict)
    ):
        protected_selected_items = sum(
            item.id in selected and item.protected for item in decoded_items
        )
        expected_metrics: dict[str, Any] = {
            "source_records": len(sources),
            "recovery_candidate_items": recovery_candidate_count,
            "certification_candidate_items": len(protected_candidates),
            "selected_items": len(raw_selected),
            "protected_selected_items": protected_selected_items,
            "verification_error_count": sum(
                issue.severity == IssueSeverity.ERROR for issue in core.issues
            ),
            "verification_warning_count": sum(
                issue.severity == IssueSeverity.WARNING for issue in core.issues
            ),
            "verification_info_count": sum(
                issue.severity == IssueSeverity.INFO for issue in core.issues
            ),
        }
        if expected_protected_prompt_tokens is not None:
            expected_metrics["protected_prompt_tokens"] = (
                expected_protected_prompt_tokens
            )
        if expected_protected_budget_overflow is not None:
            expected_metrics["protected_budget_overflow"] = (
                expected_protected_budget_overflow
            )
        if artifact.get("ledger_complete") is True:
            expected_metrics.update(
                {
                    "recovery_added_items": replayed_recovered,
                    "resolved_items": len(decoded_items),
                    "active_items": sum(
                        item.status == MemoryStatus.ACTIVE
                        for item in decoded_items
                    ),
                    "superseded_items": sum(
                        item.status == MemoryStatus.SUPERSEDED
                        for item in decoded_items
                    ),
                    "discarded_items": sum(
                        item.status == MemoryStatus.DISCARDED
                        for item in decoded_items
                    ),
                    "conflicting_items": sum(
                        item.status == MemoryStatus.CONFLICTING
                        for item in decoded_items
                    ),
                    "detected_conflicts": sum(
                        item.kind == MemoryKind.UNRESOLVED
                        and "detected-conflict" in item.tags
                        for item in decoded_items
                    ),
                    "protected_items": sum(
                        item.protected for item in decoded_items
                    ),
                }
            )
        mismatched_metrics = [
            key
            for key, expected in expected_metrics.items()
            if raw_metrics.get(key) != expected
        ]
        if (
            isinstance(compiler_metadata, dict)
            and "recovered_items" in compiler_metadata
            and raw_metrics.get("recovery_added_items")
            != compiler_metadata["recovered_items"]
        ):
            mismatched_metrics.append(
                "recovery_added_items/compiler_metadata.recovered_items"
            )
        if mismatched_metrics:
            compilation_metrics_valid = False
            issues.append(
                {
                    "code": "compilation_metrics_mismatch",
                    "item_id": None,
                    "message": (
                        "Compilation metrics differ from independent replay: "
                        + ", ".join(mismatched_metrics)
                    ),
                }
            )
    issues.extend(
        {
            "code": issue.code,
            "item_id": issue.item_id,
            "message": issue.message,
            "severity": issue.severity.value,
        }
        for issue in core.issues
    )
    return {
        "passed": not any(issue.get("severity", "error") == "error" for issue in issues),
        "source_digest_valid": artifact.get("source_digest") == expected_digest,
        "artifact_digest_valid": claimed_artifact_digest == actual_artifact_digest,
        "compression_valid": compression_valid,
        "compilation_metrics_present": compilation_metrics_present,
        "compilation_metrics_valid": compilation_metrics_valid,
        "ledger_complete": artifact.get("ledger_complete", True),
        "items": len(decoded_items),
        "protected_candidates": core.protected_candidates,
        "protected_retained": core.protected_retained,
        "protected_recall": core.protected_recall,
        "provenance_valid": core.provenance_valid,
        "provenance_total": core.provenance_total,
        "provenance_validity": core.provenance_validity,
        "verification_source_limits": resolved_limits.to_dict(),
        "verification_artifact_limits": resolved_artifact_limits.to_dict(),
        "issues": issues,
    }
