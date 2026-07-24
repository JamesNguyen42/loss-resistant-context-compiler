"""Input/output helpers for portable context artifacts."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, TextIO

from .extractors import RuleBasedExtractor
from .models import (
    ADDITIVE_SAFETY_EXTRACTOR_FAILED_MESSAGE,
    PRIMARY_EXTRACTOR_DEGRADED_MESSAGE,
    PRIMARY_EXTRACTOR_FAILED_MESSAGE,
    IssueSeverity,
    MemoryItem,
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


def _validate_artifact_shape(
    artifact: dict[str, Any],
    issues: list[dict[str, Any]],
) -> None:
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


def _records_from_value(value: Any) -> list[dict[str, Any]]:
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


def load_sources(stream: TextIO, *, json_lines: bool | None = None) -> list[SourceRecord]:
    """Load OpenAI-style messages or source records from JSON/JSONL."""

    raw = stream.read()
    if raw.startswith("\ufeff"):
        raw = raw[1:]
    if not raw.strip():
        return []
    if json_lines is True:
        records = [json.loads(line) for line in raw.splitlines() if line.strip()]
    elif json_lines is False:
        records = _records_from_value(json.loads(raw))
    else:
        try:
            records = _records_from_value(json.loads(raw))
        except json.JSONDecodeError:
            records = [json.loads(line) for line in raw.splitlines() if line.strip()]
    return [
        SourceRecord.from_dict(record, default_sequence=index)
        for index, record in enumerate(records)
    ]


def load_sources_path(path: str | Path) -> list[SourceRecord]:
    source_path = Path(path)
    with source_path.open("r", encoding="utf-8") as stream:
        return load_sources(stream, json_lines=source_path.suffix.casefold() == ".jsonl")


def dump_sources_jsonl(sources: Iterable[SourceRecord], stream: TextIO) -> None:
    for source in sources:
        stream.write(json.dumps(source.to_dict(), ensure_ascii=False) + "\n")


def verify_artifact_dict(
    artifact: Any,
    sources: list[SourceRecord],
    *,
    token_counter: Callable[[str], int] | None = None,
    token_counter_id: str | None = None,
) -> dict[str, Any]:
    """Verify a serialized artifact without trusting its own report."""

    if not isinstance(sources, list) or not all(
        isinstance(source, SourceRecord) for source in sources
    ):
        raise TypeError("sources must be a list of SourceRecord values")

    issues: list[dict[str, Any]] = []
    if not isinstance(artifact, dict):
        _shape_issue(
            issues,
            "invalid_artifact_shape",
            "Compiled artifact must be a JSON object.",
        )
        artifact = {}
    else:
        _validate_artifact_shape(artifact, issues)
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
    except (TypeError, ValueError, OverflowError) as exc:
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
    raw_compression = artifact.get("compression")
    compiler_metadata = artifact.get("compiler_metadata")
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
        "ledger_complete": artifact.get("ledger_complete", True),
        "items": len(decoded_items),
        "protected_candidates": core.protected_candidates,
        "protected_retained": core.protected_retained,
        "protected_recall": core.protected_recall,
        "provenance_valid": core.provenance_valid,
        "provenance_total": core.provenance_total,
        "provenance_validity": core.provenance_validity,
        "issues": issues,
    }
