"""Deterministic planning for a bounded, materialized context window.

This experimental module stops before provider request serialization. It keeps
the current user turn and a bounded recent history verbatim, compiles an older
prefix into a verified ``ContextBundle``, and refuses any composition whose
mandatory components do not fit. A host must still count its final immutable
provider request.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from .compiler import _capture_budget_overflow
from .connector import (
    ContextBundle,
    ExactTokenCounterAdapter,
    LocalAIConnector,
    SourceEvent,
)
from .limits import (
    CompilationLimits,
    SourceLimitError,
    SourceLimits,
    add_source_size,
    bounded_json_utf8_size,
    resolve_source_limits,
    source_value_size,
)
from .models import (
    CONTEXT_WINDOW_DEGRADATION_MODE,
    CONTEXT_WINDOW_DEGRADATION_RUNGS,
    LOSSLESS_COMPACT_MEMORY_RENDERING_PROFILE,
    CompilationPolicy,
    SourceRecord,
    _FrozenDict,
    _FrozenList,
)

CONTEXT_WINDOW_PROTOTYPE_SCHEMA = "loss-resistant-context-window-prototype-v1"
MATERIALIZATION_REFUSAL_DIAGNOSTIC_SCHEMA = (
    "loss-resistant-materialization-refusal-diagnostic-v1"
)
_ACCOUNTING_SCOPE = "planned-components-not-final-provider-request"
_FINAL_RECOUNT_REASON = "final_provider_recount_required"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MAX_COUNT = (1 << 63) - 1
_MAX_CONTEXT_WINDOW_BYTES = 16 * 1024 * 1024
_MAX_JSON_DEPTH = 128
_MAX_JSON_NODES = 1_000_000
_MAX_RECENT_MESSAGES = 4_096
_ALLOWED_ROLES = frozenset({"user", "assistant", "tool", "function"})
_RESERVED_METADATA_FIELDS = frozenset(
    {
        "ctxc_authenticated_authority",
        "localai_authority",
        "localai_original_record_sha256",
        "localai_redaction",
        "localai_source_provenance",
        "trusted_for_state",
    }
)
_SOURCE_FIELDS = frozenset(
    {
        "id",
        "sequence",
        "role",
        "content",
        "timestamp",
        "metadata",
        "content_sha256",
        "record_sha256",
    }
)
_OMISSION_FIELDS = frozenset(
    {
        "id",
        "sequence",
        "role",
        "content_sha256",
        "source_record_sha256",
        "compiled_record_sha256",
        "reason",
    }
)
_ACCOUNTING_FIELDS = frozenset(
    {
        "hard_limit_tokens",
        "reserved_output_tokens",
        "safety_margin_tokens",
        "fixed_input_tokens",
        "memory_budget_tokens",
        "memory_tokens",
        "recent_tail_tokens",
        "current_turn_tokens",
        "source_message_count",
        "compiled_prefix_message_count",
        "recent_tail_message_count",
        "current_turn_message_count",
        "input_tokens",
        "occupied_tokens",
        "remaining_tokens",
        "per_message_overhead_tokens",
        "minimum_recent_messages",
        "maximum_recent_messages",
    }
)
_PROTOTYPE_FIELDS = frozenset(
    {
        "schema",
        "tokenizer_identity",
        "accounting_scope",
        "fixed_input_sha256",
        "context_bundle",
        "context_bundle_sha256",
        "rendered_context",
        "rendered_memory_sha256",
        "protected_state_sha256",
        "recent_messages",
        "recent_messages_sha256",
        "current_turn",
        "current_turn_sha256",
        "recent_tail_omissions",
        "recent_tail_omissions_sha256",
        "retrieval_result_sha256",
        "provider_execution_ready",
        "final_provider_recount_required",
        "refusal_reason",
        "accounting",
        "prototype_sha256",
    }
)
_PROTECTED_STATE_KEYS = (
    "active_goals",
    "constraints",
    "user_corrections",
    "decisions",
    "unresolved_questions",
    "exact_errors",
    "exact_references",
    "omitted_or_overflowed_protected_items",
)
_MANDATORY_MEMORY_KINDS = frozenset({"goal", "constraint", "user_correction", "decision"})
_REFUSAL_DIAGNOSTIC_FIELDS = frozenset(
    {
        "schema",
        "reason",
        "stage",
        "cause",
        "tokenizer_identity",
        "memory_budget_tokens",
        "required_memory_tokens",
        "overflow_tokens",
        "compiled_prefix_message_count",
        "compiled_prefix_manifest_sha256",
        "retrieval_result_sha256",
        "provider_execution_ready",
        "final_provider_recount_required",
    }
)


def _validated_refusal_diagnostic(value: object) -> bytes:
    if type(value) is not dict:
        raise TypeError("refusal diagnostic must be an exact object")
    if any(type(key) is not str for key in value):
        raise TypeError("refusal diagnostic keys must be exact strings")
    actual = frozenset(value)
    if actual != _REFUSAL_DIAGNOSTIC_FIELDS:
        unknown = sorted(actual - _REFUSAL_DIAGNOSTIC_FIELDS)
        missing = sorted(_REFUSAL_DIAGNOSTIC_FIELDS - actual)
        raise ValueError(
            "refusal diagnostic fields are invalid; "
            f"unknown={unknown}, missing={missing}"
        )
    if (
        type(value["schema"]) is not str
        or value["schema"] != MATERIALIZATION_REFUSAL_DIAGNOSTIC_SCHEMA
    ):
        raise ValueError("refusal diagnostic schema is unsupported")
    if type(value["reason"]) is not str or value["reason"] != "compiled_memory_not_verified":
        raise ValueError("refusal diagnostic reason is unsupported")
    if type(value["stage"]) is not str or value["stage"] != "compile_memory":
        raise ValueError("refusal diagnostic stage is unsupported")
    if (
        type(value["cause"]) is not str
        or value["cause"] != "memory_token_budget_overflow"
    ):
        raise ValueError("refusal diagnostic cause is unsupported")
    tokenizer_identity = value["tokenizer_identity"]
    if type(tokenizer_identity) is not str or not 1 <= len(tokenizer_identity) <= 256:
        raise TypeError("refusal diagnostic tokenizer_identity must be an exact string")
    if any(ord(character) < 32 or ord(character) == 127 for character in tokenizer_identity):
        raise ValueError("refusal diagnostic tokenizer_identity contains control characters")
    try:
        tokenizer_identity.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("refusal diagnostic tokenizer_identity must be valid Unicode") from exc
    for name in (
        "memory_budget_tokens",
        "required_memory_tokens",
        "overflow_tokens",
        "compiled_prefix_message_count",
    ):
        count = value[name]
        if type(count) is not int or not 0 <= count <= _MAX_COUNT:
            raise TypeError(f"refusal diagnostic {name} must be a non-negative exact integer")
    if value["memory_budget_tokens"] == 0:
        raise ValueError("refusal diagnostic memory_budget_tokens must be positive")
    if value["compiled_prefix_message_count"] == 0:
        raise ValueError("refusal diagnostic requires a compiled source prefix")
    if value["required_memory_tokens"] <= value["memory_budget_tokens"]:
        raise ValueError("refusal diagnostic does not describe a budget overflow")
    if (
        value["required_memory_tokens"] - value["memory_budget_tokens"]
        != value["overflow_tokens"]
    ):
        raise ValueError("refusal diagnostic overflow accounting is inconsistent")
    manifest_sha256 = value["compiled_prefix_manifest_sha256"]
    if type(manifest_sha256) is not str or _SHA256.fullmatch(manifest_sha256) is None:
        raise TypeError(
            "refusal diagnostic compiled_prefix_manifest_sha256 must be a SHA-256 string"
        )
    if value["retrieval_result_sha256"] is not None:
        raise ValueError("refusal diagnostic retrieval_result_sha256 must be null")
    if value["provider_execution_ready"] is not False:
        raise ValueError("a refusal diagnostic cannot claim provider readiness")
    if value["final_provider_recount_required"] is not True:
        raise ValueError("a refusal diagnostic must require the final provider recount")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


class ContextWindowError(ValueError):
    """Raised when a trustworthy active context window cannot be produced."""

    def __init__(
        self,
        message: str,
        *,
        reason: str = "invalid_context_window",
        diagnostic: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        if diagnostic is not None:
            if type(diagnostic) is not dict:
                raise TypeError("refusal diagnostic must be an exact object")
            if type(reason) is not str:
                raise TypeError("a refusal diagnostic reason must be an exact string")
            if reason != diagnostic.get("reason"):
                raise ValueError("exception and refusal diagnostic reasons must match")
        self._diagnostic_bytes = (
            None if diagnostic is None else _validated_refusal_diagnostic(diagnostic)
        )

    @property
    def diagnostic(self) -> dict[str, object] | None:
        """Return a detached exact-JSON refusal diagnostic when one is available."""

        if self._diagnostic_bytes is None:
            return None
        value = json.loads(self._diagnostic_bytes.decode("utf-8"))
        if type(value) is not dict:  # pragma: no cover - constructor invariant
            raise RuntimeError("stored refusal diagnostic is not an object")
        return value


class _FrozenAccounting(dict[str, int]):
    """An exact JSON integer object that rejects mutation."""

    @staticmethod
    def _immutable(*_args: object, **_kwargs: object) -> None:
        raise TypeError("context window accounting is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __ior__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable

    def __deepcopy__(self, _memo: dict[int, object]) -> _FrozenAccounting:
        return self


def _count(
    value: object,
    *,
    label: str,
    positive: bool = False,
    maximum: int = _MAX_COUNT,
) -> int:
    minimum = 1 if positive else 0
    if type(value) is not int or not minimum <= value <= maximum:
        qualifier = "positive" if positive else "non-negative"
        raise ContextWindowError(
            f"{label} must be a {qualifier} exact integer no greater than {maximum}",
            reason="invalid_token_accounting",
        )
    return value


def _checked_sum(values: Iterable[int], *, label: str) -> int:
    total = 0
    for value in values:
        _count(value, label=label)
        if value > _MAX_COUNT - total:
            raise ContextWindowError(
                f"{label} exceeds {_MAX_COUNT}",
                reason="token_accounting_overflow",
            )
        total += value
    return total


def _exact_fields(
    value: Mapping[str, Any],
    *,
    expected: frozenset[str],
    label: str,
) -> None:
    if type(value) is not dict:
        raise TypeError(f"{label} must be an exact object")
    actual = frozenset(value)
    unknown = sorted(actual - expected)
    missing = sorted(expected - actual)
    if unknown:
        raise ContextWindowError(f"{label} contains unknown fields: {', '.join(unknown)}")
    if missing:
        raise ContextWindowError(f"{label} is missing fields: {', '.join(missing)}")


def _require_plain_json(value: object) -> None:
    """Reject executable container and scalar subclasses before traversal."""

    stack: list[tuple[object, int, bool]] = [(value, 0, False)]
    active: set[int] = set()
    nodes = 0
    while stack:
        current, depth, leaving = stack.pop()
        if leaving:
            active.remove(id(current))
            continue
        nodes += 1
        if nodes > _MAX_JSON_NODES:
            raise ContextWindowError("context window artifact exceeds the JSON node limit")
        if current is None or type(current) in {str, bool, int, float}:
            continue
        if type(current) not in {dict, list}:
            raise ContextWindowError("context window artifact must contain exact JSON values")
        if depth >= _MAX_JSON_DEPTH:
            raise ContextWindowError("context window artifact exceeds the JSON depth limit")
        if len(current) > _MAX_JSON_NODES - nodes:
            raise ContextWindowError("context window artifact exceeds the JSON node limit")
        container_id = id(current)
        if container_id in active:
            raise ContextWindowError("context window artifact cannot contain cycles")
        active.add(container_id)
        stack.append((current, depth, True))
        if type(current) is dict:
            if not all(type(key) is str for key in current):
                raise ContextWindowError("context window object keys must be exact strings")
            stack.extend((entry, depth + 1, False) for entry in reversed(tuple(current.values())))
        else:
            stack.extend((entry, depth + 1, False) for entry in reversed(current))


def _canonical_bytes(value: object) -> bytes:
    try:
        _require_plain_json(value)
        bounded_json_utf8_size(
            value,
            max_bytes=_MAX_CONTEXT_WINDOW_BYTES,
            max_depth=_MAX_JSON_DEPTH,
            label="context window artifact",
            limit_error=ContextWindowError,
        )
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return text.encode("utf-8")
    except ContextWindowError:
        raise
    except (RecursionError, TypeError, UnicodeEncodeError, ValueError) as exc:
        raise ContextWindowError("context window value is not canonical UTF-8 JSON") from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _detached(value: object) -> Any:
    return json.loads(_canonical_bytes(value).decode("utf-8"))


def _sha256_or_none(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ContextWindowError(f"{label} must be null or 64 lowercase hexadecimal characters")
    return value


def _safe_text(
    value: object,
    *,
    label: str,
    maximum: int,
    allow_empty: bool = False,
    allow_line_controls: bool = False,
) -> str:
    minimum = 0 if allow_empty else 1
    if type(value) is not str or not minimum <= len(value) <= maximum:
        raise ContextWindowError(f"{label} has an invalid type or length")
    allowed_controls = "\t\n\r" if allow_line_controls else ""
    if any(
        (ord(character) < 32 and character not in allowed_controls) or ord(character) == 127
        for character in value
    ):
        raise ContextWindowError(f"{label} contains forbidden control characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ContextWindowError(f"{label} must be valid Unicode") from exc
    return value


def _require_exact_source_record_storage(
    value: SourceRecord,
    *,
    label: str,
) -> None:
    """Reject substituted record values before record methods can observe them."""

    if type(value) is not SourceRecord:
        raise TypeError(f"{label} must be an exact SourceRecord")
    fields = {
        name: object.__getattribute__(value, name)
        for name in (
            "id",
            "sequence",
            "role",
            "content",
            "timestamp",
            "metadata",
            "content_sha256",
            "record_sha256",
        )
    }
    if type(fields["id"]) is not str:
        raise TypeError(f"{label}.id must be an exact string")
    if type(fields["sequence"]) is not int:
        raise TypeError(f"{label}.sequence must be an exact integer")
    if type(fields["role"]) is not str or type(fields["content"]) is not str:
        raise TypeError(f"{label}.role and content must be exact strings")
    if fields["timestamp"] is not None and type(fields["timestamp"]) is not str:
        raise TypeError(f"{label}.timestamp must be an exact string or null")
    if type(fields["content_sha256"]) is not str or type(fields["record_sha256"]) is not str:
        raise TypeError(f"{label} digests must be exact strings")

    stack: list[tuple[object, int, bool]] = [(fields["metadata"], 0, False)]
    active: set[int] = set()
    nodes = 0
    while stack:
        current, depth, leaving = stack.pop()
        if leaving:
            active.remove(id(current))
            continue
        nodes += 1
        if nodes > _MAX_JSON_NODES:
            raise ContextWindowError(f"{label}.metadata exceeds the JSON node limit")
        if current is None or type(current) in {str, bool, int}:
            continue
        if type(current) is float:
            if not math.isfinite(current):
                raise ContextWindowError(f"{label}.metadata contains a non-finite number")
            continue
        if type(current) not in {_FrozenDict, _FrozenList}:
            raise TypeError(f"{label}.metadata must contain exact frozen JSON values")
        if depth >= _MAX_JSON_DEPTH:
            raise ContextWindowError(f"{label}.metadata exceeds the JSON depth limit")
        container_id = id(current)
        if container_id in active:
            raise ContextWindowError(f"{label}.metadata cannot contain cycles")
        active.add(container_id)
        stack.append((current, depth, True))
        if type(current) is _FrozenDict:
            if dict.__len__(current) > _MAX_JSON_NODES - nodes:
                raise ContextWindowError(f"{label}.metadata exceeds the JSON node limit")
            keys = tuple(dict.keys(current))
            if not all(type(key) is str for key in keys):
                raise TypeError(f"{label}.metadata keys must be exact strings")
            values = tuple(dict.values(current))
        else:
            if list.__len__(current) > _MAX_JSON_NODES - nodes:
                raise ContextWindowError(f"{label}.metadata exceeds the JSON node limit")
            values = tuple(list.__iter__(current))
        stack.extend((entry, depth + 1, False) for entry in reversed(values))


def _source_value(
    value: SourceRecord | Mapping[str, Any],
    *,
    index: int,
    limits: SourceLimits,
) -> tuple[SourceRecord, int]:
    if type(value) is SourceRecord:
        _require_exact_source_record_storage(
            value,
            label=f"sources[{index}]",
        )
        value.ensure_integrity()
        raw = value.to_dict()
        _require_plain_json(raw)
        raw_size = source_value_size(raw, limits=limits, index=index)
    elif type(value) is dict:
        _require_plain_json(value)
        raw = _detached(value)
        unknown = sorted(frozenset(raw) - _SOURCE_FIELDS)
        if unknown:
            raise ContextWindowError("source record contains unknown fields: " + ", ".join(unknown))
        if "role" not in raw or "content" not in raw:
            raise ContextWindowError("source record requires role and content")
        raw_size = source_value_size(raw, limits=limits, index=index)
    else:
        raise TypeError("sources must contain exact SourceRecord or dict values")

    if "id" in raw and raw["id"] is not None and type(raw["id"]) is not str:
        raise TypeError("source id must be an exact string or null")
    if "sequence" in raw and type(raw["sequence"]) is not int:
        raise TypeError("source sequence must be an exact integer")
    if type(raw["role"]) is not str or type(raw["content"]) is not str:
        raise TypeError("source role and content must be exact strings")
    if "timestamp" in raw and raw["timestamp"] is not None and type(raw["timestamp"]) is not str:
        raise TypeError("source timestamp must be an exact string or null")
    metadata = raw.get("metadata", {})
    if type(metadata) is not dict:
        raise TypeError("source metadata must be an exact object")
    _require_plain_json(metadata)
    for name in ("content_sha256", "record_sha256"):
        if name in raw and type(raw[name]) is not str:
            raise TypeError(f"source {name} must be an exact string")

    source = SourceRecord.from_dict(raw, default_sequence=index)
    source.ensure_integrity()
    _safe_text(source.id, label="source id", maximum=1_024)
    role = _safe_text(source.role, label="source role", maximum=128)
    if role != role.strip().casefold() or role not in _ALLOWED_ROLES:
        raise ContextWindowError(
            "source role must be one of assistant, function, tool, or user "
            "in canonical lowercase form",
            reason="unsupported_source_role",
        )
    _safe_text(
        source.content,
        label="source content",
        maximum=limits.max_line_chars,
        allow_empty=True,
        allow_line_controls=True,
    )
    if source.timestamp is not None:
        _safe_text(source.timestamp, label="source timestamp", maximum=256)
    collisions = sorted(_RESERVED_METADATA_FIELDS.intersection(source.metadata))
    if collisions:
        raise ContextWindowError(
            "source metadata cannot supply host-owned authority fields: " + ", ".join(collisions),
            reason="unverified_authority_metadata",
        )
    normalized_size = source_value_size(source.to_dict(), limits=limits, index=index)
    return SourceRecord.from_dict(source.to_dict(), default_sequence=index), max(
        raw_size, normalized_size
    )


def _prepare_sources(
    values: Iterable[SourceRecord | Mapping[str, Any]],
    *,
    limits: SourceLimits,
) -> list[SourceRecord]:
    if type(values) not in {list, tuple}:
        raise TypeError("sources must be an exact list or tuple")
    prepared: list[SourceRecord] = []
    total_size = 0
    for index, value in enumerate(values):
        if index >= limits.max_records:
            raise SourceLimitError(f"source record count exceeds {limits.max_records} records")
        source, raw_size = _source_value(value, index=index, limits=limits)
        total_size = add_source_size(total_size, raw_size, limits=limits)
        prepared.append(source)
    if not prepared:
        raise ContextWindowError("at least one source message is required")
    ids = [source.id for source in prepared]
    sequences = [source.sequence for source in prepared]
    if len(ids) != len(set(ids)):
        raise ContextWindowError("source ids must be unique", reason="duplicate_source_identity")
    if len(sequences) != len(set(sequences)):
        raise ContextWindowError(
            "source sequences must be unique", reason="duplicate_source_identity"
        )
    if sequences != sorted(sequences):
        raise ContextWindowError("source messages must already be ordered by increasing sequence")
    return prepared


def _clone_source(value: SourceRecord, *, label: str) -> SourceRecord:
    _require_exact_source_record_storage(value, label=label)
    value.ensure_integrity()
    raw = value.to_dict()
    _require_plain_json(raw)
    source = SourceRecord.from_dict(raw)
    _safe_text(source.id, label=f"{label}.id", maximum=1_024)
    role = _safe_text(source.role, label=f"{label}.role", maximum=128)
    if role != role.strip().casefold() or role not in _ALLOWED_ROLES:
        raise ContextWindowError(f"{label}.role is unsupported")
    _safe_text(
        source.content,
        label=f"{label}.content",
        maximum=8 * 1024 * 1024,
        allow_empty=True,
        allow_line_controls=True,
    )
    collisions = sorted(_RESERVED_METADATA_FIELDS.intersection(source.metadata))
    if collisions:
        raise ContextWindowError(
            f"{label}.metadata contains host-owned authority fields",
            reason="unverified_authority_metadata",
        )
    return source


def _serialized_source(value: object, *, label: str) -> SourceRecord:
    if type(value) is not dict:
        raise TypeError(f"{label} must be an exact object")
    _exact_fields(value, expected=_SOURCE_FIELDS, label=label)
    _require_plain_json(value)
    if type(value["id"]) is not str:
        raise TypeError(f"{label}.id must be an exact string")
    if type(value["sequence"]) is not int:
        raise TypeError(f"{label}.sequence must be an exact integer")
    if type(value["role"]) is not str or type(value["content"]) is not str:
        raise TypeError(f"{label}.role and content must be exact strings")
    if value["timestamp"] is not None and type(value["timestamp"]) is not str:
        raise TypeError(f"{label}.timestamp must be an exact string or null")
    if type(value["metadata"]) is not dict:
        raise TypeError(f"{label}.metadata must be an exact object")
    if type(value["content_sha256"]) is not str or type(value["record_sha256"]) is not str:
        raise TypeError(f"{label} digests must be exact strings")
    return SourceRecord.from_dict(value)


def _policy_value(policy: CompilationPolicy) -> dict[str, Any]:
    return {
        "token_budget": policy.token_budget,
        "minimum_compression_ratio": policy.minimum_compression_ratio,
        "fail_on_budget_overflow": policy.fail_on_budget_overflow,
        "include_superseded": policy.include_superseded,
        "include_discarded": policy.include_discarded,
        "verify": policy.verify,
        "recover_missed_protected": policy.recover_missed_protected,
        "chars_per_token": policy.chars_per_token,
        "fail_on_primary_extractor_error": policy.fail_on_primary_extractor_error,
    }


def _source_event(source: SourceRecord) -> SourceEvent:
    """Map one exact source without accepting caller-authored authority."""

    metadata = source.to_dict()["metadata"]
    collisions = sorted(_RESERVED_METADATA_FIELDS.intersection(metadata))
    if collisions:
        raise ContextWindowError(
            "source metadata cannot cross the host-owned authority boundary",
            reason="unverified_authority_metadata",
        )
    return SourceEvent(
        id=source.id,
        sequence=source.sequence,
        role=source.role,
        content=source.content,
        timestamp=source.timestamp,
        metadata=metadata,
        authority=None,
        redaction=None,
        provenance=None,
        content_sha256=source.content_sha256,
        record_sha256=source.record_sha256,
    )


def _deterministic_session_id(
    prefix: list[SourceRecord],
    *,
    tokenizer_identity: str,
    policy: CompilationPolicy,
    memory_rendering_profile: str | None = None,
    degradation_metadata: Mapping[str, object] | None = None,
) -> str:
    value = {
        "sources": [source.to_dict() for source in prefix],
        "tokenizer_identity": tokenizer_identity,
        "policy": _policy_value(policy),
    }
    if memory_rendering_profile is not None:
        value["context_window_memory_rendering_profile"] = memory_rendering_profile
    if degradation_metadata is not None:
        value["context_window_degradation"] = dict(degradation_metadata)
    return "context-window-" + _digest(value)[:48]


def _compiled_prefix_manifest_sha256(prefix: list[SourceRecord]) -> str:
    """Bind the ordered compiled prefix without disclosing its raw values."""

    return _digest(
        {
            "schema": "loss-resistant-compiled-prefix-manifest-v1",
            "records": [
                [source.sequence, source.id, source.record_sha256]
                for source in prefix
            ],
        }
    )


def _normalize_bundle_operational_fields(bundle: ContextBundle) -> ContextBundle:
    """Remove clock variance from a newly produced planning-only bundle.

    Only the compiler wall-clock stamp and elapsed-time observation are
    normalized. Both are operational observations, not semantic evidence.
    Every enclosing artifact and bundle digest is then recomputed and the
    resulting ``ContextBundle`` performs its ordinary envelope validation.
    """

    raw = bundle.to_dict()
    artifact = raw["artifact"]
    artifact["compiled_at"] = "1970-01-01T00:00:00+00:00"
    metrics = artifact.get("compiler_metadata", {}).get("metrics")
    if type(metrics) is not dict or "compile_duration_seconds" not in metrics:
        raise ContextWindowError("compiled context is missing bounded operational metadata")
    metrics["compile_duration_seconds"] = 0.0
    artifact["artifact_sha256"] = _digest(
        {key: value for key, value in artifact.items() if key != "artifact_sha256"}
    )
    raw["bindings"]["artifact_sha256"] = artifact["artifact_sha256"]
    return ContextBundle(
        schema=raw["schema"],
        protocol_version=raw["protocol_version"],
        artifact=artifact,
        trusted_memory=raw["trusted_memory"],
        bindings=raw["bindings"],
        certificate=raw["certificate"],
        token_accounting=raw["token_accounting"],
    )


def _protected_state_value(bundle: ContextBundle | None) -> dict[str, Any]:
    if bundle is None:
        return {name: [] for name in _PROTECTED_STATE_KEYS}
    trusted = bundle.trusted_memory
    return {name: copy.deepcopy(trusted[name]) for name in _PROTECTED_STATE_KEYS}


def _validate_retention_bundle(
    bundle: ContextBundle,
    *,
    tokenizer_identity: str,
    rendered_context: str,
    omissions: tuple[RecentTailOmission, ...],
    memory_tokens: int,
) -> ContextBundle:
    if type(bundle) is not ContextBundle:
        raise TypeError("context_bundle must be an exact ContextBundle")
    checked = ContextBundle.from_dict(bundle.to_dict())
    if checked.token_accounting["mode"] != "exact":
        raise ContextWindowError("compiled memory token accounting must be exact")
    if checked.token_accounting["tokenizer_identity"] != tokenizer_identity:
        raise ContextWindowError("compiled memory tokenizer identity does not match the prototype")
    if checked.token_accounting["rendered_tokens"] != memory_tokens:
        raise ContextWindowError("compiled memory token count does not match accounting")
    if (
        checked.bindings["rendered_memory_sha256"]
        != hashlib.sha256(rendered_context.encode("utf-8")).hexdigest()
    ):
        raise ContextWindowError("rendered memory digest does not match the ContextBundle")
    certificate = checked.certificate
    if (
        certificate["issued"] is not True
        or certificate["semantic_completeness_claimed"] is not False
        or certificate["detected_protected_commitments"]
        != certificate["retained_detected_protected_commitments"]
    ):
        raise ContextWindowError(
            "compiled memory lacks an issued bounded retention certificate",
            reason="protected_retention_not_verified",
        )
    if checked.trusted_memory["omitted_or_overflowed_protected_items"]:
        raise ContextWindowError(
            "compiled memory omitted or overflowed protected state",
            reason="protected_retention_not_verified",
        )
    compiler_policy = checked.bindings["compiler_policy"]
    if (
        compiler_policy.get("fail_on_budget_overflow") is not True
        or compiler_policy.get("verify") is not True
        or compiler_policy.get("recover_missed_protected") is not True
    ):
        raise ContextWindowError("compiled memory policy does not enforce protected retention")
    artifact = checked.artifact
    verification = artifact.get("verification")
    if type(verification) is not dict or verification.get("passed") is not True:
        raise ContextWindowError("compiled memory verification did not pass")
    selected = artifact.get("selected_item_ids")
    items = artifact.get("items")
    if type(selected) is not list or type(items) is not list:
        raise ContextWindowError("compiled memory item inventory is malformed")
    selected_ids = set(selected)
    for item in items:
        if type(item) is not dict:
            raise ContextWindowError("compiled memory item inventory is malformed")
        active = item.get("status") not in {"superseded", "discarded"}
        mandatory = item.get("protected") is True or item.get("kind") in (_MANDATORY_MEMORY_KINDS)
        if active and mandatory and item.get("id") not in selected_ids:
            raise ContextWindowError(
                "compiled memory displaced mandatory semantic state",
                reason="protected_retention_not_verified",
            )

    source_hashes = checked.trusted_memory["source_hashes"]
    if len(source_hashes) != len(omissions):
        raise ContextWindowError("compiled source inventory does not match omission accounting")
    for index, (source_hash, omission) in enumerate(zip(source_hashes, omissions, strict=True)):
        expected = {
            "source_id": omission.id,
            "sequence": omission.sequence,
            "content_sha256": omission.content_sha256,
            "record_sha256": omission.compiled_record_sha256,
        }
        if source_hash != expected:
            raise ContextWindowError(f"compiled source inventory differs at omission {index}")
    if checked.bindings["source_count"] != len(omissions):
        raise ContextWindowError("ContextBundle source count does not match omission accounting")
    return checked


@dataclass(frozen=True, slots=True)
class ContextWindowBudget:
    """Hard allocations used before the host's final provider recount.

    ``fixed_input_tokens`` may account for host-owned provider framing, tool
    schemas, and attachments. Retrieval evidence is outside this v1 prototype.
    """

    hard_limit_tokens: int
    memory_budget_tokens: int
    reserved_output_tokens: int = 1_024
    safety_margin_tokens: int = 256
    fixed_input_tokens: int = 0
    minimum_recent_messages: int = 0
    maximum_recent_messages: int = _MAX_RECENT_MESSAGES
    per_message_overhead_tokens: int = 0

    def __post_init__(self) -> None:
        _count(self.hard_limit_tokens, label="hard_limit_tokens", positive=True)
        _count(
            self.memory_budget_tokens,
            label="memory_budget_tokens",
            positive=True,
        )
        _count(
            self.minimum_recent_messages,
            label="minimum_recent_messages",
        )
        _count(
            self.maximum_recent_messages,
            label="maximum_recent_messages",
            positive=True,
            maximum=_MAX_RECENT_MESSAGES,
        )
        for name in (
            "reserved_output_tokens",
            "safety_margin_tokens",
            "fixed_input_tokens",
            "per_message_overhead_tokens",
        ):
            _count(getattr(self, name), label=name)
        if self.minimum_recent_messages > self.maximum_recent_messages:
            raise ContextWindowError(
                "minimum_recent_messages cannot exceed maximum_recent_messages"
            )
        fixed = _checked_sum(
            (
                self.fixed_input_tokens,
                self.reserved_output_tokens,
                self.safety_margin_tokens,
            ),
            label="fixed allocation",
        )
        if fixed >= self.hard_limit_tokens:
            required_hard_limit = fixed + self.memory_budget_tokens + 1
            raise ContextWindowError(
                "mandatory_components_do_not_fit: "
                "cause=fixed_allocation_exhausts_current_turn_capacity; "
                f"hard_limit_planning_units={self.hard_limit_tokens}; "
                f"fixed_input_planning_units={self.fixed_input_tokens}; "
                f"reserved_output_planning_units={self.reserved_output_tokens}; "
                f"safety_margin_planning_units={self.safety_margin_tokens}; "
                f"fixed_allocation_planning_units={fixed}; "
                f"memory_allocation_planning_units={self.memory_budget_tokens}; "
                "minimum_current_turn_planning_units=1; "
                f"required_hard_limit_planning_units={required_hard_limit}; "
                "shortfall_planning_units="
                f"{required_hard_limit - self.hard_limit_tokens}",
                reason="mandatory_components_do_not_fit",
            )
        available_dynamic = self.available_dynamic_tokens
        if self.memory_budget_tokens >= available_dynamic:
            required_dynamic = self.memory_budget_tokens + 1
            raise ContextWindowError(
                "mandatory_components_do_not_fit: "
                "cause=memory_allocation_exhausts_current_turn_capacity; "
                f"hard_limit_planning_units={self.hard_limit_tokens}; "
                f"fixed_input_planning_units={self.fixed_input_tokens}; "
                f"reserved_output_planning_units={self.reserved_output_tokens}; "
                f"safety_margin_planning_units={self.safety_margin_tokens}; "
                f"fixed_allocation_planning_units={fixed}; "
                f"available_dynamic_planning_units={available_dynamic}; "
                f"memory_allocation_planning_units={self.memory_budget_tokens}; "
                "minimum_current_turn_planning_units=1; "
                f"required_dynamic_planning_units={required_dynamic}; "
                "shortfall_planning_units="
                f"{required_dynamic - available_dynamic}",
                reason="mandatory_components_do_not_fit",
            )

    @property
    def available_dynamic_tokens(self) -> int:
        return self.hard_limit_tokens - _checked_sum(
            (
                self.reserved_output_tokens,
                self.safety_margin_tokens,
                self.fixed_input_tokens,
            ),
            label="fixed allocation",
        )


@dataclass(frozen=True, slots=True)
class ContextWindowDegradationPolicy:
    """Closed opt-in retry policy for a constrained materialization.

    The policy first retries the same partition with the lossless compact
    selected-memory renderer.  If that exact rendering still overflows, one
    final retry reallocates otherwise available dynamic capacity to memory
    using the smaller exact pre-verification requirement observed for the
    original partition, while preserving the configured minimum recent tail.
    The public ``minimal_memory_reallocation_*`` rung names describe that
    observed requirement; they do not claim a globally minimal budget after a
    boundary message moves into the compiled prefix.  No source or protected
    item is truncated, and all shifted prefix sources remain bound by ordinary
    omission and ContextBundle source inventories.
    """

    mode: str = CONTEXT_WINDOW_DEGRADATION_MODE

    def __post_init__(self) -> None:
        if type(self.mode) is not str:
            raise TypeError("degradation policy mode must be an exact string")
        if self.mode != CONTEXT_WINDOW_DEGRADATION_MODE:
            raise ContextWindowError(f"unsupported degradation policy mode: {self.mode!r}")


@dataclass(frozen=True, slots=True)
class RecentTailOmission:
    """One exact source identity represented by compiled memory, not live tail."""

    id: str
    sequence: int
    role: str
    content_sha256: str
    source_record_sha256: str
    compiled_record_sha256: str
    reason: str = "compiled_into_verified_memory"

    def __post_init__(self) -> None:
        _safe_text(self.id, label="omission id", maximum=1_024)
        _count(self.sequence, label="omission sequence")
        role = _safe_text(self.role, label="omission role", maximum=128)
        if role not in _ALLOWED_ROLES:
            raise ContextWindowError("omission role is unsupported")
        for name in (
            "content_sha256",
            "source_record_sha256",
            "compiled_record_sha256",
        ):
            if (
                type(getattr(self, name)) is not str
                or _SHA256.fullmatch(getattr(self, name)) is None
            ):
                raise ContextWindowError(
                    f"omission {name} must be 64 lowercase hexadecimal characters"
                )
        if self.reason != "compiled_into_verified_memory":
            raise ContextWindowError("omission reason is unsupported")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sequence": self.sequence,
            "role": self.role,
            "content_sha256": self.content_sha256,
            "source_record_sha256": self.source_record_sha256,
            "compiled_record_sha256": self.compiled_record_sha256,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RecentTailOmission:
        _exact_fields(value, expected=_OMISSION_FIELDS, label="tail omission")
        return cls(
            id=value["id"],
            sequence=value["sequence"],
            role=value["role"],
            content_sha256=value["content_sha256"],
            source_record_sha256=value["source_record_sha256"],
            compiled_record_sha256=value["compiled_record_sha256"],
            reason=value["reason"],
        )


@dataclass(frozen=True, slots=True)
class ContextWindowPrototype:
    """Self-hashed, non-public plan awaiting final provider admission."""

    tokenizer_identity: str
    context_bundle: ContextBundle | None
    rendered_context: str
    recent_messages: tuple[SourceRecord, ...]
    current_turn: SourceRecord
    recent_tail_omissions: tuple[RecentTailOmission, ...]
    accounting: Mapping[str, int]
    fixed_input_sha256: str | None
    context_bundle_sha256: str | None = None
    rendered_memory_sha256: str = ""
    protected_state_sha256: str = ""
    recent_messages_sha256: str = ""
    current_turn_sha256: str = ""
    recent_tail_omissions_sha256: str = ""
    retrieval_result_sha256: None = None
    provider_execution_ready: bool = False
    final_provider_recount_required: bool = True
    refusal_reason: None = None
    prototype_sha256: str = ""
    schema: str = CONTEXT_WINDOW_PROTOTYPE_SCHEMA
    accounting_scope: str = _ACCOUNTING_SCOPE

    def __post_init__(self) -> None:
        if self.schema != CONTEXT_WINDOW_PROTOTYPE_SCHEMA:
            raise ContextWindowError(f"unsupported context window schema: {self.schema!r}")
        tokenizer = _safe_text(
            self.tokenizer_identity,
            label="tokenizer_identity",
            maximum=256,
        )
        if self.accounting_scope != _ACCOUNTING_SCOPE:
            raise ContextWindowError("context window accounting_scope is unsupported")
        if type(self.rendered_context) is not str:
            raise TypeError("rendered_context must be an exact string")
        try:
            self.rendered_context.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ContextWindowError("rendered_context must be valid Unicode") from exc
        if self.retrieval_result_sha256 is not None:
            raise ContextWindowError("retrieval_result_sha256 is outside this v1 prototype")
        if self.provider_execution_ready is not False:
            raise ContextWindowError("a context window prototype cannot claim provider readiness")
        if self.final_provider_recount_required is not True:
            raise ContextWindowError(
                "a context window prototype must require final provider recount"
            )
        if self.refusal_reason is not None:
            raise ContextWindowError("successful prototypes require a null refusal_reason")

        current = _clone_source(self.current_turn, label="current_turn")
        if current.role != "user":
            raise ContextWindowError(
                "current_turn must have the canonical user role",
                reason="invalid_current_turn",
            )
        if type(self.recent_messages) is not tuple:
            raise TypeError("recent_messages must be an exact tuple")
        recent = tuple(
            _clone_source(message, label=f"recent_messages[{index}]")
            for index, message in enumerate(self.recent_messages)
        )
        if len(recent) > _MAX_RECENT_MESSAGES:
            raise ContextWindowError("recent message tail exceeds its hard bound")
        if type(self.recent_tail_omissions) is not tuple or not all(
            type(value) is RecentTailOmission for value in self.recent_tail_omissions
        ):
            raise TypeError(
                "recent_tail_omissions must be a tuple of exact RecentTailOmission values"
            )
        omissions = tuple(
            RecentTailOmission.from_dict(value.to_dict()) for value in self.recent_tail_omissions
        )
        combined_ids = [item.id for item in omissions]
        combined_ids.extend(message.id for message in recent)
        combined_ids.append(current.id)
        combined_sequences = [item.sequence for item in omissions]
        combined_sequences.extend(message.sequence for message in recent)
        combined_sequences.append(current.sequence)
        if len(combined_ids) != len(set(combined_ids)):
            raise ContextWindowError(
                "compiled, recent, and current source ids must be disjoint",
                reason="duplicate_source_identity",
            )
        if len(combined_sequences) != len(set(combined_sequences)):
            raise ContextWindowError(
                "compiled, recent, and current sequences must be disjoint",
                reason="duplicate_source_identity",
            )
        if combined_sequences != sorted(combined_sequences):
            raise ContextWindowError(
                "compiled prefix, recent tail, and current turn must preserve source order"
            )
        if current.sequence != combined_sequences[-1]:
            raise ContextWindowError(
                "current_turn must be the final source message",
                reason="invalid_current_turn",
            )

        if type(self.accounting) is not dict:
            raise TypeError("accounting must be an exact object")
        accounting_value = _detached(self.accounting)
        _exact_fields(
            accounting_value,
            expected=_ACCOUNTING_FIELDS,
            label="accounting",
        )
        for name in _ACCOUNTING_FIELDS:
            _count(
                accounting_value[name],
                label=f"accounting.{name}",
                positive=name
                in {
                    "hard_limit_tokens",
                    "memory_budget_tokens",
                    "maximum_recent_messages",
                    "current_turn_message_count",
                },
                maximum=(_MAX_RECENT_MESSAGES if name == "maximum_recent_messages" else _MAX_COUNT),
            )
        if (
            accounting_value["minimum_recent_messages"]
            > accounting_value["maximum_recent_messages"]
        ):
            raise ContextWindowError("recent message bounds are inconsistent")
        if accounting_value["current_turn_message_count"] != 1:
            raise ContextWindowError("current turn accounting must equal one")
        if accounting_value["recent_tail_message_count"] != len(recent):
            raise ContextWindowError("recent tail count disagrees with recent_messages")
        if accounting_value["compiled_prefix_message_count"] != len(omissions):
            raise ContextWindowError("compiled prefix count disagrees with omission accounting")
        if accounting_value["source_message_count"] != len(combined_ids):
            raise ContextWindowError("source message count disagrees with the exact partition")
        if len(recent) > accounting_value["maximum_recent_messages"]:
            raise ContextWindowError("recent tail exceeds its declared maximum")

        fixed_digest = _sha256_or_none(
            self.fixed_input_sha256,
            label="fixed_input_sha256",
        )
        if (accounting_value["fixed_input_tokens"] == 0) is not (fixed_digest is None):
            raise ContextWindowError(
                "fixed_input_sha256 must be null exactly when fixed_input_tokens is zero"
            )
        expected_input = _checked_sum(
            (
                accounting_value["fixed_input_tokens"],
                accounting_value["memory_tokens"],
                accounting_value["recent_tail_tokens"],
                accounting_value["current_turn_tokens"],
            ),
            label="input token accounting",
        )
        if accounting_value["input_tokens"] != expected_input:
            raise ContextWindowError("input_tokens does not equal its components")
        expected_occupied = _checked_sum(
            (
                expected_input,
                accounting_value["reserved_output_tokens"],
                accounting_value["safety_margin_tokens"],
            ),
            label="occupied token accounting",
        )
        if accounting_value["occupied_tokens"] != expected_occupied:
            raise ContextWindowError("occupied_tokens does not equal its components")
        hard_limit = accounting_value["hard_limit_tokens"]
        if expected_occupied > hard_limit:
            raise ContextWindowError(
                "context window exceeds the hard token limit",
                reason="hard_limit_overflow",
            )
        if accounting_value["remaining_tokens"] != hard_limit - expected_occupied:
            raise ContextWindowError("remaining_tokens is inconsistent")
        if accounting_value["memory_tokens"] > accounting_value["memory_budget_tokens"]:
            raise ContextWindowError("compiled memory exceeds its token budget")

        bundle_digest = _sha256_or_none(
            self.context_bundle_sha256,
            label="context_bundle_sha256",
        )
        if self.context_bundle is None:
            if (
                omissions
                or accounting_value["memory_tokens"] != 0
                or self.rendered_context
                or bundle_digest is not None
            ):
                raise ContextWindowError(
                    "a prototype without a ContextBundle cannot claim compiled memory"
                )
            bundle = None
        else:
            if not omissions or not self.rendered_context:
                raise ContextWindowError(
                    "a ContextBundle requires a non-empty compiled prefix and rendering"
                )
            bundle = _validate_retention_bundle(
                self.context_bundle,
                tokenizer_identity=tokenizer,
                rendered_context=self.rendered_context,
                omissions=omissions,
                memory_tokens=accounting_value["memory_tokens"],
            )
            if bundle_digest != bundle.bundle_sha256:
                raise ContextWindowError("context_bundle_sha256 disagrees with the ContextBundle")

        rendered_digest = hashlib.sha256(self.rendered_context.encode("utf-8")).hexdigest()
        protected_digest = _digest(_protected_state_value(bundle))
        recent_values = [message.to_dict() for message in recent]
        recent_digest = _digest(recent_values)
        current_digest = current.record_sha256
        omission_values = [value.to_dict() for value in omissions]
        omissions_digest = _digest(omission_values)
        supplied_digests = {
            "rendered_memory_sha256": (self.rendered_memory_sha256, rendered_digest),
            "protected_state_sha256": (self.protected_state_sha256, protected_digest),
            "recent_messages_sha256": (
                self.recent_messages_sha256,
                recent_digest,
            ),
            "current_turn_sha256": (self.current_turn_sha256, current_digest),
            "recent_tail_omissions_sha256": (
                self.recent_tail_omissions_sha256,
                omissions_digest,
            ),
        }
        for name, (supplied, expected) in supplied_digests.items():
            if type(supplied) is not str:
                raise TypeError(f"{name} must be a string")
            if supplied and supplied != expected:
                raise ContextWindowError(f"{name} digest mismatch")

        object.__setattr__(self, "tokenizer_identity", tokenizer)
        object.__setattr__(self, "context_bundle", bundle)
        object.__setattr__(self, "context_bundle_sha256", bundle_digest)
        object.__setattr__(self, "recent_messages", recent)
        object.__setattr__(self, "current_turn", current)
        object.__setattr__(self, "recent_tail_omissions", omissions)
        object.__setattr__(self, "accounting", _FrozenAccounting(accounting_value))
        object.__setattr__(self, "fixed_input_sha256", fixed_digest)
        object.__setattr__(self, "rendered_memory_sha256", rendered_digest)
        object.__setattr__(self, "protected_state_sha256", protected_digest)
        object.__setattr__(self, "recent_messages_sha256", recent_digest)
        object.__setattr__(self, "current_turn_sha256", current_digest)
        object.__setattr__(self, "recent_tail_omissions_sha256", omissions_digest)

        supplied_prototype = self.prototype_sha256
        if type(supplied_prototype) is not str:
            raise TypeError("prototype_sha256 must be a string")
        if supplied_prototype and _SHA256.fullmatch(supplied_prototype) is None:
            raise ContextWindowError("prototype_sha256 must be 64 lowercase hexadecimal characters")
        actual_prototype = _digest(self._unsigned_dict())
        if supplied_prototype and supplied_prototype != actual_prototype:
            raise ContextWindowError("context window prototype digest mismatch")
        object.__setattr__(self, "prototype_sha256", actual_prototype)

    def _unsigned_dict(self) -> dict[str, Any]:
        bundle = self.context_bundle
        if bundle is not None and type(bundle) is not ContextBundle:
            raise ContextWindowError("context_bundle changed to an unsupported value")
        if type(self.recent_messages) is not tuple or not all(
            type(message) is SourceRecord for message in self.recent_messages
        ):
            raise ContextWindowError("recent_messages changed to an unsupported value")
        if type(self.current_turn) is not SourceRecord:
            raise ContextWindowError("current_turn changed to an unsupported value")
        if type(self.recent_tail_omissions) is not tuple or not all(
            type(value) is RecentTailOmission for value in self.recent_tail_omissions
        ):
            raise ContextWindowError("recent_tail_omissions changed to an unsupported value")
        if type(self.accounting) is not _FrozenAccounting:
            raise ContextWindowError("accounting changed to an unsupported value")
        return {
            "schema": self.schema,
            "tokenizer_identity": self.tokenizer_identity,
            "accounting_scope": self.accounting_scope,
            "fixed_input_sha256": self.fixed_input_sha256,
            "context_bundle": None if bundle is None else bundle.to_dict(),
            "context_bundle_sha256": self.context_bundle_sha256,
            "rendered_context": self.rendered_context,
            "rendered_memory_sha256": self.rendered_memory_sha256,
            "protected_state_sha256": self.protected_state_sha256,
            "recent_messages": [message.to_dict() for message in self.recent_messages],
            "recent_messages_sha256": self.recent_messages_sha256,
            "current_turn": self.current_turn.to_dict(),
            "current_turn_sha256": self.current_turn_sha256,
            "recent_tail_omissions": [value.to_dict() for value in self.recent_tail_omissions],
            "recent_tail_omissions_sha256": (self.recent_tail_omissions_sha256),
            "retrieval_result_sha256": self.retrieval_result_sha256,
            "provider_execution_ready": self.provider_execution_ready,
            "final_provider_recount_required": (self.final_provider_recount_required),
            "refusal_reason": self.refusal_reason,
            "accounting": dict(self.accounting),
        }

    def _validated_unsigned_dict(self) -> dict[str, Any]:
        value = self._unsigned_dict()
        if _digest(value) != self.prototype_sha256:
            raise ContextWindowError("context window prototype changed after creation")
        return _detached(value)

    def ensure_integrity(self) -> None:
        self._validated_unsigned_dict()

    def to_dict(self) -> dict[str, Any]:
        value = self._validated_unsigned_dict()
        value["prototype_sha256"] = self.prototype_sha256
        return value

    def to_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        expected_prototype_sha256: str | None = None,
    ) -> ContextWindowPrototype:
        _exact_fields(
            value,
            expected=_PROTOTYPE_FIELDS,
            label="context window prototype",
        )
        raw = _detached(value)
        claimed = raw["prototype_sha256"]
        if type(claimed) is not str or _SHA256.fullmatch(claimed) is None:
            raise ContextWindowError("serialized prototype requires a valid prototype_sha256")
        expected = _sha256_or_none(
            expected_prototype_sha256,
            label="expected_prototype_sha256",
        )
        if expected is not None and expected != claimed:
            raise ContextWindowError("prototype does not match the independently expected digest")
        unsigned = {key: entry for key, entry in raw.items() if key != "prototype_sha256"}
        if _digest(unsigned) != claimed:
            raise ContextWindowError("context window prototype digest mismatch")
        raw_bundle = raw["context_bundle"]
        if raw_bundle is not None and type(raw_bundle) is not dict:
            raise TypeError("context_bundle must be an exact object or null")
        raw_recent = raw["recent_messages"]
        if type(raw_recent) is not list:
            raise TypeError("recent_messages must be an exact array")
        raw_current = raw["current_turn"]
        if type(raw_current) is not dict:
            raise TypeError("current_turn must be an exact object")
        raw_omissions = raw["recent_tail_omissions"]
        if type(raw_omissions) is not list:
            raise TypeError("recent_tail_omissions must be an exact array")
        raw_accounting = raw["accounting"]
        if type(raw_accounting) is not dict:
            raise TypeError("accounting must be an exact object")
        return cls(
            schema=raw["schema"],
            tokenizer_identity=raw["tokenizer_identity"],
            accounting_scope=raw["accounting_scope"],
            fixed_input_sha256=raw["fixed_input_sha256"],
            context_bundle=(None if raw_bundle is None else ContextBundle.from_dict(raw_bundle)),
            context_bundle_sha256=raw["context_bundle_sha256"],
            rendered_context=raw["rendered_context"],
            rendered_memory_sha256=raw["rendered_memory_sha256"],
            protected_state_sha256=raw["protected_state_sha256"],
            recent_messages=tuple(
                _serialized_source(
                    message,
                    label=f"recent_messages[{index}]",
                )
                for index, message in enumerate(raw_recent)
            ),
            recent_messages_sha256=raw["recent_messages_sha256"],
            current_turn=_serialized_source(
                raw_current,
                label="current_turn",
            ),
            current_turn_sha256=raw["current_turn_sha256"],
            recent_tail_omissions=tuple(
                RecentTailOmission.from_dict(value) for value in raw_omissions
            ),
            recent_tail_omissions_sha256=(raw["recent_tail_omissions_sha256"]),
            retrieval_result_sha256=raw["retrieval_result_sha256"],
            provider_execution_ready=raw["provider_execution_ready"],
            final_provider_recount_required=(raw["final_provider_recount_required"]),
            refusal_reason=raw["refusal_reason"],
            accounting=raw_accounting,
            prototype_sha256=claimed,
        )

    def runtime_payload(self) -> dict[str, Any]:
        """Refuse direct runtime export from the unpublished prototype."""

        self._validated_unsigned_dict()
        raise ContextWindowError(
            "ContextWindowPrototype is planning-only; materialize it with an "
            "independently expected allocation digest before projection",
            reason=_FINAL_RECOUNT_REASON,
        )


def _message_cost(
    source: SourceRecord,
    *,
    token_counter: ExactTokenCounterAdapter,
    overhead: int,
) -> int:
    try:
        content_tokens = token_counter(source.content)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ContextWindowError(
            "exact token counter rejected source content",
            reason="token_counter_failed",
        ) from exc
    if type(content_tokens) is not int:
        raise ContextWindowError(
            "exact token counter returned a non-exact integer",
            reason="token_counter_failed",
        )
    _count(content_tokens, label="content token count")
    return _checked_sum(
        (content_tokens, overhead),
        label="message token count",
    )


def _compose_context_window_once(
    sources: Iterable[SourceRecord | Mapping[str, Any]],
    *,
    current_turn_id: str,
    budget: ContextWindowBudget,
    token_counter: ExactTokenCounterAdapter,
    fixed_input_sha256: str | None = None,
    policy: CompilationPolicy | None = None,
    source_limits: SourceLimits | None = None,
    compilation_limits: CompilationLimits | None = None,
    memory_rendering_profile: str | None = None,
    degradation_mode: str | None = None,
    degradation_rung: str | None = None,
    requested_memory_budget_tokens: int | None = None,
) -> ContextWindowPrototype:
    """Create a deterministic planning prototype with an exact source partition."""

    if type(budget) is not ContextWindowBudget:
        raise TypeError("budget must be an exact ContextWindowBudget")
    if type(token_counter) is not ExactTokenCounterAdapter:
        raise TypeError("token_counter must be an exact ExactTokenCounterAdapter")
    current_id = _safe_text(
        current_turn_id,
        label="current_turn_id",
        maximum=1_024,
    )
    fixed_digest = _sha256_or_none(
        fixed_input_sha256,
        label="fixed_input_sha256",
    )
    if (budget.fixed_input_tokens == 0) is not (fixed_digest is None):
        raise ContextWindowError(
            "fixed_input_sha256 must be supplied exactly when fixed_input_tokens is non-zero"
        )
    if policy is not None and type(policy) is not CompilationPolicy:
        raise TypeError("policy must be an exact CompilationPolicy or null")
    if memory_rendering_profile is not None and (
        type(memory_rendering_profile) is not str
        or memory_rendering_profile != LOSSLESS_COMPACT_MEMORY_RENDERING_PROFILE
    ):
        raise ValueError("memory_rendering_profile is unsupported")
    degradation_values = (
        degradation_mode,
        degradation_rung,
        requested_memory_budget_tokens,
    )
    if any(value is not None for value in degradation_values):
        if (
            type(degradation_mode) is not str
            or degradation_mode != CONTEXT_WINDOW_DEGRADATION_MODE
            or type(degradation_rung) is not str
            or degradation_rung not in CONTEXT_WINDOW_DEGRADATION_RUNGS
            or type(requested_memory_budget_tokens) is not int
            or requested_memory_budget_tokens <= 0
        ):
            raise ValueError("context-window degradation metadata is invalid")
    elif memory_rendering_profile is not None:
        raise ValueError("memory rendering profile requires degradation metadata")
    resolved_limits = resolve_source_limits(source_limits)
    ordered = _prepare_sources(sources, limits=resolved_limits)
    current_matches = [source for source in ordered if source.id == current_id]
    if (
        len(current_matches) != 1
        or current_matches[0].role != "user"
        or current_matches[0] is not ordered[-1]
    ):
        raise ContextWindowError(
            "current_turn_id must identify the one final canonical user message",
            reason="invalid_current_turn",
        )
    current = ordered[-1]
    history = ordered[:-1]
    costs = [
        _message_cost(
            source,
            token_counter=token_counter,
            overhead=budget.per_message_overhead_tokens,
        )
        for source in ordered
    ]
    history_costs = costs[:-1]
    current_tokens = costs[-1]
    available = budget.available_dynamic_tokens
    total_dynamic = _checked_sum(costs, label="dynamic message tokens")
    prefix: list[SourceRecord] = []
    recent = history
    recent_tokens = _checked_sum(history_costs, label="recent tail tokens")

    needs_compilation = total_dynamic > available or len(history) > budget.maximum_recent_messages
    if needs_compilation:
        tail_capacity = available - budget.memory_budget_tokens
        minimum_count = min(budget.minimum_recent_messages, len(history))
        recent_start = len(history) - minimum_count
        retained_tokens = _checked_sum(
            (*history_costs[recent_start:], current_tokens),
            label="mandatory recent and current tokens",
        )
        if retained_tokens > tail_capacity:
            minimum_recent_tokens = retained_tokens - current_tokens
            raise ContextWindowError(
                "mandatory_components_do_not_fit: "
                "cause=current_turn_and_minimum_recent_tail_exceed_tail_capacity; "
                f"available_dynamic_planning_units={available}; "
                f"memory_allocation_planning_units={budget.memory_budget_tokens}; "
                f"tail_capacity_planning_units={tail_capacity}; "
                f"current_turn_planning_units={current_tokens}; "
                f"minimum_recent_message_count={minimum_count}; "
                "minimum_recent_tail_planning_units="
                f"{minimum_recent_tokens}; "
                f"required_tail_planning_units={retained_tokens}; "
                f"shortfall_planning_units={retained_tokens - tail_capacity}",
                reason="mandatory_components_do_not_fit",
            )
        while recent_start > 0 and len(history) - recent_start < budget.maximum_recent_messages:
            candidate = history_costs[recent_start - 1]
            if candidate > tail_capacity - retained_tokens:
                break
            recent_start -= 1
            retained_tokens += candidate
        prefix = history[:recent_start]
        recent = history[recent_start:]
        recent_tokens = _checked_sum(
            history_costs[recent_start:],
            label="recent tail tokens",
        )
        if not prefix:
            raise ContextWindowError("bounded tail selection did not produce a compilable prefix")

    context_bundle: ContextBundle | None = None
    rendered_context = ""
    memory_tokens = 0
    omissions: tuple[RecentTailOmission, ...] = ()
    if prefix:
        base_policy = policy if policy is not None else CompilationPolicy()
        compilation_policy = replace(
            base_policy,
            token_budget=budget.memory_budget_tokens,
            fail_on_budget_overflow=True,
            verify=True,
            recover_missed_protected=True,
        )
        connector = LocalAIConnector(
            policy=compilation_policy,
            token_counter=token_counter,
            source_limits=resolved_limits,
            compilation_limits=compilation_limits,
        )
        degradation_metadata: dict[str, object] | None = None
        if degradation_mode is not None:
            if degradation_rung is None or requested_memory_budget_tokens is None:
                raise RuntimeError("degradation metadata lost an internal field")
            connector._use_context_window_degradation(  # noqa: SLF001
                mode=degradation_mode,
                rung=degradation_rung,
                requested_memory_budget_tokens=requested_memory_budget_tokens,
                memory_rendering_profile=memory_rendering_profile,
            )
            degradation_metadata = {
                "mode": degradation_mode,
                "rung": degradation_rung,
                "requested_memory_budget_tokens": requested_memory_budget_tokens,
                "effective_memory_budget_tokens": budget.memory_budget_tokens,
            }
        events = [_source_event(source) for source in prefix]
        session_id = _deterministic_session_id(
            prefix,
            tokenizer_identity=token_counter.identity,
            policy=compilation_policy,
            memory_rendering_profile=memory_rendering_profile,
            degradation_metadata=degradation_metadata,
        )
        overflow_capture: list[tuple[int, int]] = []
        try:
            with _capture_budget_overflow() as overflow_capture:
                compiled = connector.compile_memory(
                    events=events,
                    session_id=session_id,
                )
            context_bundle = _normalize_bundle_operational_fields(compiled)
            replay = connector.verify_memory(
                context_bundle,
                events=events,
                session_id=session_id,
            )
        except ValueError as exc:
            diagnostic = None
            tokenizer_identity = token_counter.identity
            if type(tokenizer_identity) is not str:
                tokenizer_identity_valid = False
            else:
                try:
                    tokenizer_identity.encode("utf-8")
                except UnicodeEncodeError:
                    tokenizer_identity_valid = False
                else:
                    tokenizer_identity_valid = (
                        1 <= len(tokenizer_identity) <= 256
                        and not any(
                            ord(character) < 32 or ord(character) == 127
                            for character in tokenizer_identity
                        )
                    )
            overflow_details = (
                overflow_capture[0] if len(overflow_capture) == 1 else None
            )
            if overflow_details is not None:
                required_tokens, token_budget = overflow_details
                expected_message = (
                    "loss-resistant context exceeds token budget by "
                    f"{required_tokens - token_budget} estimated tokens"
                )
            else:
                required_tokens = token_budget = -1
                expected_message = ""
            if (
                type(exc) is ValueError
                and exc.args == (expected_message,)
                and token_budget == budget.memory_budget_tokens
                and tokenizer_identity_valid
                and all(
                    type(value) is int and 0 <= value <= _MAX_COUNT
                    for value in (
                        token_budget,
                        required_tokens,
                        required_tokens - token_budget,
                        len(prefix),
                    )
                )
                and len(prefix) > 0
            ):
                diagnostic = {
                    "schema": MATERIALIZATION_REFUSAL_DIAGNOSTIC_SCHEMA,
                    "reason": "compiled_memory_not_verified",
                    "stage": "compile_memory",
                    "cause": "memory_token_budget_overflow",
                    "tokenizer_identity": tokenizer_identity,
                    "memory_budget_tokens": token_budget,
                    "required_memory_tokens": required_tokens,
                    "overflow_tokens": required_tokens - token_budget,
                    "compiled_prefix_message_count": len(prefix),
                    "compiled_prefix_manifest_sha256": (
                        _compiled_prefix_manifest_sha256(prefix)
                    ),
                    "retrieval_result_sha256": None,
                    "provider_execution_ready": False,
                    "final_provider_recount_required": True,
                }
            raise ContextWindowError(
                "older context could not be compiled and independently verified "
                "within the memory budget",
                reason="compiled_memory_not_verified",
                diagnostic=diagnostic,
            ) from exc
        except (TypeError, RuntimeError, OverflowError) as exc:
            raise ContextWindowError(
                "older context could not be compiled and independently verified "
                "within the memory budget",
                reason="compiled_memory_not_verified",
            ) from exc
        certificate = replay.get("certificate")
        if (
            replay.get("passed") is not True
            or type(certificate) is not dict
            or certificate.get("issued") is not True
        ):
            raise ContextWindowError(
                "independent replay did not verify the compiled ContextBundle",
                reason="compiled_memory_not_verified",
            )
        rendered_context = connector.render_context(context_bundle)
        memory_tokens = context_bundle.token_accounting["rendered_tokens"]
        source_hashes = context_bundle.trusted_memory["source_hashes"]
        if len(source_hashes) != len(prefix):
            raise ContextWindowError("compiled source inventory does not match the source prefix")
        omission_values: list[RecentTailOmission] = []
        for source, compiled_hash in zip(prefix, source_hashes, strict=True):
            if (
                compiled_hash["source_id"] != source.id
                or compiled_hash["sequence"] != source.sequence
                or compiled_hash["content_sha256"] != source.content_sha256
            ):
                raise ContextWindowError(
                    "compiled source inventory changed an exact source identity"
                )
            omission_values.append(
                RecentTailOmission(
                    id=source.id,
                    sequence=source.sequence,
                    role=source.role,
                    content_sha256=source.content_sha256,
                    source_record_sha256=source.record_sha256,
                    compiled_record_sha256=compiled_hash["record_sha256"],
                )
            )
        omissions = tuple(omission_values)
        context_bundle = _validate_retention_bundle(
            context_bundle,
            tokenizer_identity=token_counter.identity,
            rendered_context=rendered_context,
            omissions=omissions,
            memory_tokens=memory_tokens,
        )

    input_tokens = _checked_sum(
        (
            budget.fixed_input_tokens,
            memory_tokens,
            recent_tokens,
            current_tokens,
        ),
        label="input token accounting",
    )
    occupied_tokens = _checked_sum(
        (
            input_tokens,
            budget.reserved_output_tokens,
            budget.safety_margin_tokens,
        ),
        label="occupied token accounting",
    )
    if occupied_tokens > budget.hard_limit_tokens:
        raise ContextWindowError(
            "composed context window exceeds the hard token limit",
            reason="hard_limit_overflow",
        )
    accounting = {
        "hard_limit_tokens": budget.hard_limit_tokens,
        "reserved_output_tokens": budget.reserved_output_tokens,
        "safety_margin_tokens": budget.safety_margin_tokens,
        "fixed_input_tokens": budget.fixed_input_tokens,
        "memory_budget_tokens": budget.memory_budget_tokens,
        "memory_tokens": memory_tokens,
        "recent_tail_tokens": recent_tokens,
        "current_turn_tokens": current_tokens,
        "source_message_count": len(ordered),
        "compiled_prefix_message_count": len(prefix),
        "recent_tail_message_count": len(recent),
        "current_turn_message_count": 1,
        "input_tokens": input_tokens,
        "occupied_tokens": occupied_tokens,
        "remaining_tokens": budget.hard_limit_tokens - occupied_tokens,
        "per_message_overhead_tokens": budget.per_message_overhead_tokens,
        "minimum_recent_messages": budget.minimum_recent_messages,
        "maximum_recent_messages": budget.maximum_recent_messages,
    }
    bundle_digest = None if context_bundle is None else context_bundle.bundle_sha256
    return ContextWindowPrototype(
        tokenizer_identity=token_counter.identity,
        context_bundle=context_bundle,
        context_bundle_sha256=bundle_digest,
        rendered_context=rendered_context,
        recent_messages=tuple(recent),
        current_turn=current,
        recent_tail_omissions=omissions,
        accounting=accounting,
        fixed_input_sha256=fixed_digest,
    )


def _is_exact_memory_overflow(error: ContextWindowError) -> bool:
    diagnostic = error.diagnostic
    return (
        error.reason == "compiled_memory_not_verified"
        and type(diagnostic) is dict
        and diagnostic.get("cause") == "memory_token_budget_overflow"
    )


def compose_context_window(
    sources: Iterable[SourceRecord | Mapping[str, Any]],
    *,
    current_turn_id: str,
    budget: ContextWindowBudget,
    token_counter: ExactTokenCounterAdapter,
    fixed_input_sha256: str | None = None,
    policy: CompilationPolicy | None = None,
    source_limits: SourceLimits | None = None,
    compilation_limits: CompilationLimits | None = None,
    degradation_policy: ContextWindowDegradationPolicy | None = None,
) -> ContextWindowPrototype:
    """Create a strict plan, with an optional bounded lossless retry ladder."""

    if degradation_policy is None:
        return _compose_context_window_once(
            sources,
            current_turn_id=current_turn_id,
            budget=budget,
            token_counter=token_counter,
            fixed_input_sha256=fixed_input_sha256,
            policy=policy,
            source_limits=source_limits,
            compilation_limits=compilation_limits,
        )
    if type(degradation_policy) is not ContextWindowDegradationPolicy:
        raise TypeError(
            "degradation_policy must be an exact ContextWindowDegradationPolicy or null"
        )

    resolved_limits = resolve_source_limits(source_limits)
    prepared = tuple(_prepare_sources(sources, limits=resolved_limits))

    def attempt(
        candidate_budget: ContextWindowBudget,
        *,
        compact: bool,
        rung: str | None,
    ) -> ContextWindowPrototype:
        return _compose_context_window_once(
            prepared,
            current_turn_id=current_turn_id,
            budget=candidate_budget,
            token_counter=token_counter,
            fixed_input_sha256=fixed_input_sha256,
            policy=policy,
            source_limits=resolved_limits,
            compilation_limits=compilation_limits,
            memory_rendering_profile=(
                LOSSLESS_COMPACT_MEMORY_RENDERING_PROFILE if compact else None
            ),
            degradation_mode=(CONTEXT_WINDOW_DEGRADATION_MODE if rung is not None else None),
            degradation_rung=rung,
            requested_memory_budget_tokens=(
                budget.memory_budget_tokens if rung is not None else None
            ),
        )

    strict_overflow: ContextWindowError | None = None
    try:
        return attempt(budget, compact=False, rung=None)
    except ContextWindowError as strict_error:
        if not _is_exact_memory_overflow(strict_error):
            raise
        strict_overflow = strict_error

    compact_overflow: ContextWindowError | None = None
    try:
        return attempt(budget, compact=True, rung="lossless_compact")
    except ContextWindowError as compact_error:
        if not _is_exact_memory_overflow(compact_error):
            raise
        compact_overflow = compact_error

    if type(strict_overflow) is not ContextWindowError:  # pragma: no cover
        raise RuntimeError("strict overflow retry lost its exact failure")
    if type(compact_overflow) is not ContextWindowError:  # pragma: no cover
        raise RuntimeError("compact overflow retry lost its exact failure")
    if not prepared:
        raise compact_overflow
    current_cost = _message_cost(
        prepared[-1],
        token_counter=token_counter,
        overhead=budget.per_message_overhead_tokens,
    )
    history = prepared[:-1]
    minimum_count = min(budget.minimum_recent_messages, len(history))
    mandatory_recent = history[len(history) - minimum_count :] if minimum_count else ()
    mandatory_recent_cost = _checked_sum(
        (
            _message_cost(
                source,
                token_counter=token_counter,
                overhead=budget.per_message_overhead_tokens,
            )
            for source in mandatory_recent
        ),
        label="minimum recent tail tokens",
    )
    maximum_memory_budget = (
        budget.available_dynamic_tokens - current_cost - mandatory_recent_cost
    )
    maximum_memory_budget = min(
        maximum_memory_budget,
        budget.available_dynamic_tokens - 1,
    )
    strict_diagnostic = strict_overflow.diagnostic
    compact_diagnostic = compact_overflow.diagnostic
    if (
        type(strict_diagnostic) is not dict
        or type(compact_diagnostic) is not dict
    ):  # pragma: no cover - exact-error invariant
        raise RuntimeError("rendering overflow lost its validated diagnostic")
    strict_required = strict_diagnostic["required_memory_tokens"]
    compact_required = compact_diagnostic["required_memory_tokens"]
    if type(strict_required) is not int or type(compact_required) is not int:
        raise RuntimeError("rendering overflow contains an invalid required count")
    # These exact counts describe the original requested-budget partition.
    # Reallocating memory can move additional boundary messages into the
    # compiled prefix, where a correction can change the selected rendering.
    # Keep the ladder to one final bounded attempt; the public rung therefore
    # records the smaller observed pre-verification requirement rather than
    # claiming a global minimum over every possible repartition.
    use_compact = compact_required < strict_required
    required_memory_budget = compact_required if use_compact else strict_required
    chosen_overflow = compact_overflow if use_compact else strict_overflow
    if (
        required_memory_budget <= budget.memory_budget_tokens
        or required_memory_budget > maximum_memory_budget
    ):
        raise chosen_overflow
    reallocated_budget = replace(
        budget,
        memory_budget_tokens=required_memory_budget,
    )
    return attempt(
        reallocated_budget,
        compact=use_compact,
        rung=(
            "minimal_memory_reallocation_compact"
            if use_compact
            else "minimal_memory_reallocation_standard"
        ),
    )


__all__ = [
    "CONTEXT_WINDOW_PROTOTYPE_SCHEMA",
    "CONTEXT_WINDOW_DEGRADATION_MODE",
    "MATERIALIZATION_REFUSAL_DIAGNOSTIC_SCHEMA",
    "ContextWindowBudget",
    "ContextWindowDegradationPolicy",
    "ContextWindowError",
    "ContextWindowPrototype",
    "RecentTailOmission",
    "compose_context_window",
]
