"""Optional LocalAI connector and versioned process-boundary protocol.

The connector deliberately depends only on the public context-compiler API and
the Python standard library.  ``localai-contracts`` objects can be supplied
in-process when they expose ``model_dump()``, ``to_dict()``, or dataclass
fields, but that sibling package is never required for ordinary imports,
compilation, or CLI use.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, fields, is_dataclass
from typing import Any

from .archive import SourceArchive
from .artifact_inspection import summarize_artifact
from .compiler import ContextCompiler
from .io import validate_artifact_envelope, verify_artifact_dict
from .limits import (
    ArtifactLimitError,
    ArtifactLimits,
    CompilationLimitError,
    CompilationLimits,
    SourceLimitError,
    SourceLimits,
    add_source_size,
    resolve_artifact_limits,
    resolve_compilation_limits,
    resolve_source_limits,
    source_value_size,
)
from .models import (
    AUTHENTICATED_AUTHORITY_METADATA_KEY,
    CompilationPolicy,
    MemoryItem,
    MemoryKind,
    MemoryStatus,
    ProvenanceSpan,
    SourceRecord,
    render_typed_memory,
    source_digest,
)

CONNECTOR_PROTOCOL_VERSION = "0.1"
CONNECTOR_REQUEST_SCHEMA = "ctxc-connector-request-0.1"
CONNECTOR_RESPONSE_SCHEMA = "ctxc-connector-response-0.1"
SOURCE_EVENT_SCHEMA = "localai-source-event-0.1"
INCREMENTAL_CHECKPOINT_SCHEMA = "ctxc-incremental-checkpoint-0.1"
CONTEXT_BUNDLE_SCHEMA = "localai-context-bundle-0.1"
CONNECTOR_INSPECTION_SCHEMA = "ctxc-connector-inspection-0.1"
RETENTION_CERTIFICATE_WORDING = "all detected protected commitments retained"

CONNECTOR_OPERATIONS = (
    "capabilities",
    "ingest_source_events",
    "compile_memory",
    "render_context",
    "verify_memory",
    "inspect_memory",
)

_CONNECTOR_PUBLIC_ERROR_SPECS = {
    ("resource_limit", "resource_limit_exceeded"): (
        "connector request exceeds a resource limit",
        "ConnectorResourceLimitError",
        False,
    ),
    ("state", "unknown_session"): (
        "connector session is unknown",
        "ConnectorStateError",
        False,
    ),
    ("timeout", "operation_timed_out"): (
        "connector operation timed out",
        "ConnectorTimeoutError",
        True,
    ),
    ("invalid_request", "invalid_json"): (
        "connector request is not valid JSON",
        "ConnectorRequestError",
        False,
    ),
    ("invalid_request", "invalid_type"): (
        "connector request has an invalid type",
        "ConnectorRequestError",
        False,
    ),
    ("invalid_request", "invalid_value"): (
        "connector request has an invalid value",
        "ConnectorRequestError",
        False,
    ),
    ("invalid_request", "unsupported_operation"): (
        "connector operation is unsupported",
        "ConnectorRequestError",
        False,
    ),
    ("invalid_request", "unsupported_schema"): (
        "connector request schema is unsupported",
        "ConnectorRequestError",
        False,
    ),
    ("integrity", "integrity_check_failed"): (
        "connector integrity check failed",
        "ConnectorIntegrityError",
        False,
    ),
    ("runtime", "connector_failure"): (
        "connector operation failed",
        "ConnectorRuntimeError",
        False,
    ),
}
_MAX_ERROR_CLASSIFICATION_CHARACTERS = 4096
_SHA256 = re.compile(r"[a-f0-9]{64}")
_MAX_JSON_INTEGER_DIGITS = 640
_UNTRUSTED_HISTORY_ROLES = frozenset({"assistant", "tool", "function"})
_EVENT_FIELDS = frozenset(
    {
        "schema",
        "id",
        "sequence",
        "role",
        "content",
        "timestamp",
        "metadata",
        "authority",
        "redaction",
        "provenance",
        "content_sha256",
        "record_sha256",
    }
)
_AUTHORITY_FIELDS = frozenset({"authenticated", "trusted_for_state", "issuer"})
_RESERVED_EVENT_METADATA = frozenset(
    {
        AUTHENTICATED_AUTHORITY_METADATA_KEY,
        "localai_authority",
        "localai_redaction",
        "localai_source_provenance",
        "localai_original_record_sha256",
    }
)
_CANONICAL_SOURCE_RECORD_FIELDS = frozenset(
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
_POLICY_FIELDS = frozenset(field.name for field in fields(CompilationPolicy))
_TRUSTED_MEMORY_FIELDS = frozenset(
    {
        "active_goals",
        "constraints",
        "user_corrections",
        "decisions",
        "confirmed_facts",
        "unresolved_questions",
        "exact_errors",
        "exact_references",
        "source_spans",
        "source_hashes",
        "omitted_or_overflowed_protected_items",
    }
)
_TRUSTED_MEMORY_CATEGORY_KINDS = {
    "active_goals": MemoryKind.GOAL,
    "constraints": MemoryKind.CONSTRAINT,
    "user_corrections": MemoryKind.USER_CORRECTION,
    "decisions": MemoryKind.DECISION,
    "confirmed_facts": MemoryKind.CONFIRMED_FACT,
    "unresolved_questions": MemoryKind.UNRESOLVED,
    "exact_errors": MemoryKind.EXACT_ERROR,
    "exact_references": MemoryKind.EXACT_REFERENCE,
}
_TRUSTED_MEMORY_ITEM_LIMIT = 200_000
_TRUSTED_MEMORY_SPAN_LIMIT = 1_000_000
_TRUSTED_MEMORY_SOURCE_HASH_LIMIT = 100_000
_SOURCE_HASH_FIELDS = frozenset(
    {"source_id", "sequence", "content_sha256", "record_sha256"}
)
_OMITTED_PROTECTED_FIELDS = frozenset({"reason", "item", "overflow_tokens"})
_BINDING_FIELDS = frozenset(
    {
        "session_id",
        "source_digest",
        "source_count",
        "archive_chain_head_sha256",
        "archive_head_verified",
        "compiler_policy",
        "compiler_policy_sha256",
        "tokenizer_identity",
        "token_accounting",
        "token_accounting_exact",
        "rendered_memory_sha256",
        "artifact_sha256",
    }
)
_CERTIFICATE_FIELDS = frozenset(
    {
        "issued",
        "claim",
        "scope",
        "detected_protected_commitments",
        "retained_detected_protected_commitments",
        "semantic_completeness_claimed",
    }
)
_TOKEN_ACCOUNTING_FIELDS = frozenset(
    {
        "mode",
        "exact",
        "tokenizer_identity",
        "source_tokens",
        "rendered_tokens",
        "field_name_compatibility_note",
    }
)
_TOKEN_FIELD_COMPATIBILITY_NOTE = (
    "compiled artifact schema 1.0 retains *_tokens_estimate field "
    "names even when an exact in-process counter supplied the values"
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _detached_json(value: Any) -> Any:
    """Return a deep detached canonical-JSON value."""

    return json.loads(_canonical_json(value))


def _validate_sha256(value: str | None, *, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be 64 lowercase hexadecimal characters or null")
    return value


def _exact_fields(
    value: Mapping[str, Any],
    *,
    allowed: frozenset[str],
    required: frozenset[str] = frozenset(),
    label: str,
) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - allowed)
    if missing or unknown:
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        raise ValueError(f"{label} fields are invalid: {'; '.join(details)}")


def _mapping_from_object(value: Any, *, label: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return copy.deepcopy(dict(value))
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    for method_name in ("model_dump", "to_dict", "dict"):
        method = getattr(value, method_name, None)
        if not callable(method):
            continue
        try:
            result = method(mode="json") if method_name == "model_dump" else method()
        except TypeError:
            result = method()
        if isinstance(result, Mapping):
            return copy.deepcopy(dict(result))
    raise TypeError(
        f"{label} must be a mapping, dataclass, or object with model_dump()/to_dict()"
    )


def _canonical_source_record_from_mapping(
    value: Any,
    *,
    default_sequence: int,
    label: str,
) -> SourceRecord:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a JSON object")
    raw = dict(value)
    _exact_fields(
        raw,
        allowed=_CANONICAL_SOURCE_RECORD_FIELDS,
        required=_CANONICAL_SOURCE_RECORD_FIELDS,
        label=label,
    )
    return SourceRecord.from_dict(raw, default_sequence=default_sequence)


def _policy_to_dict(policy: CompilationPolicy) -> dict[str, Any]:
    return {field.name: getattr(policy, field.name) for field in fields(policy)}


def _policy_from_value(
    value: CompilationPolicy | Mapping[str, Any] | None,
    *,
    fallback: CompilationPolicy,
) -> CompilationPolicy:
    if value is None:
        return fallback
    if isinstance(value, CompilationPolicy):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("policy must be a CompilationPolicy, JSON object, or null")
    _exact_fields(
        value,
        allowed=_POLICY_FIELDS,
        label="connector compilation policy",
    )
    return CompilationPolicy(**dict(value))


def _policy_artifact_claim_issues(
    policy: Mapping[str, Any],
    artifact: Mapping[str, Any],
) -> list[tuple[str, str]]:
    """Return policy claims that contradict independently emitted evidence."""

    issues: list[tuple[str, str]] = []
    if policy.get("verify") is not True:
        issues.append(
            (
                "connector_policy_verification_disabled",
                "ContextBundle connector policy must require verification.",
            )
        )
    metadata = artifact.get("compiler_metadata")
    artifact_policy = metadata.get("policy") if isinstance(metadata, dict) else None
    if not isinstance(artifact_policy, dict):
        issues.append(
            (
                "missing_artifact_policy",
                "ContextBundle artifact is missing compiler policy metadata.",
            )
        )
    else:
        for name in (
            "token_budget",
            "minimum_compression_ratio",
            "chars_per_token",
            "include_superseded",
            "include_discarded",
        ):
            if policy.get(name) != artifact_policy.get(name):
                issues.append(
                    (
                        "connector_policy_artifact_mismatch",
                        f"ContextBundle connector policy {name} contradicts "
                        "artifact compiler metadata.",
                    )
                )
    if (
        not isinstance(metadata, dict)
        or policy.get("fail_on_primary_extractor_error")
        is not metadata.get("fail_on_primary_extractor_error")
    ):
        issues.append(
            (
                "connector_primary_failure_policy_mismatch",
                "ContextBundle primary-extractor failure policy contradicts "
                "artifact compiler metadata.",
            )
        )
    compression = artifact.get("compression")
    budget_overflow = (
        compression.get("budget_overflow")
        if isinstance(compression, dict)
        else None
    )
    if (
        policy.get("fail_on_budget_overflow") is True
        and isinstance(budget_overflow, int)
        and not isinstance(budget_overflow, bool)
        and budget_overflow > 0
    ):
        issues.append(
            (
                "impossible_budget_overflow_policy",
                "ContextBundle claims fail_on_budget_overflow for an artifact "
                "that contains a positive budget overflow.",
            )
        )
    recovered_items = (
        metadata.get("recovered_items") if isinstance(metadata, dict) else None
    )
    if (
        policy.get("recover_missed_protected") is False
        and isinstance(recovered_items, int)
        and not isinstance(recovered_items, bool)
        and recovered_items > 0
    ):
        issues.append(
            (
                "impossible_recovery_policy",
                "ContextBundle claims protected-item recovery was disabled for "
                "an artifact containing recovered items.",
            )
        )
    return issues


@dataclass(frozen=True, slots=True)
class SourceEvent:
    """Plain, versioned source event accepted by the connector."""

    role: str
    content: str
    sequence: int | None = None
    id: str | None = None
    timestamp: str | None = None
    metadata: dict[str, Any] | None = None
    authority: dict[str, Any] | None = None
    redaction: dict[str, Any] | None = None
    provenance: dict[str, Any] | None = None
    content_sha256: str = ""
    record_sha256: str = ""
    schema: str = SOURCE_EVENT_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "id": self.id,
            "sequence": self.sequence,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": copy.deepcopy({} if self.metadata is None else self.metadata),
            "authority": copy.deepcopy(self.authority),
            "redaction": copy.deepcopy(self.redaction),
            "provenance": copy.deepcopy(self.provenance),
            "content_sha256": self.content_sha256,
            "record_sha256": self.record_sha256,
        }


def _invalid_trusted_memory_entry(
    exc: Exception,
    *,
    label: str,
) -> Exception:
    message = f"{label} is invalid: {exc}"
    if isinstance(exc, TypeError):
        return TypeError(message)
    return ValueError(message)


def _validated_memory_item(value: Any, *, label: str) -> MemoryItem:
    try:
        return MemoryItem.from_dict(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise _invalid_trusted_memory_entry(exc, label=label) from exc


def _validated_provenance_span(value: Any, *, label: str) -> ProvenanceSpan:
    try:
        return ProvenanceSpan.from_dict(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise _invalid_trusted_memory_entry(exc, label=label) from exc


def _bounded_array(
    value: Any,
    *,
    label: str,
    maximum: int,
) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"{label} must be an array")
    if len(value) > maximum:
        raise ValueError(f"{label} exceeds {maximum} entries")
    return value


def _validate_trusted_memory_shape(trusted_memory: dict[str, Any]) -> None:
    _exact_fields(
        trusted_memory,
        allowed=_TRUSTED_MEMORY_FIELDS,
        required=_TRUSTED_MEMORY_FIELDS,
        label="ContextBundle trusted_memory",
    )
    for name in _TRUSTED_MEMORY_CATEGORY_KINDS:
        entries = _bounded_array(
            trusted_memory[name],
            label=f"ContextBundle trusted_memory.{name}",
            maximum=_TRUSTED_MEMORY_ITEM_LIMIT,
        )
        for index, entry in enumerate(entries):
            _validated_memory_item(
                entry,
                label=f"ContextBundle trusted_memory.{name}[{index}]",
            )

    spans = _bounded_array(
        trusted_memory["source_spans"],
        label="ContextBundle trusted_memory.source_spans",
        maximum=_TRUSTED_MEMORY_SPAN_LIMIT,
    )
    for index, span in enumerate(spans):
        _validated_provenance_span(
            span,
            label=f"ContextBundle trusted_memory.source_spans[{index}]",
        )

    source_hashes = _bounded_array(
        trusted_memory["source_hashes"],
        label="ContextBundle trusted_memory.source_hashes",
        maximum=_TRUSTED_MEMORY_SOURCE_HASH_LIMIT,
    )
    for index, source_hash in enumerate(source_hashes):
        label = f"ContextBundle trusted_memory.source_hashes[{index}]"
        if not isinstance(source_hash, dict):
            raise TypeError(f"{label} must be an object")
        _exact_fields(
            source_hash,
            allowed=_SOURCE_HASH_FIELDS,
            required=_SOURCE_HASH_FIELDS,
            label=label,
        )
        source_id = source_hash["source_id"]
        if not isinstance(source_id, str) or not source_id:
            raise TypeError(f"{label}.source_id must be a non-empty string")
        if len(source_id) > 1_024:
            raise ValueError(f"{label}.source_id exceeds 1024 characters")
        sequence = source_hash["sequence"]
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise TypeError(f"{label}.sequence must be an integer")
        if sequence < 0:
            raise ValueError(f"{label}.sequence cannot be negative")
        for digest_name in ("content_sha256", "record_sha256"):
            if _validate_sha256(
                source_hash[digest_name],
                label=f"{label}.{digest_name}",
            ) is None:
                raise TypeError(f"{label}.{digest_name} cannot be null")

    omitted = _bounded_array(
        trusted_memory["omitted_or_overflowed_protected_items"],
        label=(
            "ContextBundle trusted_memory."
            "omitted_or_overflowed_protected_items"
        ),
        maximum=_TRUSTED_MEMORY_ITEM_LIMIT,
    )
    for index, entry in enumerate(omitted):
        label = (
            "ContextBundle trusted_memory."
            f"omitted_or_overflowed_protected_items[{index}]"
        )
        if not isinstance(entry, dict):
            raise TypeError(f"{label} must be an object")
        _exact_fields(
            entry,
            allowed=_OMITTED_PROTECTED_FIELDS,
            required=_OMITTED_PROTECTED_FIELDS,
            label=label,
        )
        if entry["reason"] not in {"omitted", "protected_budget_overflow"}:
            raise ValueError(f"{label}.reason is invalid")
        _validated_memory_item(entry["item"], label=f"{label}.item")
        overflow = entry["overflow_tokens"]
        if isinstance(overflow, bool) or not isinstance(overflow, int):
            raise TypeError(f"{label}.overflow_tokens must be an integer")
        if overflow < 0:
            raise ValueError(f"{label}.overflow_tokens cannot be negative")


@dataclass(frozen=True, slots=True)
class ExactTokenCounterAdapter:
    """In-process exact token counter with a stable tokenizer identity."""

    identity: str
    count_tokens: Callable[[str], int]

    def __post_init__(self) -> None:
        if not isinstance(self.identity, str) or not self.identity.strip():
            raise TypeError("tokenizer identity must be a non-empty string")
        if len(self.identity) > 256:
            raise ValueError("tokenizer identity exceeds 256 characters")
        if not callable(self.count_tokens):
            raise TypeError("count_tokens must be callable")

    def __call__(self, text: str) -> int:
        value = self.count_tokens(text)
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("exact token counter must return an integer")
        if value < 0:
            raise ValueError("exact token counter cannot return a negative count")
        return value


def _resolve_token_counter(
    adapter: ExactTokenCounterAdapter | Callable[[str], int] | Any | None,
    identity: str | None,
) -> tuple[Callable[[str], int] | None, str | None]:
    if adapter is None:
        if identity is not None:
            raise ValueError("token_counter_id requires an exact token counter adapter")
        return None, None
    if isinstance(adapter, ExactTokenCounterAdapter):
        if identity is not None and identity.strip() != adapter.identity.strip():
            raise ValueError("token_counter_id disagrees with the adapter identity")
        return adapter, adapter.identity.strip()
    if callable(adapter):
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError("a callable token counter requires token_counter_id")
        wrapped = ExactTokenCounterAdapter(identity.strip(), adapter)
        return wrapped, wrapped.identity
    count_method = getattr(adapter, "count_tokens", None)
    inferred_identity = (
        getattr(adapter, "identity", None)
        or getattr(adapter, "tokenizer_id", None)
        or getattr(adapter, "id", None)
    )
    if callable(count_method):
        if identity is None:
            if not isinstance(inferred_identity, str) or not inferred_identity.strip():
                raise ValueError(
                    "an object token counter requires token_counter_id or "
                    "a stable identity attribute"
                )
            chosen_identity = inferred_identity.strip()
        else:
            if not isinstance(identity, str) or not identity.strip():
                raise TypeError("token_counter_id must be a non-empty string")
            chosen_identity = identity.strip()
            if inferred_identity is not None and (
                not isinstance(inferred_identity, str)
                or not inferred_identity.strip()
                or chosen_identity != inferred_identity.strip()
            ):
                raise ValueError("token_counter_id disagrees with the adapter identity")
        wrapped = ExactTokenCounterAdapter(chosen_identity, count_method)
        return wrapped, wrapped.identity
    raise TypeError(
        "exact token adapter must be callable with token_counter_id or expose "
        "count_tokens() and a stable identity"
    )


def source_event_to_record(
    event: SourceEvent | Mapping[str, Any] | Any,
    *,
    default_sequence: int,
) -> SourceRecord:
    """Map a host ``SourceEvent`` to a newly immutable ``SourceRecord``.

    Assistant and tool roles carry a connector-only authentication marker.
    The deterministic extractor and verifier interpret an explicit ``False``
    marker as historical-only data.  Omitting this connector marker elsewhere
    preserves every pre-existing standalone ``ctxc`` authority rule.
    """

    raw = event.to_dict() if isinstance(event, SourceEvent) else _mapping_from_object(
        event,
        label="SourceEvent",
    )
    _exact_fields(
        raw,
        allowed=_EVENT_FIELDS,
        required=frozenset({"role", "content"}),
        label="SourceEvent",
    )
    schema = raw.get("schema")
    if schema is not None and schema != SOURCE_EVENT_SCHEMA:
        raise ValueError(f"unsupported SourceEvent schema: {schema!r}")
    sequence = raw.get("sequence", default_sequence)
    if sequence is None:
        sequence = default_sequence
    metadata = raw.get("metadata", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise TypeError("SourceEvent metadata must be an object")
    metadata = copy.deepcopy(metadata)
    collisions = sorted(_RESERVED_EVENT_METADATA.intersection(metadata))
    if collisions:
        raise ValueError(
            "SourceEvent metadata uses connector-reserved keys: " + ", ".join(collisions)
        )
    role = raw["role"]
    if not isinstance(role, str):
        raise TypeError("SourceEvent role must be a string")
    if not role or role != role.strip():
        raise ValueError("SourceEvent role must be non-empty without surrounding whitespace")
    # Validate any inbound content/record hashes against the ordinary
    # SourceRecord fields before adding connector-owned authority annotations.
    # The mapped record receives a new canonical record hash that also binds
    # those annotations.
    base_record = SourceRecord.create(
        id=raw.get("id"),
        sequence=sequence,
        role=raw["role"],
        content=raw["content"],
        timestamp=raw.get("timestamp"),
        metadata=metadata,
        content_sha256=raw.get("content_sha256", ""),
        record_sha256=raw.get("record_sha256", ""),
    )
    metadata = base_record.to_dict()["metadata"]

    authority = raw.get("authority")
    if authority is not None:
        if not isinstance(authority, dict):
            raise TypeError("SourceEvent authority must be an object or null")
        _exact_fields(
            authority,
            allowed=_AUTHORITY_FIELDS,
            required=frozenset({"authenticated"}),
            label="SourceEvent authority",
        )
        if not isinstance(authority["authenticated"], bool):
            raise TypeError("SourceEvent authority.authenticated must be a boolean")
        trusted_for_state = authority.get("trusted_for_state", False)
        if not isinstance(trusted_for_state, bool):
            raise TypeError("SourceEvent authority.trusted_for_state must be a boolean")
        issuer = authority.get("issuer")
        if issuer is not None:
            if not isinstance(issuer, str):
                raise TypeError(
                    "SourceEvent authority.issuer must be a string or null"
                )
            if not issuer or issuer != issuer.strip():
                raise ValueError("SourceEvent authority.issuer has invalid whitespace")
        metadata["localai_authority"] = copy.deepcopy(authority)

    for field_name, metadata_name in (
        ("redaction", "localai_redaction"),
        ("provenance", "localai_source_provenance"),
    ):
        field_value = raw.get(field_name)
        if field_value is not None:
            if not isinstance(field_value, dict):
                raise TypeError(f"SourceEvent {field_name} must be an object or null")
            metadata[metadata_name] = copy.deepcopy(field_value)

    authenticated = bool(
        isinstance(authority, dict) and authority.get("authenticated") is True
    )
    role_key = role.casefold()
    if role_key in _UNTRUSTED_HISTORY_ROLES:
        metadata[AUTHENTICATED_AUTHORITY_METADATA_KEY] = authenticated
        # Historical metadata cannot self-promote assistant/tool content. Only
        # the host-owned authority envelope can opt a tool into the core's
        # existing trusted-for-state fact path.
        metadata.pop("trusted_for_state", None)
        if (
            authenticated
            and isinstance(authority, dict)
            and authority.get("trusted_for_state") is True
        ):
            metadata["trusted_for_state"] = True

    inbound_record_sha256 = raw.get("record_sha256", "")
    if inbound_record_sha256:
        metadata["localai_original_record_sha256"] = inbound_record_sha256
    return SourceRecord.create(
        id=base_record.id,
        sequence=base_record.sequence,
        role=base_record.role,
        content=base_record.content,
        timestamp=base_record.timestamp,
        metadata=metadata,
        content_sha256=base_record.content_sha256,
    )


def _connector_source_record(record: SourceRecord) -> SourceRecord:
    """Apply connector-only historical trust defaults without changing core use."""

    if not isinstance(record, SourceRecord):
        raise TypeError("connector sources must be SourceRecord values")
    record.ensure_integrity()
    if record.role.strip().casefold() not in _UNTRUSTED_HISTORY_ROLES:
        return record

    metadata = record.to_dict()["metadata"]
    marker = metadata.get(AUTHENTICATED_AUTHORITY_METADATA_KEY)
    if marker is not None and not isinstance(marker, bool):
        raise TypeError(
            f"{AUTHENTICATED_AUTHORITY_METADATA_KEY} must be a boolean when present"
        )
    authority = metadata.get("localai_authority")
    authenticated = bool(
        marker is True
        and isinstance(authority, dict)
        and authority.get("authenticated") is True
    )
    normalized = copy.deepcopy(metadata)
    normalized[AUTHENTICATED_AUTHORITY_METADATA_KEY] = authenticated
    normalized.pop("trusted_for_state", None)
    if (
        authenticated
        and isinstance(authority, dict)
        and authority.get("trusted_for_state") is True
    ):
        normalized["trusted_for_state"] = True
    if normalized == metadata:
        return record
    normalized.setdefault("localai_original_record_sha256", record.record_sha256)
    return SourceRecord.create(
        id=record.id,
        sequence=record.sequence,
        role=record.role,
        content=record.content,
        timestamp=record.timestamp,
        metadata=normalized,
        content_sha256=record.content_sha256,
    )


def _bounded_records(
    values: Iterable[SourceRecord],
    *,
    limits: SourceLimits,
) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for index, record in enumerate(values):
        if index >= limits.max_records:
            raise SourceLimitError(
                f"source record count exceeds {limits.max_records} records"
            )
        records.append(record)
    return records


class IncrementalCompiler:
    """Append source events, compile the current prefix, and checkpoint it.

    Compilation delegates to ``ContextCompiler.compile`` over the exact
    immutable source prefix, so incremental use does not introduce alternate
    extraction, resolution, selection, or verification semantics.  An
    unchanged prefix returns the cached sealed result.
    """

    def __init__(
        self,
        compiler: ContextCompiler | None = None,
        *,
        session_id: str | None = None,
        sources: Iterable[SourceRecord] = (),
        archive_chain_head_sha256: str | None = None,
        archive_head_verified: bool = False,
    ) -> None:
        self.compiler = (
            compiler
            if compiler is not None
            else ContextCompiler(untrusted_historical_roles=True)
        )
        self.session_id = session_id or str(uuid.uuid4())
        if not isinstance(self.session_id, str) or not self.session_id:
            raise TypeError("session_id must be a non-empty string")
        if len(self.session_id) > 256:
            raise ValueError("session_id exceeds 256 characters")
        self.archive_chain_head_sha256 = _validate_sha256(
            archive_chain_head_sha256,
            label="archive_chain_head_sha256",
        )
        if not isinstance(archive_head_verified, bool):
            raise TypeError("archive_head_verified must be a boolean")
        if archive_head_verified and self.archive_chain_head_sha256 is None:
            raise ValueError(
                "archive_head_verified requires archive_chain_head_sha256"
            )
        self.archive_head_verified = archive_head_verified
        bounded_sources = _bounded_records(
            sources,
            limits=self.compiler.source_limits,
        )
        if self.compiler.untrusted_historical_roles:
            bounded_sources = [
                _connector_source_record(source) for source in bounded_sources
            ]
        self._sources = tuple(
            ContextCompiler._prepare_sources(  # noqa: SLF001 - same-package invariant reuse
                bounded_sources,
                limits=self.compiler.source_limits,
            )
        )
        self._cached_digest: str | None = None
        self._cached_memory = None

    @property
    def sources(self) -> tuple[SourceRecord, ...]:
        return self._sources

    @property
    def source_digest(self) -> str:
        return source_digest(self._sources)

    def ingest_source_events(
        self,
        events: Iterable[SourceEvent | Mapping[str, Any] | Any],
    ) -> int:
        existing_by_id = {record.id: record for record in self._sources}
        existing_by_sequence = {record.sequence: record for record in self._sources}
        next_sequence = max(existing_by_sequence, default=-1) + 1
        accepted: list[SourceRecord] = []
        total_size = 0
        for index, existing in enumerate(self._sources):
            total_size = add_source_size(
                total_size,
                source_value_size(
                    existing.to_dict(),
                    limits=self.compiler.source_limits,
                    index=index,
                ),
                limits=self.compiler.source_limits,
            )
        for index, event in enumerate(events):
            if index >= self.compiler.source_limits.max_records:
                raise SourceLimitError(
                    "source record count exceeds "
                    f"{self.compiler.source_limits.max_records} records"
                )
            record = source_event_to_record(event, default_sequence=next_sequence)
            next_sequence = max(next_sequence, record.sequence + 1)
            same_id = existing_by_id.get(record.id)
            if same_id is not None:
                if same_id == record:
                    continue
                raise ValueError(f"immutable source id collision: {record.id}")
            same_sequence = existing_by_sequence.get(record.sequence)
            if same_sequence is not None:
                if same_sequence == record:
                    continue
                raise ValueError(
                    "immutable source sequence collision: "
                    f"{record.sequence} ({same_sequence.id} versus {record.id})"
                )
            if len(self._sources) + len(accepted) >= self.compiler.source_limits.max_records:
                raise SourceLimitError(
                    "source record count exceeds "
                    f"{self.compiler.source_limits.max_records} records"
                )
            existing_by_id[record.id] = record
            existing_by_sequence[record.sequence] = record
            total_size = add_source_size(
                total_size,
                source_value_size(
                    record.to_dict(),
                    limits=self.compiler.source_limits,
                    index=len(self._sources) + len(accepted),
                ),
                limits=self.compiler.source_limits,
            )
            accepted.append(record)
        if accepted:
            self._sources = tuple(
                ContextCompiler._prepare_sources(  # noqa: SLF001 - same-package invariant reuse
                    [*self._sources, *accepted],
                    limits=self.compiler.source_limits,
                )
            )
            self._cached_digest = None
            self._cached_memory = None
        return len(accepted)

    def compile(self, *, timeout_seconds: float | None = None):
        current_digest = self.source_digest
        if (
            timeout_seconds is None
            and self._cached_memory is not None
            and self._cached_digest == current_digest
        ):
            return self._cached_memory
        memory = self.compiler.compile(list(self._sources), timeout_seconds=timeout_seconds)
        if timeout_seconds is None:
            self._cached_digest = current_digest
            self._cached_memory = memory
        return memory

    def checkpoint(self) -> dict[str, Any]:
        payload = {
            "schema": INCREMENTAL_CHECKPOINT_SCHEMA,
            "session_id": self.session_id,
            "source_records": [record.to_dict() for record in self._sources],
            "source_count": len(self._sources),
            "source_digest": self.source_digest,
            "archive_chain_head_sha256": self.archive_chain_head_sha256,
            "archive_head_verified": self.archive_head_verified,
        }
        return {**payload, "checkpoint_sha256": _sha256_json(payload)}

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint: Mapping[str, Any],
        *,
        compiler: ContextCompiler | None = None,
    ) -> IncrementalCompiler:
        if not isinstance(checkpoint, Mapping):
            raise TypeError("checkpoint must be a JSON object")
        raw = copy.deepcopy(dict(checkpoint))
        expected_fields = frozenset(
            {
                "schema",
                "session_id",
                "source_records",
                "source_count",
                "source_digest",
                "archive_chain_head_sha256",
                "archive_head_verified",
                "checkpoint_sha256",
            }
        )
        _exact_fields(
            raw,
            allowed=expected_fields,
            required=expected_fields,
            label="incremental checkpoint",
        )
        if raw["schema"] != INCREMENTAL_CHECKPOINT_SCHEMA:
            raise ValueError(f"unsupported checkpoint schema: {raw['schema']!r}")
        claimed = _validate_sha256(raw["checkpoint_sha256"], label="checkpoint_sha256")
        if (
            _validate_sha256(
                raw["source_digest"],
                label="checkpoint source_digest",
            )
            is None
        ):
            raise TypeError("checkpoint source_digest must be a SHA-256 string")
        if (
            isinstance(raw["source_count"], bool)
            or not isinstance(raw["source_count"], int)
            or raw["source_count"] < 0
        ):
            raise TypeError("checkpoint source_count must be a non-negative integer")
        payload = {key: value for key, value in raw.items() if key != "checkpoint_sha256"}
        if claimed != _sha256_json(payload):
            raise ValueError("incremental checkpoint digest mismatch")
        records = raw["source_records"]
        if not isinstance(records, list):
            raise TypeError("checkpoint source_records must be an array")
        decoded = [
            _canonical_source_record_from_mapping(
                record,
                default_sequence=index,
                label=f"checkpoint source_records[{index}]",
            )
            for index, record in enumerate(records)
        ]
        session = cls(
            compiler=compiler,
            session_id=raw["session_id"],
            sources=decoded,
            archive_chain_head_sha256=raw["archive_chain_head_sha256"],
            archive_head_verified=raw["archive_head_verified"],
        )
        if raw["source_count"] != len(session.sources):
            raise ValueError("checkpoint source_count mismatch")
        if raw["source_digest"] != session.source_digest:
            raise ValueError("checkpoint source_digest mismatch")
        return session


@dataclass(frozen=True, slots=True)
class ContextBundle:
    """Self-hashed LocalAI transport bundle around a compiled artifact."""

    artifact: dict[str, Any]
    trusted_memory: dict[str, Any]
    bindings: dict[str, Any]
    certificate: dict[str, Any]
    token_accounting: dict[str, Any]
    schema: str = CONTEXT_BUNDLE_SCHEMA
    protocol_version: str = CONNECTOR_PROTOCOL_VERSION
    bundle_sha256: str = ""

    def __post_init__(self) -> None:
        if self.schema != CONTEXT_BUNDLE_SCHEMA:
            raise ValueError(f"unsupported ContextBundle schema: {self.schema!r}")
        if self.protocol_version != CONNECTOR_PROTOCOL_VERSION:
            raise ValueError(
                f"unsupported connector protocol version: {self.protocol_version!r}"
            )
        for name in (
            "artifact",
            "trusted_memory",
            "bindings",
            "certificate",
            "token_accounting",
        ):
            if not isinstance(getattr(self, name), dict):
                raise TypeError(f"ContextBundle {name} must be an object")
        _validate_trusted_memory_shape(self.trusted_memory)

        _exact_fields(
            self.bindings,
            allowed=_BINDING_FIELDS,
            required=_BINDING_FIELDS,
            label="ContextBundle bindings",
        )
        _exact_fields(
            self.certificate,
            allowed=_CERTIFICATE_FIELDS,
            required=_CERTIFICATE_FIELDS,
            label="ContextBundle certificate",
        )
        _exact_fields(
            self.token_accounting,
            allowed=_TOKEN_ACCOUNTING_FIELDS,
            required=_TOKEN_ACCOUNTING_FIELDS,
            label="ContextBundle token_accounting",
        )
        if not isinstance(self.bundle_sha256, str):
            raise TypeError("ContextBundle bundle_sha256 must be a string")
        if self.bundle_sha256 and _SHA256.fullmatch(self.bundle_sha256) is None:
            raise ValueError(
                "ContextBundle bundle_sha256 must be 64 lowercase hexadecimal characters"
            )
        if (
            not isinstance(self.bindings["session_id"], str)
            or not self.bindings["session_id"]
        ):
            raise TypeError("ContextBundle bindings.session_id must be a non-empty string")
        if len(self.bindings["session_id"]) > 256:
            raise ValueError(
                "ContextBundle bindings.session_id exceeds 256 characters"
            )
        if _validate_sha256(
            self.bindings["source_digest"],
            label="ContextBundle bindings.source_digest",
        ) is None:
            raise TypeError("ContextBundle bindings.source_digest cannot be null")
        source_count = self.bindings["source_count"]
        if (
            isinstance(source_count, bool)
            or not isinstance(source_count, int)
            or source_count < 0
        ):
            raise TypeError(
                "ContextBundle bindings.source_count must be a non-negative integer"
            )
        _validate_sha256(
            self.bindings["archive_chain_head_sha256"],
            label="ContextBundle bindings.archive_chain_head_sha256",
        )
        if not isinstance(self.bindings["archive_head_verified"], bool):
            raise TypeError(
                "ContextBundle bindings.archive_head_verified must be a boolean"
            )
        if (
            self.bindings["archive_head_verified"]
            and self.bindings["archive_chain_head_sha256"] is None
        ):
            raise ValueError(
                "ContextBundle cannot claim a verified archive without a chain head"
            )
        if not isinstance(self.bindings["compiler_policy"], dict):
            raise TypeError("ContextBundle bindings.compiler_policy must be an object")
        _exact_fields(
            self.bindings["compiler_policy"],
            allowed=_POLICY_FIELDS,
            required=_POLICY_FIELDS,
            label="ContextBundle bindings.compiler_policy",
        )
        _policy_from_value(
            self.bindings["compiler_policy"],
            fallback=CompilationPolicy(),
        )
        for name in (
            "compiler_policy_sha256",
            "rendered_memory_sha256",
            "artifact_sha256",
        ):
            if _validate_sha256(
                self.bindings[name],
                label=f"ContextBundle bindings.{name}",
            ) is None:
                raise TypeError(f"ContextBundle bindings.{name} cannot be null")
        if not isinstance(self.bindings["token_accounting_exact"], bool):
            raise TypeError(
                "ContextBundle bindings.token_accounting_exact must be a boolean"
            )
        if self.bindings["token_accounting"] not in {"exact", "estimated"}:
            raise ValueError("ContextBundle binding token accounting mode is invalid")
        if (
            not isinstance(self.bindings["tokenizer_identity"], str)
            or not self.bindings["tokenizer_identity"]
        ):
            raise TypeError(
                "ContextBundle bindings.tokenizer_identity must be a non-empty string"
            )
        if len(self.bindings["tokenizer_identity"]) > 256:
            raise ValueError(
                "ContextBundle bindings.tokenizer_identity exceeds 256 characters"
            )

        if not isinstance(self.certificate["issued"], bool):
            raise TypeError("ContextBundle certificate.issued must be a boolean")
        expected_claim = (
            RETENTION_CERTIFICATE_WORDING
            if self.certificate["issued"]
            else None
        )
        if self.certificate["claim"] != expected_claim:
            raise ValueError("ContextBundle certificate claim has invalid wording")
        if self.certificate["semantic_completeness_claimed"] is not False:
            raise ValueError("ContextBundle cannot claim semantic completeness")
        if self.certificate["scope"] != "detector-scoped protected commitments":
            raise ValueError("ContextBundle certificate scope is invalid")
        for name in (
            "detected_protected_commitments",
            "retained_detected_protected_commitments",
        ):
            value = self.certificate[name]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise TypeError(
                    f"ContextBundle certificate.{name} must be a non-negative integer"
                )
        if self.token_accounting["mode"] not in {"exact", "estimated"}:
            raise ValueError("ContextBundle token accounting mode is invalid")
        if not isinstance(self.token_accounting["exact"], bool):
            raise TypeError("ContextBundle token accounting exact flag must be boolean")
        if (
            self.token_accounting["exact"]
            is not (self.token_accounting["mode"] == "exact")
        ):
            raise ValueError("ContextBundle token accounting mode and flag disagree")
        for name in ("source_tokens", "rendered_tokens"):
            value = self.token_accounting[name]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise TypeError(
                    f"ContextBundle token_accounting.{name} must be a non-negative integer"
                )
        if (
            not isinstance(self.token_accounting["tokenizer_identity"], str)
            or not self.token_accounting["tokenizer_identity"]
        ):
            raise TypeError(
                "ContextBundle token_accounting.tokenizer_identity "
                "must be a non-empty string"
            )
        if len(self.token_accounting["tokenizer_identity"]) > 256:
            raise ValueError(
                "ContextBundle token_accounting.tokenizer_identity "
                "exceeds 256 characters"
            )

        validate_artifact_envelope(self.artifact)
        actual = _sha256_json(self._unsigned_dict())
        if self.bundle_sha256 and self.bundle_sha256 != actual:
            raise ValueError("ContextBundle digest mismatch")
        object.__setattr__(self, "artifact", _detached_json(self.artifact))
        object.__setattr__(self, "trusted_memory", _detached_json(self.trusted_memory))
        object.__setattr__(self, "bindings", _detached_json(self.bindings))
        object.__setattr__(self, "certificate", _detached_json(self.certificate))
        object.__setattr__(self, "token_accounting", _detached_json(self.token_accounting))
        object.__setattr__(self, "bundle_sha256", actual)

    def _unsigned_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "protocol_version": self.protocol_version,
            "artifact": self.artifact,
            "trusted_memory": self.trusted_memory,
            "bindings": self.bindings,
            "certificate": self.certificate,
            "token_accounting": self.token_accounting,
        }

    def _assert_integrity(self) -> None:
        if _sha256_json(self._unsigned_dict()) != self.bundle_sha256:
            raise ValueError("ContextBundle changed after creation")

    def to_dict(self) -> dict[str, Any]:
        self._assert_integrity()
        return _detached_json({**self._unsigned_dict(), "bundle_sha256": self.bundle_sha256})

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ContextBundle:
        if not isinstance(value, Mapping):
            raise TypeError("ContextBundle must be a JSON object")
        raw = copy.deepcopy(dict(value))
        expected = frozenset(
            {
                "schema",
                "protocol_version",
                "artifact",
                "trusted_memory",
                "bindings",
                "certificate",
                "token_accounting",
                "bundle_sha256",
            }
        )
        _exact_fields(
            raw,
            allowed=expected,
            required=expected,
            label="ContextBundle",
        )
        claimed = raw["bundle_sha256"]
        if not isinstance(claimed, str) or _SHA256.fullmatch(claimed) is None:
            raise ValueError(
                "serialized ContextBundle requires a 64-character lowercase "
                "bundle_sha256"
            )
        return cls(
            schema=raw["schema"],
            protocol_version=raw["protocol_version"],
            artifact=raw["artifact"],
            trusted_memory=raw["trusted_memory"],
            bindings=raw["bindings"],
            certificate=raw["certificate"],
            token_accounting=raw["token_accounting"],
            bundle_sha256=raw["bundle_sha256"],
        )


def _coerce_bundle(value: ContextBundle | Mapping[str, Any]) -> ContextBundle:
    if isinstance(value, ContextBundle):
        value._assert_integrity()
        return value
    return ContextBundle.from_dict(value)


def _memory_item_payload(item: MemoryItem) -> dict[str, Any]:
    return item.to_dict()


def _trusted_memory_section(
    *,
    items: list[MemoryItem],
    selected_item_ids: list[str],
    sources: tuple[SourceRecord, ...],
    protected_budget_overflow: int,
) -> dict[str, Any]:
    selected = set(selected_item_ids)
    active = [
        item
        for item in items
        if item.id in selected
        and item.status not in {MemoryStatus.SUPERSEDED, MemoryStatus.DISCARDED}
    ]
    categories = {
        "active_goals": MemoryKind.GOAL,
        "constraints": MemoryKind.CONSTRAINT,
        "user_corrections": MemoryKind.USER_CORRECTION,
        "decisions": MemoryKind.DECISION,
        "confirmed_facts": MemoryKind.CONFIRMED_FACT,
        "unresolved_questions": MemoryKind.UNRESOLVED,
        "exact_errors": MemoryKind.EXACT_ERROR,
        "exact_references": MemoryKind.EXACT_REFERENCE,
    }
    section: dict[str, Any] = {
        name: [
            _memory_item_payload(item)
            for item in active
            if item.kind == kind
        ]
        for name, kind in categories.items()
    }
    included = [
        item
        for item in active
        if item.kind in set(categories.values())
    ]
    spans_by_identity: dict[tuple[str, int, int, str], dict[str, Any]] = {}
    for item in included:
        for span in item.provenance:
            key = (span.source_id, span.start, span.end, span.quote_sha256)
            spans_by_identity[key] = span.to_dict()
    section["source_spans"] = [
        spans_by_identity[key]
        for key in sorted(spans_by_identity)
    ]
    section["source_hashes"] = [
        {
            "source_id": source.id,
            "sequence": source.sequence,
            "content_sha256": source.content_sha256,
            "record_sha256": source.record_sha256,
        }
        for source in sources
    ]

    active_protected = [
        item
        for item in items
        if item.protected
        and item.status not in {MemoryStatus.SUPERSEDED, MemoryStatus.DISCARDED}
    ]
    omitted_or_overflowed: list[dict[str, Any]] = [
        {
            "reason": "omitted",
            "item": _memory_item_payload(item),
            "overflow_tokens": 0,
        }
        for item in active_protected
        if item.id not in selected
    ]
    if protected_budget_overflow:
        omitted_or_overflowed.extend(
            {
                "reason": "protected_budget_overflow",
                "item": _memory_item_payload(item),
                "overflow_tokens": protected_budget_overflow,
            }
            for item in active_protected
            if item.id in selected
        )
    section["omitted_or_overflowed_protected_items"] = omitted_or_overflowed
    return section


class LocalAIConnector:
    """Framework-neutral connector over the existing compiler and verifier."""

    def __init__(
        self,
        *,
        policy: CompilationPolicy | None = None,
        token_counter: ExactTokenCounterAdapter | Callable[[str], int] | Any | None = None,
        token_counter_id: str | None = None,
        source_limits: SourceLimits | None = None,
        compilation_limits: CompilationLimits | None = None,
        artifact_limits: ArtifactLimits | None = None,
        source_archive: SourceArchive | None = None,
    ) -> None:
        self.policy = policy if policy is not None else CompilationPolicy()
        if not isinstance(self.policy, CompilationPolicy):
            raise TypeError("policy must be a CompilationPolicy")
        self.token_counter, self.token_counter_id = _resolve_token_counter(
            token_counter,
            token_counter_id,
        )
        self.source_limits = resolve_source_limits(source_limits)
        self.compilation_limits = resolve_compilation_limits(compilation_limits)
        self.artifact_limits = resolve_artifact_limits(artifact_limits)
        if source_archive is not None and not isinstance(source_archive, SourceArchive):
            raise TypeError("source_archive must be a SourceArchive or null")
        self.source_archive = source_archive
        self._sessions: dict[str, IncrementalCompiler] = {}
        self._archive_session_id: str | None = None

    def _compiler(self, policy: CompilationPolicy | None = None) -> ContextCompiler:
        return ContextCompiler(
            policy=policy or self.policy,
            token_counter=self.token_counter,
            token_counter_id=self.token_counter_id,
            source_limits=self.source_limits,
            compilation_limits=self.compilation_limits,
            untrusted_historical_roles=True,
        )

    def _prepare_connector_sources(
        self,
        sources: Iterable[SourceRecord],
    ) -> tuple[SourceRecord, ...]:
        bounded = _bounded_records(sources, limits=self.source_limits)
        normalized = [_connector_source_record(source) for source in bounded]
        return tuple(
            ContextCompiler._prepare_sources(  # noqa: SLF001
                normalized,
                limits=self.source_limits,
            )
        )

    def _restore_checkpoint(
        self,
        checkpoint: Mapping[str, Any],
    ) -> IncrementalCompiler:
        # Authenticate the checkpoint against its exact original source bytes
        # before applying connector-owned historical trust annotations. This
        # accepts standalone checkpoints without letting unmarked assistant or
        # tool records retain standalone authority inside the connector.
        decoded = IncrementalCompiler.from_checkpoint(
            checkpoint,
            compiler=ContextCompiler(
                policy=self.policy,
                token_counter=self.token_counter,
                token_counter_id=self.token_counter_id,
                source_limits=self.source_limits,
                compilation_limits=self.compilation_limits,
            ),
        )
        session = IncrementalCompiler(
            self._compiler(),
            session_id=decoded.session_id,
            sources=decoded.sources,
            archive_chain_head_sha256=decoded.archive_chain_head_sha256,
            archive_head_verified=False,
        )
        session.archive_head_verified = self._archive_attests(
            session.sources,
            session.archive_chain_head_sha256,
        )
        return session

    def _archive_attests(
        self,
        sources: Iterable[SourceRecord],
        chain_head_sha256: str | None,
    ) -> bool:
        if self.source_archive is None or chain_head_sha256 is None:
            return False
        try:
            report = self.source_archive.verify(
                expected_chain_head=chain_head_sha256
            )
            if (
                not report.passed
                or report.chain_head_sha256 != chain_head_sha256
            ):
                return False
            archived = self._prepare_connector_sources(
                self.source_archive.load(
                    expected_chain_head=chain_head_sha256
                )
            )
            prepared = self._prepare_connector_sources(sources)
        except (OSError, TypeError, ValueError):
            return False
        return (
            len(archived) == len(prepared)
            and source_digest(archived) == source_digest(prepared)
        )

    def _synchronize_archive_session(
        self,
        session: IncrementalCompiler,
        *,
        expected_chain_head: str | None,
    ) -> IncrementalCompiler:
        if self.source_archive is None:
            raise RuntimeError("source archive synchronization requires an archive")
        if (
            self._archive_session_id is not None
            and self._archive_session_id != session.session_id
        ):
            raise ValueError(
                "the configured SourceArchive is already bound to connector "
                f"session {self._archive_session_id!r}"
            )
        candidate = IncrementalCompiler.from_checkpoint(
            session.checkpoint(),
            compiler=session.compiler,
        )
        candidate._sources = self._prepare_connector_sources(  # noqa: SLF001
            candidate.sources
        )
        archive_precondition = expected_chain_head
        if archive_precondition is None and session.archive_head_verified:
            archive_precondition = session.archive_chain_head_sha256
        existing_archived = self.source_archive.load(
            expected_chain_head=archive_precondition
        )
        normalized_existing = self._prepare_connector_sources(
            existing_archived
        )
        existing_by_id = {record.id: record for record in normalized_existing}
        append_records: list[SourceRecord] = []
        for record in candidate.sources:
            archived_record = existing_by_id.get(record.id)
            if archived_record is None:
                append_records.append(record)
            elif archived_record != record:
                raise ValueError(f"immutable source id collision: {record.id}")
        self.source_archive.append(
            append_records,
            expected_chain_head=archive_precondition,
        )
        report = self.source_archive.verify()
        if not report.passed or report.chain_head_sha256 is None:
            raise ValueError("configured source archive failed verification")
        archived_sources = self._prepare_connector_sources(
            self.source_archive.load(
                expected_chain_head=report.chain_head_sha256
            )
        )
        candidate._sources = archived_sources  # noqa: SLF001
        candidate._cached_digest = None  # noqa: SLF001
        candidate._cached_memory = None  # noqa: SLF001
        candidate.archive_chain_head_sha256 = report.chain_head_sha256
        candidate.archive_head_verified = True
        return candidate

    def _new_session(
        self,
        *,
        session_id: str | None = None,
        checkpoint: Mapping[str, Any] | None = None,
        register: bool = True,
    ) -> IncrementalCompiler:
        if checkpoint is not None:
            session = self._restore_checkpoint(checkpoint)
            if session_id is not None and session_id != session.session_id:
                raise ValueError("session_id disagrees with the checkpoint")
        else:
            session = IncrementalCompiler(
                self._compiler(),
                session_id=session_id,
            )
        existing = self._sessions.get(session.session_id)
        if existing is not None:
            same_state = (
                existing.source_digest == session.source_digest
                and existing.archive_chain_head_sha256
                == session.archive_chain_head_sha256
                and existing.archive_head_verified
                is session.archive_head_verified
            )
            if not same_state:
                raise ValueError(
                    "checkpoint conflicts with a live connector session; "
                    "refusing rollback or fork replacement"
                )
            return existing
        if register:
            self._sessions[session.session_id] = session
        return session

    def _session(
        self,
        *,
        session_id: str | None,
        checkpoint: Mapping[str, Any] | None = None,
        create: bool = False,
        register: bool = True,
    ) -> IncrementalCompiler:
        if checkpoint is not None:
            return self._new_session(
                session_id=session_id,
                checkpoint=checkpoint,
                register=register,
            )
        if session_id is not None and session_id in self._sessions:
            return self._sessions[session_id]
        if create:
            return self._new_session(session_id=session_id, register=register)
        if session_id is None:
            raise ValueError("session_id or checkpoint is required")
        raise KeyError(f"unknown connector session: {session_id}")

    def capabilities(self) -> dict[str, Any]:
        return {
            "protocol_version": CONNECTOR_PROTOCOL_VERSION,
            "request_schema": CONNECTOR_REQUEST_SCHEMA,
            "response_schema": CONNECTOR_RESPONSE_SCHEMA,
            "source_event_schema": SOURCE_EVENT_SCHEMA,
            "context_bundle_schema": CONTEXT_BUNDLE_SCHEMA,
            "checkpoint_schema": INCREMENTAL_CHECKPOINT_SCHEMA,
            "operations": list(CONNECTOR_OPERATIONS),
            "transport": {
                "stdio_jsonl": True,
                "one_request_per_line": True,
                "plain_json_process_boundary": True,
            },
            "incremental": {
                "supported": True,
                "checkpoint_resume": True,
                "batch_semantics_preserved": True,
            },
            "token_accounting": {
                "exact_available": self.token_counter is not None,
                "tokenizer_identity": (
                    self.token_counter_id
                    if self.token_counter is not None
                    else "character-estimate-v1"
                ),
                "mode": "exact" if self.token_counter is not None else "estimated",
            },
            "authority": {
                "assistant_and_tool_default": "untrusted_historical",
                "authenticated_authority_supported": True,
                "tool_state_scope": "confirmed_fact_only",
            },
            "certificate": {
                "wording": RETENTION_CERTIFICATE_WORDING,
                "detector_scoped": True,
                "semantic_completeness_claimed": False,
            },
            "optional_dependencies": {
                "localai_contracts_required": False,
            },
        }

    def ingest_source_events(
        self,
        events: Iterable[SourceEvent | Mapping[str, Any] | Any],
        *,
        session_id: str | None = None,
        checkpoint: Mapping[str, Any] | None = None,
        archive_chain_head_sha256: str | None = None,
    ) -> dict[str, Any]:
        session = self._session(
            session_id=session_id,
            checkpoint=checkpoint,
            create=True,
            register=False,
        )
        expected_archive_head = _validate_sha256(
            archive_chain_head_sha256,
            label="archive_chain_head_sha256",
        )
        if self.source_archive is not None:
            # Load and verify the persisted prefix before assigning default
            # event sequences. Restarts therefore continue after the archive
            # rather than colliding with its first record.
            session = self._synchronize_archive_session(
                session,
                expected_chain_head=expected_archive_head,
            )
        # Stage the update in a detached session. A source-limit, identity, or
        # archive failure therefore cannot partially advance the live prefix.
        candidate = IncrementalCompiler.from_checkpoint(
            session.checkpoint(),
            compiler=session.compiler,
        )
        accepted = candidate.ingest_source_events(events)
        if self.source_archive is not None:
            candidate = self._synchronize_archive_session(
                candidate,
                expected_chain_head=session.archive_chain_head_sha256,
            )
            self._archive_session_id = candidate.session_id
        elif expected_archive_head is not None:
            if (
                candidate.archive_chain_head_sha256 is not None
                and candidate.archive_chain_head_sha256 != expected_archive_head
            ):
                raise ValueError("archive chain head disagrees with the session checkpoint")
            candidate.archive_chain_head_sha256 = expected_archive_head
            candidate.archive_head_verified = False
        self._sessions[candidate.session_id] = candidate
        checkpoint_value = candidate.checkpoint()
        return {
            "session_id": candidate.session_id,
            "accepted_events": accepted,
            "source_count": len(candidate.sources),
            "source_digest": candidate.source_digest,
            "source_records": [source.to_dict() for source in candidate.sources],
            "archive_chain_head_sha256": candidate.archive_chain_head_sha256,
            "archive_head_verified": candidate.archive_head_verified,
            "checkpoint": checkpoint_value,
        }

    def compile_memory(
        self,
        *,
        session_id: str | None = None,
        checkpoint: Mapping[str, Any] | None = None,
        events: Iterable[SourceEvent | Mapping[str, Any] | Any] | None = None,
        policy: CompilationPolicy | Mapping[str, Any] | None = None,
        archive_chain_head_sha256: str | None = None,
        timeout_seconds: float | None = None,
    ) -> ContextBundle:
        effective_policy = _policy_from_value(policy, fallback=self.policy)
        if not effective_policy.verify:
            raise ValueError(
                "connector compile policy must require verification"
            )
        requested_head = _validate_sha256(
            archive_chain_head_sha256,
            label="archive_chain_head_sha256",
        )
        if events is not None:
            ingested = self.ingest_source_events(
                events,
                session_id=session_id,
                checkpoint=checkpoint,
                archive_chain_head_sha256=requested_head,
            )
            session = self._sessions[ingested["session_id"]]
        else:
            base_session = self._session(
                session_id=session_id,
                checkpoint=checkpoint,
                register=False,
            )
            if self.source_archive is not None:
                session = self._synchronize_archive_session(
                    base_session,
                    expected_chain_head=requested_head,
                )
                self._archive_session_id = session.session_id
            else:
                session = IncrementalCompiler.from_checkpoint(
                    base_session.checkpoint(),
                    compiler=base_session.compiler,
                )
                session._sources = self._prepare_connector_sources(  # noqa: SLF001
                    session.sources
                )
        if requested_head is not None and self.source_archive is None:
            if (
                session.archive_chain_head_sha256 is not None
                and session.archive_chain_head_sha256 != requested_head
            ):
                raise ValueError("archive chain head disagrees with the session")
            session.archive_chain_head_sha256 = requested_head
        compiler = self._compiler(effective_policy)
        memory = compiler.compile(
            list(session.sources),
            timeout_seconds=timeout_seconds,
            )
        artifact = memory.to_dict()
        rendered = memory.to_prompt()
        metrics = memory.compiler_metadata.get("metrics", {})
        protected_overflow = (
            metrics.get("protected_budget_overflow", 0)
            if isinstance(metrics, dict)
            else 0
        )
        trusted_memory = _trusted_memory_section(
            items=list(memory.items),
            selected_item_ids=list(memory.selected_item_ids),
            sources=session.sources,
            protected_budget_overflow=protected_overflow,
        )
        policy_value = _policy_to_dict(effective_policy)
        policy_digest = _sha256_json(policy_value)
        artifact["compiler_metadata"]["connector_policy"] = copy.deepcopy(
            policy_value
        )
        artifact["compiler_metadata"]["connector_policy_sha256"] = policy_digest
        artifact["artifact_sha256"] = _sha256_json(
            {
                key: value
                for key, value in artifact.items()
                if key != "artifact_sha256"
            }
        )
        tokenizer_identity = (
            self.token_counter_id
            if self.token_counter is not None
            else "character-estimate-v1"
        )
        token_mode = "exact" if self.token_counter is not None else "estimated"
        bindings = {
            "session_id": session.session_id,
            "source_digest": memory.source_digest,
            "source_count": memory.source_count,
            "archive_chain_head_sha256": session.archive_chain_head_sha256,
            "archive_head_verified": session.archive_head_verified,
            "compiler_policy": policy_value,
            "compiler_policy_sha256": policy_digest,
            "tokenizer_identity": tokenizer_identity,
            "token_accounting": token_mode,
            "token_accounting_exact": self.token_counter is not None,
            "rendered_memory_sha256": _sha256_text(rendered),
            "artifact_sha256": artifact["artifact_sha256"],
        }
        detected = memory.verification.protected_candidates
        retained = memory.verification.protected_retained
        certificate_issued = (
            memory.verification.passed
            and detected == retained
            and not any(
                entry["reason"] == "omitted"
                for entry in trusted_memory[
                    "omitted_or_overflowed_protected_items"
                ]
            )
        )
        certificate = {
            "issued": certificate_issued,
            "claim": (
                RETENTION_CERTIFICATE_WORDING if certificate_issued else None
            ),
            "scope": "detector-scoped protected commitments",
            "detected_protected_commitments": detected,
            "retained_detected_protected_commitments": retained,
            "semantic_completeness_claimed": False,
        }
        token_accounting = {
            "mode": token_mode,
            "exact": self.token_counter is not None,
            "tokenizer_identity": tokenizer_identity,
            "source_tokens": memory.compression.source_tokens_estimate,
            "rendered_tokens": memory.compression.active_tokens_estimate,
            "field_name_compatibility_note": _TOKEN_FIELD_COMPATIBILITY_NOTE,
        }
        bundle = ContextBundle(
            artifact=artifact,
            trusted_memory=trusted_memory,
            bindings=bindings,
            certificate=certificate,
            token_accounting=token_accounting,
        )
        self._sessions[session.session_id] = session
        return bundle

    def _require_trusted_memory_artifact_alignment(
        self,
        bundle: ContextBundle,
        decoded_items: list[MemoryItem],
    ) -> None:
        artifact = bundle.artifact
        metadata = artifact.get("compiler_metadata")
        metrics = metadata.get("metrics") if isinstance(metadata, dict) else None
        protected_overflow = (
            metrics.get("protected_budget_overflow", 0)
            if isinstance(metrics, dict)
            else 0
        )
        expected = _trusted_memory_section(
            items=decoded_items,
            selected_item_ids=artifact["selected_item_ids"],
            sources=(),
            protected_budget_overflow=protected_overflow,
        )
        artifact_bound_fields = (
            *tuple(_TRUSTED_MEMORY_CATEGORY_KINDS),
            "source_spans",
            "omitted_or_overflowed_protected_items",
        )
        if any(
            _sha256_json(bundle.trusted_memory[name])
            != _sha256_json(expected[name])
            for name in artifact_bound_fields
        ):
            raise ValueError(
                "ContextBundle trusted_memory does not match its artifact"
            )

        source_hashes = bundle.trusted_memory["source_hashes"]
        if len(source_hashes) != artifact["source_count"]:
            raise ValueError(
                "ContextBundle trusted_memory source hashes do not match "
                "its artifact source count"
            )
        source_ids = [entry["source_id"] for entry in source_hashes]
        source_sequences = [entry["sequence"] for entry in source_hashes]
        if (
            len(source_ids) != len(set(source_ids))
            or len(source_sequences) != len(set(source_sequences))
        ):
            raise ValueError(
                "ContextBundle trusted_memory source hashes must have unique "
                "source ids and sequences"
            )
        referenced_source_ids = {
            span.source_id
            for item in decoded_items
            for span in item.provenance
        }
        if not referenced_source_ids.issubset(source_ids):
            raise ValueError(
                "ContextBundle trusted_memory source hashes omit artifact provenance"
            )
        if (
            bundle.bindings["source_digest"] != artifact["source_digest"]
            or bundle.bindings["source_count"] != artifact["source_count"]
        ):
            raise ValueError(
                "ContextBundle source binding does not match its artifact"
            )

    def _require_supported_bundle_claims(self, bundle: ContextBundle) -> None:
        bindings = bundle.bindings
        metadata = bundle.artifact.get("compiler_metadata")
        if (
            not isinstance(metadata, dict)
            or metadata.get("connector_untrusted_historical_roles") is not True
        ):
            raise ValueError(
                "ContextBundle artifact is missing the connector historical-trust policy"
            )
        policy = bindings["compiler_policy"]
        policy_digest = _sha256_json(policy)
        if bindings["compiler_policy_sha256"] != policy_digest:
            raise ValueError("ContextBundle compiler policy digest is invalid")
        if (
            metadata.get("connector_policy_sha256") != policy_digest
            or _sha256_json(metadata.get("connector_policy"))
            != policy_digest
        ):
            raise ValueError(
                "ContextBundle compiler policy does not match its artifact"
            )
        policy_issues = _policy_artifact_claim_issues(policy, bundle.artifact)
        if policy_issues:
            raise ValueError(policy_issues[0][1])
        if bindings["artifact_sha256"] != bundle.artifact["artifact_sha256"]:
            raise ValueError("ContextBundle artifact digest binding is invalid")
        decoded_items = [
            MemoryItem.from_dict(item) for item in bundle.artifact["items"]
        ]
        self._require_trusted_memory_artifact_alignment(bundle, decoded_items)
        rendered = render_typed_memory(
            decoded_items,
            bundle.artifact["selected_item_ids"],
        )
        if bindings["rendered_memory_sha256"] != _sha256_text(rendered):
            raise ValueError(
                "ContextBundle rendered memory digest binding is invalid"
            )

        exact = bindings["token_accounting_exact"]
        mode = bindings["token_accounting"]
        identity = bindings["tokenizer_identity"]
        if exact is not (mode == "exact"):
            raise ValueError("ContextBundle token accounting binding is inconsistent")
        if (
            bundle.token_accounting["exact"] is not exact
            or bundle.token_accounting["mode"] != mode
            or bundle.token_accounting["tokenizer_identity"] != identity
        ):
            raise ValueError(
                "ContextBundle token accounting details disagree with their binding"
            )
        if exact:
            if self.token_counter is None or identity != self.token_counter_id:
                raise ValueError(
                    "exact accounting requires the matching in-process "
                    "tokenizer adapter"
                )
        elif identity != "character-estimate-v1":
            raise ValueError(
                "estimated accounting must identify character-estimate-v1"
            )
        expected_accounting = {
            "mode": mode,
            "exact": exact,
            "tokenizer_identity": identity,
            "source_tokens": bundle.artifact["compression"][
                "source_tokens_estimate"
            ],
            "rendered_tokens": bundle.artifact["compression"][
                "active_tokens_estimate"
            ],
            "field_name_compatibility_note": _TOKEN_FIELD_COMPATIBILITY_NOTE,
        }
        if _sha256_json(bundle.token_accounting) != _sha256_json(
            expected_accounting
        ):
            raise ValueError(
                "ContextBundle token accounting does not match its artifact"
            )
        verification = bundle.artifact["verification"]
        detected = verification["protected_candidates"]
        retained = verification["protected_retained"]
        omitted = bundle.trusted_memory["omitted_or_overflowed_protected_items"]
        certificate_issued = (
            verification["passed"] is True
            and detected == retained
            and not any(
                isinstance(entry, dict) and entry.get("reason") == "omitted"
                for entry in omitted
            )
        )
        expected_certificate = {
            "issued": certificate_issued,
            "claim": (
                RETENTION_CERTIFICATE_WORDING if certificate_issued else None
            ),
            "scope": "detector-scoped protected commitments",
            "detected_protected_commitments": detected,
            "retained_detected_protected_commitments": retained,
            "semantic_completeness_claimed": False,
        }
        if _sha256_json(bundle.certificate) != _sha256_json(expected_certificate):
            raise ValueError(
                "ContextBundle retention certificate does not match its artifact"
            )

    def render_context(
        self,
        bundle: ContextBundle | Mapping[str, Any],
    ) -> str:
        checked = _coerce_bundle(bundle)
        self._require_supported_bundle_claims(checked)
        artifact = validate_artifact_envelope(
            checked.artifact,
            limits=self.artifact_limits,
        )
        verification = artifact.get("verification")
        if not isinstance(verification, dict) or verification.get("passed") is not True:
            raise ValueError("refusing to render context from unverified memory")
        items = [MemoryItem.from_dict(item) for item in artifact["items"]]
        rendered = render_typed_memory(items, artifact["selected_item_ids"])
        expected = checked.bindings.get("rendered_memory_sha256")
        if expected != _sha256_text(rendered):
            raise ValueError("rendered memory digest does not match ContextBundle binding")
        return rendered

    def _verification_sources(
        self,
        bundle: ContextBundle,
        *,
        session_id: str | None,
        checkpoint: Mapping[str, Any] | None,
        source_records: Iterable[SourceRecord | Mapping[str, Any]] | None,
        events: Iterable[SourceEvent | Mapping[str, Any] | Any] | None,
    ) -> tuple[tuple[SourceRecord, ...], IncrementalCompiler | None]:
        supplied = sum(
            value is not None
            for value in (checkpoint, source_records, events)
        )
        if supplied > 1:
            raise ValueError(
                "verify_memory accepts only one of checkpoint, source_records, or events"
            )
        if checkpoint is not None:
            # Verification must not mutate or roll back a live session. Decode
            # the checkpoint into a detached replay session instead.
            session = self._restore_checkpoint(checkpoint)
            if session_id is not None and session.session_id != session_id:
                raise ValueError("session_id disagrees with the checkpoint")
            return session.sources, session
        if source_records is not None:
            decoded: list[SourceRecord] = []
            for index, value in enumerate(source_records):
                if index >= self.source_limits.max_records:
                    raise SourceLimitError(
                        "source record count exceeds "
                        f"{self.source_limits.max_records} records"
                    )
                if isinstance(value, SourceRecord):
                    decoded.append(value)
                elif isinstance(value, Mapping):
                    decoded.append(
                        _canonical_source_record_from_mapping(
                            value,
                            default_sequence=index,
                            label=f"source_records[{index}]",
                        )
                    )
                else:
                    raise TypeError(
                        "source_records must contain SourceRecord or JSON object values"
                    )
            return self._prepare_connector_sources(decoded), None
        if events is not None:
            temporary = IncrementalCompiler(
                self._compiler(),
                session_id=session_id or bundle.bindings.get("session_id"),
            )
            temporary.ingest_source_events(events)
            return temporary.sources, temporary
        bound_session_id = session_id or bundle.bindings.get("session_id")
        if isinstance(bound_session_id, str) and bound_session_id in self._sessions:
            session = self._sessions[bound_session_id]
            return session.sources, session
        raise ValueError(
            "trusted sources are required through a live session, checkpoint, "
            "source_records, or SourceEvents"
        )

    def verify_memory(
        self,
        bundle: ContextBundle | Mapping[str, Any],
        *,
        session_id: str | None = None,
        checkpoint: Mapping[str, Any] | None = None,
        source_records: Iterable[SourceRecord | Mapping[str, Any]] | None = None,
        events: Iterable[SourceEvent | Mapping[str, Any] | Any] | None = None,
        expected_archive_chain_head_sha256: str | None = None,
    ) -> dict[str, Any]:
        checked = _coerce_bundle(bundle)
        sources, session = self._verification_sources(
            checked,
            session_id=session_id,
            checkpoint=checkpoint,
            source_records=source_records,
            events=events,
        )
        report = verify_artifact_dict(
            checked.artifact,
            list(sources),
            token_counter=self.token_counter,
            token_counter_id=self.token_counter_id,
            source_limits=self.source_limits,
            artifact_limits=self.artifact_limits,
            untrusted_historical_roles=True,
        )
        binding_issues: list[dict[str, Any]] = []

        def binding_issue(code: str, message: str) -> None:
            binding_issues.append(
                {
                    "code": code,
                    "severity": "error",
                    "message": message,
                }
            )

        bindings = checked.bindings
        trusted_session_id = session.session_id if session is not None else session_id
        if (
            trusted_session_id is not None
            and bindings.get("session_id") != trusted_session_id
        ):
            binding_issue(
                "bundle_session_id_mismatch",
                "ContextBundle session_id differs from the trusted replay session.",
            )
        if bindings.get("source_digest") != source_digest(sources):
            binding_issue(
                "bundle_source_digest_mismatch",
                "ContextBundle is not bound to the supplied immutable source set.",
            )
        if bindings.get("source_count") != len(sources):
            binding_issue(
                "bundle_source_count_mismatch",
                "ContextBundle source_count does not match the supplied sources.",
            )
        artifact_digest = checked.artifact.get("artifact_sha256")
        if bindings.get("artifact_sha256") != artifact_digest:
            binding_issue(
                "bundle_artifact_digest_mismatch",
                "ContextBundle artifact binding does not match the embedded artifact.",
            )
        policy_value = bindings.get("compiler_policy")
        if not isinstance(policy_value, dict):
            binding_issue(
                "invalid_bundle_policy",
                "ContextBundle compiler_policy binding must be an object.",
            )
        elif bindings.get("compiler_policy_sha256") != _sha256_json(policy_value):
            binding_issue(
                "bundle_policy_digest_mismatch",
                "ContextBundle compiler policy digest is invalid.",
            )
        artifact_metadata = checked.artifact.get("compiler_metadata", {})
        if (
            not isinstance(artifact_metadata, dict)
            or artifact_metadata.get("connector_untrusted_historical_roles") is not True
        ):
            binding_issue(
                "missing_connector_trust_policy",
                "The artifact does not bind the connector historical-trust policy.",
            )
        if isinstance(policy_value, dict):
            artifact_connector_policy = (
                artifact_metadata.get("connector_policy")
                if isinstance(artifact_metadata, dict)
                else None
            )
            artifact_connector_policy_sha256 = (
                artifact_metadata.get("connector_policy_sha256")
                if isinstance(artifact_metadata, dict)
                else None
            )
            policy_digest = _sha256_json(policy_value)
            if (
                _sha256_json(artifact_connector_policy) != policy_digest
                or artifact_connector_policy_sha256 != policy_digest
            ):
                binding_issue(
                    "bundle_policy_artifact_mismatch",
                    "ContextBundle policy does not match artifact compiler metadata.",
                )
            for code, message in _policy_artifact_claim_issues(
                policy_value,
                checked.artifact,
            ):
                binding_issue(code, message)

        exact = bindings.get("token_accounting_exact")
        mode = bindings.get("token_accounting")
        tokenizer_identity = bindings.get("tokenizer_identity")
        expected_identity = (
            self.token_counter_id
            if self.token_counter is not None
            else "character-estimate-v1"
        )
        if exact is True:
            if (
                self.token_counter is None
                or mode != "exact"
                or tokenizer_identity != expected_identity
            ):
                binding_issue(
                    "unavailable_exact_tokenizer",
                    "Exact accounting requires the matching in-process tokenizer adapter.",
                )
        elif exact is False:
            if mode != "estimated" or tokenizer_identity != "character-estimate-v1":
                binding_issue(
                    "invalid_estimated_accounting",
                    "Estimated accounting must identify character-estimate-v1.",
                )
        else:
            binding_issue(
                "invalid_token_accounting_flag",
                "ContextBundle token_accounting_exact must be a boolean.",
            )
        if checked.token_accounting.get("exact") is not exact:
            binding_issue(
                "token_accounting_claim_mismatch",
                "ContextBundle accounting details disagree with their binding.",
            )

        decoded_items = [
            MemoryItem.from_dict(item) for item in checked.artifact["items"]
        ]
        rendered = render_typed_memory(
            decoded_items,
            checked.artifact["selected_item_ids"],
        )
        if bindings.get("rendered_memory_sha256") != _sha256_text(rendered):
            binding_issue(
                "rendered_memory_digest_mismatch",
                "ContextBundle rendered-memory binding is invalid.",
            )
        raw_metrics = checked.artifact.get("compiler_metadata", {}).get(
            "metrics",
            {},
        )
        protected_overflow = (
            raw_metrics.get("protected_budget_overflow", 0)
            if isinstance(raw_metrics, dict)
            else 0
        )
        expected_trusted_memory = _trusted_memory_section(
            items=decoded_items,
            selected_item_ids=checked.artifact["selected_item_ids"],
            sources=sources,
            protected_budget_overflow=protected_overflow,
        )
        if _sha256_json(checked.trusted_memory) != _sha256_json(
            expected_trusted_memory
        ):
            binding_issue(
                "trusted_memory_artifact_mismatch",
                "ContextBundle trusted_memory does not match its artifact and sources.",
            )
        expected_accounting = {
            "mode": mode,
            "exact": exact,
            "tokenizer_identity": tokenizer_identity,
            "source_tokens": checked.artifact["compression"][
                "source_tokens_estimate"
            ],
            "rendered_tokens": checked.artifact["compression"][
                "active_tokens_estimate"
            ],
            "field_name_compatibility_note": _TOKEN_FIELD_COMPATIBILITY_NOTE,
        }
        if _sha256_json(checked.token_accounting) != _sha256_json(
            expected_accounting
        ):
            binding_issue(
                "token_accounting_artifact_mismatch",
                "ContextBundle token accounting does not match its artifact.",
            )

        expected_archive_head = _validate_sha256(
            expected_archive_chain_head_sha256,
            label="expected_archive_chain_head_sha256",
        )
        bound_archive_head = bindings.get("archive_chain_head_sha256")
        if bound_archive_head is not None:
            try:
                _validate_sha256(
                    bound_archive_head,
                    label="ContextBundle archive_chain_head_sha256",
                )
            except ValueError as exc:
                binding_issue("invalid_archive_head_binding", str(exc))
        if (
            expected_archive_head is not None
            and bound_archive_head != expected_archive_head
        ):
            binding_issue(
                "archive_head_mismatch",
                "ContextBundle archive head differs from the independently expected head.",
            )
        if (
            session is not None
            and session.archive_chain_head_sha256 is not None
            and bound_archive_head != session.archive_chain_head_sha256
        ):
            binding_issue(
                "session_archive_head_mismatch",
                "ContextBundle archive head differs from its source session.",
            )
        independently_verified_archive = self._archive_attests(
            sources,
            bound_archive_head,
        )
        trusted_archive_verified = independently_verified_archive or bool(
            session is not None
            and session.archive_head_verified
            and session.archive_chain_head_sha256 == bound_archive_head
        )
        if bindings.get("archive_head_verified") is not trusted_archive_verified:
            binding_issue(
                "archive_verification_claim_mismatch",
                "ContextBundle archive verification claim lacks the trusted archive anchor.",
            )

        detected = report["protected_candidates"]
        retained = report["protected_retained"]
        expected_embedded_certificate_issued = (
            report["passed"]
            and detected == retained
            and not any(
                entry["reason"] == "omitted"
                for entry in expected_trusted_memory[
                    "omitted_or_overflowed_protected_items"
                ]
            )
        )
        expected_embedded_certificate = {
            "issued": expected_embedded_certificate_issued,
            "claim": (
                RETENTION_CERTIFICATE_WORDING
                if expected_embedded_certificate_issued
                else None
            ),
            "scope": "detector-scoped protected commitments",
            "detected_protected_commitments": detected,
            "retained_detected_protected_commitments": retained,
            "semantic_completeness_claimed": False,
        }
        if _sha256_json(checked.certificate) != _sha256_json(
            expected_embedded_certificate
        ):
            binding_issue(
                "retention_certificate_mismatch",
                "ContextBundle retention certificate does not match independent replay.",
            )

        all_issues = [*report["issues"], *binding_issues]
        passed = report["passed"] and not binding_issues
        certificate_issued = passed and expected_embedded_certificate_issued
        return {
            **report,
            "passed": passed,
            "bundle_digest_valid": True,
            "bindings_valid": not binding_issues,
            "issues": all_issues,
            "certificate": {
                "issued": certificate_issued,
                "claim": (
                    RETENTION_CERTIFICATE_WORDING
                    if certificate_issued
                    else None
                ),
                "scope": "detector-scoped protected commitments",
                "detected_protected_commitments": detected,
                "retained_detected_protected_commitments": retained,
                "semantic_completeness_claimed": False,
            },
        }

    def inspect_memory(
        self,
        bundle: ContextBundle | Mapping[str, Any],
    ) -> dict[str, Any]:
        checked = _coerce_bundle(bundle)
        self._require_supported_bundle_claims(checked)
        summary = summarize_artifact(
            checked.artifact,
            limits=self.artifact_limits,
        )
        category_names = (
            "active_goals",
            "constraints",
            "user_corrections",
            "decisions",
            "confirmed_facts",
            "unresolved_questions",
            "exact_errors",
            "exact_references",
        )
        counts = {
            name: len(checked.trusted_memory.get(name, []))
            for name in category_names
        }
        return {
            "schema": CONNECTOR_INSPECTION_SCHEMA,
            "bundle_sha256": checked.bundle_sha256,
            "artifact": summary,
            "trusted_memory_counts": counts,
            "source_spans": len(checked.trusted_memory.get("source_spans", [])),
            "source_hashes": len(checked.trusted_memory.get("source_hashes", [])),
            "omitted_or_overflowed_protected_items": len(
                checked.trusted_memory.get(
                    "omitted_or_overflowed_protected_items",
                    [],
                )
            ),
            "bindings": copy.deepcopy(checked.bindings),
            "certificate": copy.deepcopy(checked.certificate),
            "token_accounting": copy.deepcopy(checked.token_accounting),
        }

    def _dispatch_operation(
        self,
        operation: str,
        payload: Mapping[str, Any],
    ) -> Any:
        if operation == "capabilities":
            _exact_fields(payload, allowed=frozenset(), label="capabilities payload")
            return self.capabilities()
        if operation == "ingest_source_events":
            allowed = frozenset(
                {
                    "events",
                    "session_id",
                    "checkpoint",
                    "archive_chain_head_sha256",
                }
            )
            _exact_fields(
                payload,
                allowed=allowed,
                required=frozenset({"events"}),
                label="ingest_source_events payload",
            )
            events = payload["events"]
            if not isinstance(events, list):
                raise TypeError("ingest_source_events events must be an array")
            return self.ingest_source_events(
                events,
                session_id=payload.get("session_id"),
                checkpoint=payload.get("checkpoint"),
                archive_chain_head_sha256=payload.get(
                    "archive_chain_head_sha256"
                ),
            )
        if operation == "compile_memory":
            allowed = frozenset(
                {
                    "session_id",
                    "checkpoint",
                    "events",
                    "policy",
                    "archive_chain_head_sha256",
                    "timeout_seconds",
                }
            )
            _exact_fields(
                payload,
                allowed=allowed,
                label="compile_memory payload",
            )
            events = payload.get("events")
            if events is not None and not isinstance(events, list):
                raise TypeError("compile_memory events must be an array or null")
            bundle = self.compile_memory(
                session_id=payload.get("session_id"),
                checkpoint=payload.get("checkpoint"),
                events=events,
                policy=payload.get("policy"),
                archive_chain_head_sha256=payload.get(
                    "archive_chain_head_sha256"
                ),
                timeout_seconds=payload.get("timeout_seconds"),
            )
            return {
                "bundle": bundle.to_dict(),
                "checkpoint": self._sessions[
                    bundle.bindings["session_id"]
                ].checkpoint(),
            }
        if operation == "render_context":
            _exact_fields(
                payload,
                allowed=frozenset({"bundle"}),
                required=frozenset({"bundle"}),
                label="render_context payload",
            )
            bundle = _coerce_bundle(payload["bundle"])
            rendered = self.render_context(bundle)
            return {
                "context": rendered,
                "rendered_memory_sha256": _sha256_text(rendered),
                "token_accounting": copy.deepcopy(bundle.token_accounting),
            }
        if operation == "verify_memory":
            allowed = frozenset(
                {
                    "bundle",
                    "session_id",
                    "checkpoint",
                    "source_records",
                    "events",
                    "expected_archive_chain_head_sha256",
                }
            )
            _exact_fields(
                payload,
                allowed=allowed,
                required=frozenset({"bundle"}),
                label="verify_memory payload",
            )
            return self.verify_memory(
                payload["bundle"],
                session_id=payload.get("session_id"),
                checkpoint=payload.get("checkpoint"),
                source_records=payload.get("source_records"),
                events=payload.get("events"),
                expected_archive_chain_head_sha256=payload.get(
                    "expected_archive_chain_head_sha256"
                ),
            )
        if operation == "inspect_memory":
            _exact_fields(
                payload,
                allowed=frozenset({"bundle"}),
                required=frozenset({"bundle"}),
                label="inspect_memory payload",
            )
            return self.inspect_memory(payload["bundle"])
        raise ValueError(f"unsupported connector operation: {operation!r}")

    def handle_request(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Handle one shared request envelope and always return a response envelope."""

        request_id: str | None = None
        operation: str | None = None
        try:
            if not isinstance(request, Mapping):
                raise TypeError("connector request must be a JSON object")
            raw = dict(request)
            expected = frozenset({"schema", "request_id", "operation", "payload"})
            _exact_fields(
                raw,
                allowed=expected,
                required=expected,
                label="connector request",
            )
            if raw["schema"] != CONNECTOR_REQUEST_SCHEMA:
                raise ValueError(
                    f"unsupported connector request schema: {raw['schema']!r}"
                )
            request_id = raw["request_id"]
            if not isinstance(request_id, str) or not request_id:
                raise TypeError("connector request_id must be a non-empty string")
            if len(request_id) > 256:
                raise ValueError("connector request_id exceeds 256 characters")
            operation = raw["operation"]
            if not isinstance(operation, str) or not operation:
                raise TypeError("connector operation must be a non-empty string")
            payload = raw["payload"]
            if not isinstance(payload, Mapping):
                raise TypeError("connector payload must be a JSON object")
            result = self._dispatch_operation(operation, payload)
        except Exception as exc:
            return _response_envelope(
                request_id=request_id,
                operation=operation,
                error=_connector_error(exc),
            )
        return _response_envelope(
            request_id=request_id,
            operation=operation,
            result=result,
        )


def _error_classification_text(exc: Exception) -> str:
    """Return bounded internal-only text for stable error classification."""

    try:
        message = str(exc)
    except BaseException:
        return ""
    return message[:_MAX_ERROR_CLASSIFICATION_CHARACTERS].casefold()


def _connector_error(exc: Exception) -> dict[str, Any]:
    if isinstance(
        exc,
        (ArtifactLimitError, CompilationLimitError, SourceLimitError),
    ):
        category = "resource_limit"
        code = "resource_limit_exceeded"
    elif isinstance(exc, KeyError):
        category = "state"
        code = "unknown_session"
    elif isinstance(exc, TimeoutError):
        category = "timeout"
        code = "operation_timed_out"
    elif isinstance(exc, json.JSONDecodeError):
        category = "invalid_request"
        code = "invalid_json"
    elif isinstance(exc, TypeError):
        category = "invalid_request"
        code = "invalid_type"
    elif isinstance(exc, ValueError):
        category = "invalid_request"
        message = _error_classification_text(exc)
        if "connector request exceeds" in message:
            category = "resource_limit"
            code = "resource_limit_exceeded"
        elif "digest mismatch" in message or "hash mismatch" in message:
            category = "integrity"
            code = "integrity_check_failed"
        elif any(
            marker in message
            for marker in (
                "duplicate json object key",
                "non-standard json constant",
                "json number must be finite",
                "expecting value",
                "extra data",
            )
        ):
            code = "invalid_json"
        elif "unsupported connector operation" in message:
            code = "unsupported_operation"
        elif "unsupported" in message and "schema" in message:
            code = "unsupported_schema"
        else:
            code = "invalid_value"
    else:
        category = "runtime"
        code = "connector_failure"
    public_spec = _CONNECTOR_PUBLIC_ERROR_SPECS.get((category, code))
    if public_spec is None:  # Defensive closure for future classifications.
        category = "runtime"
        code = "connector_failure"
        public_spec = _CONNECTOR_PUBLIC_ERROR_SPECS[(category, code)]
    public_message, public_exception_type, retryable = public_spec
    return {
        "category": category,
        "code": code,
        "message": public_message,
        "retryable": retryable,
        "details": {"exception_type": public_exception_type},
    }


def _response_envelope(
    *,
    request_id: str | None,
    operation: str | None,
    result: Any | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if (result is None) == (error is None):
        raise ValueError("connector response requires exactly one of result or error")
    return {
        "schema": CONNECTOR_RESPONSE_SCHEMA,
        "request_id": request_id,
        "operation": operation,
        "ok": error is None,
        "result": result if error is None else None,
        "error": error,
    }


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, entry in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key: {key}")
        value[key] = entry
    return value


def _finite_json_float(value: str) -> float:
    decoded = float(value)
    if not math.isfinite(decoded):
        raise ValueError("JSON number must be finite")
    return decoded


def _bounded_json_int(value: str) -> int:
    digits = value[1:] if value.startswith("-") else value
    if len(digits) > _MAX_JSON_INTEGER_DIGITS:
        raise ValueError(
            "connector request exceeds the supported JSON integer length of "
            f"{_MAX_JSON_INTEGER_DIGITS} digits"
        )
    try:
        return int(value)
    except ValueError:
        raise ValueError("connector request contains an invalid JSON integer") from None


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is forbidden: {value}")


def _json_depth(value: Any, *, maximum: int) -> None:
    stack = [(value, 1)]
    seen: set[int] = set()
    while stack:
        current, depth = stack.pop()
        if depth > maximum:
            raise ValueError(f"connector request exceeds {maximum} JSON levels")
        if isinstance(current, dict):
            identity = id(current)
            if identity in seen:
                raise ValueError("connector request JSON contains a cycle")
            seen.add(identity)
            stack.extend((entry, depth + 1) for entry in current.values())
        elif isinstance(current, list):
            identity = id(current)
            if identity in seen:
                raise ValueError("connector request JSON contains a cycle")
            seen.add(identity)
            stack.extend((entry, depth + 1) for entry in current)


def _prevalidate_json_nesting(raw: str, *, maximum: int) -> None:
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
            if depth > maximum:
                raise ValueError(
                    f"connector request exceeds {maximum} JSON levels"
                )
        elif character in "]}":
            depth = max(0, depth - 1)


def _reject_unpaired_surrogates(value: Any) -> None:
    stack = [value]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            if any(0xD800 <= ord(character) <= 0xDFFF for character in current):
                raise ValueError(
                    "connector request contains an unpaired Unicode surrogate"
                )
        elif isinstance(current, dict):
            identity = id(current)
            if identity in seen:
                raise ValueError("connector request JSON contains a cycle")
            seen.add(identity)
            stack.extend(current.keys())
            stack.extend(current.values())
        elif isinstance(current, list):
            identity = id(current)
            if identity in seen:
                raise ValueError("connector request JSON contains a cycle")
            seen.add(identity)
            stack.extend(current)


def _normalize_surrogate_pairs(value: Any) -> Any:
    if isinstance(value, str):
        normalized: list[str] = []
        index = 0
        while index < len(value):
            codepoint = ord(value[index])
            if 0xD800 <= codepoint <= 0xDBFF:
                if index + 1 >= len(value):
                    raise ValueError(
                        "connector request contains an unpaired Unicode surrogate"
                    )
                low = ord(value[index + 1])
                if not 0xDC00 <= low <= 0xDFFF:
                    raise ValueError(
                        "connector request contains an unpaired Unicode surrogate"
                    )
                normalized.append(
                    chr(0x10000 + ((codepoint - 0xD800) << 10) + low - 0xDC00)
                )
                index += 2
                continue
            if 0xDC00 <= codepoint <= 0xDFFF:
                raise ValueError(
                    "connector request contains an unpaired Unicode surrogate"
                )
            normalized.append(value[index])
            index += 1
        return "".join(normalized)
    if isinstance(value, list):
        return [_normalize_surrogate_pairs(entry) for entry in value]
    if isinstance(value, dict):
        normalized_object: dict[str, Any] = {}
        for key, entry in value.items():
            normalized_key = _normalize_surrogate_pairs(key)
            if normalized_key in normalized_object:
                raise ValueError(
                    f"duplicate JSON object key after Unicode normalization: "
                    f"{normalized_key}"
                )
            normalized_object[normalized_key] = _normalize_surrogate_pairs(entry)
        return normalized_object
    return value


def decode_connector_request(
    raw: str,
    *,
    max_request_bytes: int = 8 * 1024 * 1024,
    max_json_depth: int = 128,
) -> dict[str, Any]:
    """Strictly decode one bounded request-envelope JSON line."""

    if not isinstance(raw, str):
        raise TypeError("connector request line must be text")
    if isinstance(max_request_bytes, bool) or not isinstance(max_request_bytes, int):
        raise TypeError("max_request_bytes must be an integer")
    if max_request_bytes <= 0:
        raise ValueError("max_request_bytes must be positive")
    if isinstance(max_json_depth, bool) or not isinstance(max_json_depth, int):
        raise TypeError("max_json_depth must be an integer")
    if max_json_depth <= 0:
        raise ValueError("max_json_depth must be positive")
    _reject_unpaired_surrogates(raw)
    if len(raw.encode("utf-8")) > max_request_bytes:
        raise ValueError(
            f"connector request exceeds {max_request_bytes} UTF-8 bytes"
        )
    _prevalidate_json_nesting(raw, maximum=max_json_depth)
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_strict_json_object,
            parse_float=_finite_json_float,
            parse_int=_bounded_json_int,
            parse_constant=_reject_json_constant,
        )
    except RecursionError as exc:
        raise ValueError(
            "connector request exceeds the supported JSON nesting depth"
        ) from exc
    value = _normalize_surrogate_pairs(value)
    _json_depth(value, maximum=max_json_depth)
    if not isinstance(value, dict):
        raise TypeError("connector request must decode to a JSON object")
    return value


def serve_stdio(
    *,
    connector: LocalAIConnector | None = None,
    input_stream: Any,
    output_stream: Any,
    max_request_bytes: int = 8 * 1024 * 1024,
    max_json_depth: int = 128,
) -> int:
    """Serve sequential request/response JSON Lines until input reaches EOF."""

    service = connector if connector is not None else LocalAIConnector()
    while True:
        raw_line = input_stream.readline(max_request_bytes + 2)
        if raw_line == "":
            break
        line_too_long = (
            len(raw_line) > max_request_bytes
            and not raw_line.endswith(("\n", "\r"))
        )
        if line_too_long:
            while raw_line and not raw_line.endswith(("\n", "\r")):
                raw_line = input_stream.readline(max_request_bytes + 2)
            response = _response_envelope(
                request_id=None,
                operation=None,
                error=_connector_error(
                    ValueError(
                        "connector request exceeds "
                        f"{max_request_bytes} UTF-8 bytes"
                    )
                ),
            )
            output_stream.write(
                json.dumps(
                    response,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            )
            output_stream.flush()
            continue
        if not raw_line.strip():
            continue
        try:
            request = decode_connector_request(
                raw_line,
                max_request_bytes=max_request_bytes,
                max_json_depth=max_json_depth,
            )
        except Exception as exc:
            response = _response_envelope(
                request_id=None,
                operation=None,
                error=_connector_error(exc),
            )
        else:
            response = service.handle_request(request)
        output_stream.write(
            json.dumps(
                response,
                ensure_ascii=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        )
        output_stream.flush()
    return 0
