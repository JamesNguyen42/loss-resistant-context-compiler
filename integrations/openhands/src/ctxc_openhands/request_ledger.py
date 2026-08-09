"""Immutable, exact final-request ledger for the OpenHands integration.

The ledger stores ordered UTF-8 transport parts.  Concatenating those parts is
the exact transport payload whose bytes are counted and hashed.  Every byte is
attributed to one required component category, including provider framing.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .tokenizer import (
    require_supported_exact_tokenizer,
    validate_tokenizer_vectors,
)

FINAL_REQUEST_LEDGER_SCHEMA = "ctxc-openhands-final-request-ledger"
FINAL_REQUEST_LEDGER_VERSION = 2
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+~-]{0,511}")

REQUIRED_COMPONENT_CATEGORIES = (
    "prompts",
    "verified_memory",
    "recent_tail",
    "current_turn",
    "retrieval",
    "attachments",
    "tool_schemas",
    "provider_framing",
)
_REQUIRED_COMPONENT_SET = frozenset(REQUIRED_COMPONENT_CATEGORIES)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MAX_ACCOUNTING_COUNT = (1 << 63) - 1

_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema",
        "version",
        "accounting_mode",
        "tokenizer",
        "bindings",
        "transport",
        "accounting",
        "tool_schema_sha256",
        "final_request_sha256",
        "ledger_sha256",
    }
)
_TOKENIZER_FIELDS = frozenset({"identity", "exact_scope", "vector_sha256"})
_BINDING_FIELDS = frozenset(
    {
        "active_epoch",
        "generation_id",
        "model",
        "path",
        "session_id",
        "source_head_sha256",
        "semantic_result_digest",
    }
)
_TRANSPORT_FIELDS = frozenset({"encoding", "parts", "byte_length", "sha256"})
_PART_FIELDS = frozenset({"category", "text", "tokens"})
_ACCOUNTING_FIELDS = frozenset(
    {
        "component_counts",
        "total_tokens",
        "reserved_output_tokens",
        "safety_margin_tokens",
        "hard_limit_tokens",
        "occupied_tokens",
    }
)


class RequestLedgerError(ValueError):
    """Raised when an exact request ledger fails closed validation."""


@dataclass(frozen=True, slots=True)
class RequestLedgerLimits:
    """Resource limits applied before accepting or constructing a ledger."""

    max_transport_bytes: int = 8 * 1024 * 1024
    max_ledger_bytes: int = 16 * 1024 * 1024
    max_parts: int = 4096
    max_part_bytes: int = 2 * 1024 * 1024
    max_binding_bytes: int = 4096
    max_json_depth: int = 16
    max_json_items: int = 32_768

    def __post_init__(self) -> None:
        for name in (
            "max_transport_bytes",
            "max_ledger_bytes",
            "max_parts",
            "max_part_bytes",
            "max_binding_bytes",
            "max_json_depth",
            "max_json_items",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value <= 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True, slots=True)
class _LedgerAnalysis:
    issues: tuple[str, ...]
    component_counts: tuple[tuple[str, int], ...] = ()
    total_tokens: int | None = None
    tokenizer_identity: str | None = None
    tokenizer_vector_sha256: str | None = None
    tool_schema_sha256: str | None = None
    transport_sha256: str | None = None
    final_request_sha256: str | None = None
    ledger_sha256: str | None = None
    canonical_bytes: bytes | None = None


@dataclass(frozen=True, slots=True)
class FinalRequestLedger(Mapping[str, Any]):
    """Deep-detached immutable representation of a validated ledger.

    Only canonical bytes are retained internally.  ``to_dict`` and item access
    return new JSON values, so mutating them cannot mutate this ledger.
    """

    _canonical: bytes

    @classmethod
    def _from_validated_value(cls, value: Mapping[str, Any]) -> FinalRequestLedger:
        return cls(_canonical_json_bytes(value))

    def to_dict(self) -> dict[str, Any]:
        value = json.loads(self._canonical.decode("utf-8"))
        if not isinstance(value, dict):  # pragma: no cover - construction invariant
            raise AssertionError("ledger canonical bytes did not decode to an object")
        return value

    def to_json(self) -> str:
        return self._canonical.decode("utf-8")

    @property
    def canonical_bytes(self) -> bytes:
        return self._canonical

    @property
    def ledger_sha256(self) -> str:
        return str(self.to_dict()["ledger_sha256"])

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(_TOP_LEVEL_FIELDS)


def _utf8_bytes(value: str, *, label: str) -> bytes:
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RequestLedgerError(f"{label} must be valid UTF-8 text") from exc


def _canonical_json_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (RecursionError, TypeError, ValueError) as exc:
        raise RequestLedgerError("value is not canonical-JSON serializable") from exc
    return _utf8_bytes(encoded, label="canonical JSON")


def _utf8_byte_length(
    value: object,
    *,
    label: str,
    maximum: int,
) -> int:
    """Count UTF-8 bytes without first allocating an encoded copy."""

    if type(value) is not str:
        raise RequestLedgerError(f"{label} must be a string")
    total = 0
    for character in value:
        codepoint = ord(character)
        if 0xD800 <= codepoint <= 0xDFFF:
            raise RequestLedgerError(f"{label} must be valid UTF-8 text")
        if codepoint <= 0x7F:
            total += 1
        elif codepoint <= 0x7FF:
            total += 2
        elif codepoint <= 0xFFFF:
            total += 3
        else:
            total += 4
        if total > maximum:
            raise RequestLedgerError(f"{label} exceeds the {maximum}-byte limit")
    return total


def _canonical_string_byte_length(
    value: object,
    *,
    label: str,
    maximum: int,
) -> int:
    """Return the exact ensure_ascii=False canonical JSON string size."""

    if type(value) is not str:
        raise RequestLedgerError(f"{label} must be a string")
    total = 2
    for character in value:
        codepoint = ord(character)
        if 0xD800 <= codepoint <= 0xDFFF:
            raise RequestLedgerError("ledger contains invalid UTF-8 text")
        if character in {'"', "\\"} or character in {"\b", "\f", "\n", "\r", "\t"}:
            total += 2
        elif codepoint < 0x20:
            total += 6
        elif codepoint <= 0x7F:
            total += 1
        elif codepoint <= 0x7FF:
            total += 2
        elif codepoint <= 0xFFFF:
            total += 3
        else:
            total += 4
        if total > maximum:
            raise RequestLedgerError(
                f"ledger exceeds the {maximum}-byte canonical JSON limit"
            )
    return total


def _mapping_entries(
    value: object,
    *,
    label: str,
    maximum: int,
    max_key_bytes: int,
) -> list[tuple[str, object]]:
    """Inspect a caller-controlled mapping with a strict entry cap."""

    if not isinstance(value, Mapping):
        raise RequestLedgerError(f"{label} must be an object")
    entries: list[tuple[str, object]] = []
    seen: set[str] = set()
    try:
        iterator = iter(value.items())
        while True:
            try:
                entry = next(iterator)
            except StopIteration:
                break
            if len(entries) >= maximum:
                raise RequestLedgerError(f"{label} exceeds the item limit")
            try:
                entry_iterator = iter(entry)
                key = next(entry_iterator)
                child = next(entry_iterator)
                try:
                    next(entry_iterator)
                except StopIteration:
                    pass
                else:
                    raise RequestLedgerError(
                        f"{label} mapping yielded an invalid item"
                    )
            except RequestLedgerError:
                raise
            except (StopIteration, TypeError, ValueError) as exc:
                raise RequestLedgerError(
                    f"{label} mapping yielded an invalid item"
                ) from exc
            if type(key) is not str:
                raise RequestLedgerError(f"{label} field names must be strings")
            _utf8_byte_length(
                key,
                label=f"{label} field name",
                maximum=max_key_bytes,
            )
            if key in seen:
                raise RequestLedgerError(f"{label} mapping yielded a duplicate key")
            seen.add(key)
            entries.append((key, child))
    except RequestLedgerError:
        raise
    except Exception as exc:
        raise RequestLedgerError(f"{label} mapping could not be inspected") from exc
    return entries


def _sequence_items(
    value: object,
    *,
    label: str,
    maximum: int,
) -> list[object]:
    """Inspect a caller-controlled sequence without consulting hostile length."""

    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise RequestLedgerError(f"{label} must be an array")
    children: list[object] = []
    try:
        iterator = iter(value)
        while True:
            try:
                child = next(iterator)
            except StopIteration:
                break
            if len(children) >= maximum:
                raise RequestLedgerError(f"{label} exceeds the item limit")
            children.append(child)
    except RequestLedgerError:
        raise
    except Exception as exc:
        raise RequestLedgerError(f"{label} array could not be inspected") from exc
    return children


def _bounded_ledger_object(
    value: object,
    *,
    limits: RequestLedgerLimits,
) -> tuple[dict[str, Any], bytes]:
    """Detach one acyclic JSON object before allocating its canonical encoding."""

    if not isinstance(value, Mapping):
        raise RequestLedgerError("ledger must be an object")

    detached: object = None
    pending: list[
        tuple[object, dict[str, Any] | list[Any] | None, str | int | None, int]
    ] = [(value, None, None, 1)]
    seen_containers: set[int] = set()
    item_count = 1
    canonical_size = 0

    def add_size(amount: int) -> None:
        nonlocal canonical_size
        canonical_size += amount
        if canonical_size > limits.max_ledger_bytes:
            raise RequestLedgerError(
                f"ledger exceeds the {limits.max_ledger_bytes}-byte limit"
            )

    def assign(
        parent: dict[str, Any] | list[Any] | None,
        slot: str | int | None,
        child: object,
    ) -> None:
        nonlocal detached
        if parent is None:
            detached = child
        elif (
            isinstance(parent, dict) and isinstance(slot, str)
        ) or (
            isinstance(parent, list) and isinstance(slot, int)
        ):
            parent[slot] = child
        else:  # pragma: no cover - traversal invariant
            raise AssertionError("invalid bounded-ledger assignment")

    while pending:
        item, parent, slot, depth = pending.pop()
        if depth > limits.max_json_depth:
            raise RequestLedgerError(
                f"ledger exceeds the {limits.max_json_depth}-level depth limit"
            )

        if type(item) is str:
            add_size(
                _canonical_string_byte_length(
                    item,
                    label="ledger string",
                    maximum=limits.max_ledger_bytes,
                )
            )
            assign(parent, slot, item)
            continue
        if item is None:
            add_size(4)
            assign(parent, slot, None)
            continue
        if type(item) is bool:
            add_size(4 if item else 5)
            assign(parent, slot, item)
            continue
        if type(item) is int:
            bit_length = item.bit_length()
            decimal_upper_bound = max(
                1,
                (bit_length * 30_103 + 99_999) // 100_000,
            ) + int(item < 0)
            if decimal_upper_bound > limits.max_ledger_bytes - canonical_size:
                raise RequestLedgerError(
                    f"ledger exceeds the {limits.max_ledger_bytes}-byte limit"
                )
            try:
                integer_text = str(item)
            except ValueError as exc:
                raise RequestLedgerError(
                    "ledger integer is not canonical-JSON serializable"
                ) from exc
            add_size(len(integer_text))
            assign(parent, slot, item)
            continue

        if isinstance(item, Mapping):
            identity = id(item)
            if identity in seen_containers:
                raise RequestLedgerError(
                    "ledger contains a cycle or shared container"
                )
            seen_containers.add(identity)
            available = limits.max_json_items - item_count
            entries = _mapping_entries(
                item,
                label="ledger",
                maximum=max(0, available),
                max_key_bytes=limits.max_ledger_bytes,
            )
            item_count += len(entries)
            if item_count > limits.max_json_items:
                raise RequestLedgerError(
                    f"ledger exceeds the {limits.max_json_items}-item limit"
                )
            target: dict[str, Any] = {}
            assign(parent, slot, target)
            add_size(2 + max(0, len(entries) - 1))
            for key, _child in entries:
                add_size(
                    _canonical_string_byte_length(
                        key,
                        label="ledger field name",
                        maximum=limits.max_ledger_bytes,
                    )
                    + 1
                )
            for key, child in reversed(entries):
                pending.append((child, target, key, depth + 1))
            continue

        if isinstance(item, Sequence) and not isinstance(
            item, (str, bytes, bytearray)
        ):
            identity = id(item)
            if identity in seen_containers:
                raise RequestLedgerError(
                    "ledger contains a cycle or shared container"
                )
            seen_containers.add(identity)
            available = limits.max_json_items - item_count
            children = _sequence_items(
                item,
                label="ledger",
                maximum=max(0, available),
            )
            item_count += len(children)
            if item_count > limits.max_json_items:
                raise RequestLedgerError(
                    f"ledger exceeds the {limits.max_json_items}-item limit"
                )
            target_list: list[Any] = [None] * len(children)
            assign(parent, slot, target_list)
            add_size(2 + max(0, len(children) - 1))
            for index in range(len(children) - 1, -1, -1):
                pending.append((children[index], target_list, index, depth + 1))
            continue

        raise RequestLedgerError("ledger contains a non-JSON value")

    if not isinstance(detached, dict):  # pragma: no cover - root check above
        raise RequestLedgerError("ledger must detach to an object")
    canonical = _canonical_json_bytes(detached)
    if len(canonical) > limits.max_ledger_bytes:
        raise RequestLedgerError(
            f"ledger exceeds the {limits.max_ledger_bytes}-byte limit"
        )
    return detached, canonical


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_sha256(value: object) -> str:
    return _sha256_bytes(_canonical_json_bytes(value))


def _is_non_negative_int(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int)
        and 0 <= value <= _MAX_ACCOUNTING_COUNT
    )


def _validate_exact_fields(
    value: object,
    expected: frozenset[str],
    label: str,
    issues: list[str],
) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        issues.append(f"{label} must be an object")
        return None
    keys = set(value)
    unknown = sorted(str(key) for key in keys - expected)
    missing = sorted(expected - keys)
    if unknown:
        issues.append(f"{label} has unknown fields: {', '.join(unknown)}")
    if missing:
        issues.append(f"{label} is missing fields: {', '.join(missing)}")
    if any(not isinstance(key, str) for key in keys):
        issues.append(f"{label} field names must be strings")
    return value


def _validate_bounded_string(
    value: object,
    *,
    label: str,
    max_bytes: int,
    issues: list[str],
    allow_empty: bool = False,
) -> str | None:
    if type(value) is not str:
        issues.append(f"{label} must be a string")
        return None
    if not allow_empty and not value:
        issues.append(f"{label} must not be empty")
        return None
    try:
        _utf8_byte_length(value, label=label, maximum=max_bytes)
    except RequestLedgerError as exc:
        issues.append(str(exc))
        return None
    return value

def _validate_binding_identifier(
    value: object,
    *,
    label: str,
    max_bytes: int,
    issues: list[str],
) -> str | None:
    normalized = _validate_bounded_string(
        value,
        label=label,
        max_bytes=max_bytes,
        issues=issues,
    )
    if normalized is None:
        return None
    if _IDENTIFIER.fullmatch(normalized) is None:
        issues.append(f"{label} has an invalid identifier")
        return None
    return normalized



def _request_digest(*, model: str, path: str, payload: str) -> str:
    return _canonical_sha256(
        {
            "encoding": "utf-8",
            "model": model,
            "path": path,
            "payload_utf8": payload,
        }
    )


def _tool_schema_digest(texts: Sequence[str]) -> str:
    return _canonical_sha256({"tool_schema_parts_utf8": list(texts)})


def _ledger_self_digest(value: Mapping[str, Any]) -> str:
    return _canonical_sha256(
        {key: item for key, item in value.items() if key != "ledger_sha256"}
    )


def _normalize_source_parts(
    transport_parts: object,
    *,
    limits: RequestLedgerLimits,
) -> list[dict[str, str]]:
    if isinstance(transport_parts, (str, bytes, bytearray)) or not isinstance(
        transport_parts, Sequence
    ):
        raise RequestLedgerError("transport_parts must be a sequence of objects")
    source_items = _sequence_items(
        transport_parts,
        label="transport_parts",
        maximum=limits.max_parts,
    )
    if not source_items:
        raise RequestLedgerError("transport_parts must not be empty")

    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    total_bytes = 0
    for index, item in enumerate(source_items):
        if not isinstance(item, Mapping):
            raise RequestLedgerError(f"transport part {index} must be an object")
        entries = _mapping_entries(
            item,
            label=f"transport part {index}",
            maximum=3,
            max_key_bytes=limits.max_binding_bytes,
        )
        values = dict(entries)
        keys = set(values)
        if keys != {"category", "text"}:
            unknown = sorted(keys - {"category", "text"})
            missing = sorted({"category", "text"} - keys)
            details: list[str] = []
            if unknown:
                details.append(f"unknown fields {unknown}")
            if missing:
                details.append(f"missing fields {missing}")
            raise RequestLedgerError(f"transport part {index}: {'; '.join(details)}")
        category = values["category"]
        text = values["text"]
        if type(category) is not str or category not in _REQUIRED_COMPONENT_SET:
            raise RequestLedgerError(f"transport part {index} has unsupported category")
        if type(text) is not str:
            raise RequestLedgerError(f"transport part {index} text must be a string")
        try:
            size = _utf8_byte_length(
                text,
                label=f"transport part {index} text",
                maximum=limits.max_part_bytes,
            )
        except RequestLedgerError as exc:
            raise RequestLedgerError(str(exc)) from exc
        total_bytes += size
        if total_bytes > limits.max_transport_bytes:
            raise RequestLedgerError(
                f"transport payload exceeds the {limits.max_transport_bytes}-byte limit"
            )
        normalized.append({"category": category, "text": text})
        seen.add(category)

    missing_categories = sorted(_REQUIRED_COMPONENT_SET - seen)
    if missing_categories:
        raise RequestLedgerError(
            "transport parts are missing required categories: " + ", ".join(missing_categories)
        )
    return normalized


def _validate_binding_digest(value: object, label: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise RequestLedgerError(
            f"{label} must be 64 lowercase hexadecimal characters"
        )
    return value


def _validate_count_input(value: object, label: str) -> int:
    if not _is_non_negative_int(value):
        raise RequestLedgerError(
            f"{label} must be a non-negative integer no greater than "
            f"{_MAX_ACCOUNTING_COUNT}"
        )
    return int(value)


def build_final_request_ledger(
    *,
    transport_parts: Sequence[Mapping[str, str]],
    model: str,
    path: str,
    session_id: str,
    generation_id: str,
    active_epoch: int,
    source_head_sha256: str,
    semantic_result_digest: str,
    reserved_output_tokens: int,
    safety_margin_tokens: int,
    hard_limit_tokens: int,
    tokenizer: object,
    limits: RequestLedgerLimits | None = None,
) -> FinalRequestLedger:
    """Construct, self-hash, and strictly validate an immutable exact ledger."""

    resolved_limits = limits or RequestLedgerLimits()
    exact = require_supported_exact_tokenizer(tokenizer)
    vector_report = validate_tokenizer_vectors(exact)
    if not vector_report.passed:
        raise RequestLedgerError(
            "tokenizer vector replay failed: " + "; ".join(vector_report.issues)
        )

    binding_issues: list[str] = []
    normalized_model = _validate_bounded_string(
        model,
        label="model",
        max_bytes=resolved_limits.max_binding_bytes,
        issues=binding_issues,
    )
    normalized_path = _validate_bounded_string(
        path,
        label="path",
        max_bytes=resolved_limits.max_binding_bytes,
        issues=binding_issues,
    )
    normalized_session_id = _validate_binding_identifier(
        session_id,
        label="session_id",
        max_bytes=resolved_limits.max_binding_bytes,
        issues=binding_issues,
    )
    normalized_generation_id = _validate_binding_identifier(
        generation_id,
        label="generation_id",
        max_bytes=resolved_limits.max_binding_bytes,
        issues=binding_issues,
    )
    if binding_issues:
        raise RequestLedgerError("; ".join(binding_issues))
    if (
        normalized_model is None
        or normalized_session_id is None
        or normalized_generation_id is None
        or normalized_path is None
    ):
        raise RequestLedgerError(
            "request bindings failed closed after validation"
        )
    source_head = _validate_binding_digest(source_head_sha256, "source_head_sha256")
    semantic_digest = _validate_binding_digest(
        semantic_result_digest, "semantic_result_digest"
    )
    normalized_active_epoch = _validate_count_input(active_epoch, "active_epoch")
    if normalized_active_epoch == 0:
        raise RequestLedgerError("active_epoch must be positive")
    reserved = _validate_count_input(reserved_output_tokens, "reserved_output_tokens")
    margin = _validate_count_input(safety_margin_tokens, "safety_margin_tokens")
    hard_limit = _validate_count_input(hard_limit_tokens, "hard_limit_tokens")
    if hard_limit == 0:
        raise RequestLedgerError("hard_limit_tokens must be positive")

    normalized_parts = _normalize_source_parts(transport_parts, limits=resolved_limits)
    texts = tuple(part["text"] for part in normalized_parts)
    raw_part_counts = exact.count_parts(texts)
    if (
        isinstance(raw_part_counts, (str, bytes, bytearray))
        or not isinstance(raw_part_counts, Sequence)
        or len(raw_part_counts) != len(normalized_parts)
    ):
        raise RequestLedgerError("exact tokenizer returned an invalid component count vector")
    part_counts: list[int] = []
    for index, count in enumerate(raw_part_counts):
        if not _is_non_negative_int(count):
            raise RequestLedgerError(
                f"exact tokenizer returned an invalid count for transport part {index}"
            )
        part_counts.append(int(count))

    payload = "".join(texts)
    total = exact.count_text(payload)
    if not _is_non_negative_int(total):
        raise RequestLedgerError("exact tokenizer returned an invalid total token count")
    if sum(part_counts) != total:
        raise RequestLedgerError(
            "exact tokenizer component counts do not equal the final transport count"
        )
    occupied = total + reserved + margin
    if occupied > hard_limit:
        raise RequestLedgerError(
            "final request exceeds hard token limit: "
            f"{total} + {reserved} reserved + {margin} margin = "
            f"{occupied} > {hard_limit}"
        )

    component_counts = {category: 0 for category in REQUIRED_COMPONENT_CATEGORIES}
    ledger_parts: list[dict[str, object]] = []
    tool_schema_texts: list[str] = []
    for part, count in zip(normalized_parts, part_counts, strict=True):
        category = part["category"]
        component_counts[category] += count
        ledger_parts.append(
            {"category": category, "text": part["text"], "tokens": count}
        )
        if category == "tool_schemas":
            tool_schema_texts.append(part["text"])

    payload_bytes = _utf8_bytes(payload, label="transport payload")
    transport_digest = _sha256_bytes(payload_bytes)
    tool_digest = _tool_schema_digest(tool_schema_texts)
    final_digest = _request_digest(
        model=normalized_model,
        path=normalized_path,
        payload=payload,
    )
    value: dict[str, Any] = {
        "schema": FINAL_REQUEST_LEDGER_SCHEMA,
        "version": FINAL_REQUEST_LEDGER_VERSION,
        "accounting_mode": "exact",
        "tokenizer": {
            "identity": exact.identity,
            "exact_scope": exact.exact_scope,
            "vector_sha256": exact.vector_sha256,
        },
        "bindings": {
            "active_epoch": normalized_active_epoch,
            "generation_id": normalized_generation_id,
            "model": normalized_model,
            "path": normalized_path,
            "session_id": normalized_session_id,
            "source_head_sha256": source_head,
            "semantic_result_digest": semantic_digest,
        },
        "transport": {
            "encoding": "utf-8",
            "parts": ledger_parts,
            "byte_length": len(payload_bytes),
            "sha256": transport_digest,
        },
        "accounting": {
            "component_counts": component_counts,
            "total_tokens": total,
            "reserved_output_tokens": reserved,
            "safety_margin_tokens": margin,
            "hard_limit_tokens": hard_limit,
            "occupied_tokens": occupied,
        },
        "tool_schema_sha256": tool_digest,
        "final_request_sha256": final_digest,
    }
    value["ledger_sha256"] = _ledger_self_digest(value)
    canonical_size = len(_canonical_json_bytes(value))
    if canonical_size > resolved_limits.max_ledger_bytes:
        raise RequestLedgerError(
            f"ledger exceeds the {resolved_limits.max_ledger_bytes}-byte limit"
        )
    return validate_final_request_ledger(value, tokenizer=exact, limits=resolved_limits)


def _analyze_ledger(
    value: object,
    *,
    tokenizer: object,
    limits: RequestLedgerLimits,
    override_parts: Sequence[Mapping[str, str]] | None = None,
) -> _LedgerAnalysis:
    issues: list[str] = []
    try:
        exact = require_supported_exact_tokenizer(tokenizer)
    except (TypeError, ValueError) as exc:
        return _LedgerAnalysis(issues=(str(exc),))
    vector_report = validate_tokenizer_vectors(exact)
    if not vector_report.passed:
        issues.extend(f"tokenizer vector replay: {issue}" for issue in vector_report.issues)

    try:
        raw: object = value.to_dict() if isinstance(value, FinalRequestLedger) else value
        top, canonical = _bounded_ledger_object(raw, limits=limits)
    except (RequestLedgerError, TypeError, ValueError) as exc:
        return _LedgerAnalysis(
            issues=(str(exc),),
            tokenizer_identity=exact.identity,
            tokenizer_vector_sha256=exact.vector_sha256,
        )
    top = _validate_exact_fields(top, _TOP_LEVEL_FIELDS, "ledger", issues)
    if top is None:
        return _LedgerAnalysis(
            issues=tuple(issues),
            tokenizer_identity=exact.identity,
            tokenizer_vector_sha256=exact.vector_sha256,
        )

    if top.get("schema") != FINAL_REQUEST_LEDGER_SCHEMA:
        issues.append("unsupported ledger schema")
    version = top.get("version")
    if isinstance(version, bool) or version != FINAL_REQUEST_LEDGER_VERSION:
        issues.append("unsupported ledger version")
    if top.get("accounting_mode") != "exact":
        issues.append("ledger accounting_mode must be exact")

    token_data = _validate_exact_fields(
        top.get("tokenizer"), _TOKENIZER_FIELDS, "tokenizer", issues
    )
    if token_data is not None:
        if token_data.get("identity") != exact.identity:
            issues.append("tokenizer identity mismatch")
        if token_data.get("exact_scope") != exact.exact_scope:
            issues.append("tokenizer exactness scope mismatch")
        if token_data.get("vector_sha256") != exact.vector_sha256:
            issues.append("tokenizer vector digest mismatch")

    bindings = _validate_exact_fields(
        top.get("bindings"), _BINDING_FIELDS, "bindings", issues
    )
    model: str | None = None
    path: str | None = None
    if bindings is not None:
        for field in ("session_id", "generation_id"):
            _validate_binding_identifier(
                bindings.get(field),
                label=f"bindings.{field}",
                max_bytes=limits.max_binding_bytes,
                issues=issues,
            )
        model = _validate_bounded_string(
            bindings.get("model"),
            label="bindings.model",
            max_bytes=limits.max_binding_bytes,
            issues=issues,
        )
        path = _validate_bounded_string(
            bindings.get("path"),
            label="bindings.path",
            max_bytes=limits.max_binding_bytes,
            issues=issues,
        )
        for field in ("source_head_sha256", "semantic_result_digest"):
            digest = bindings.get(field)
            if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
                issues.append(
                    f"bindings.{field} must be 64 lowercase hexadecimal characters"
                )
        active_epoch = bindings.get("active_epoch")
        if not _is_non_negative_int(active_epoch) or active_epoch == 0:
            issues.append(
                "bindings.active_epoch must be a positive signed 64-bit integer"
            )

    transport = _validate_exact_fields(
        top.get("transport"), _TRANSPORT_FIELDS, "transport", issues
    )
    claimed_parts: object = None if transport is None else transport.get("parts")
    if override_parts is not None:
        try:
            source_parts = _normalize_source_parts(override_parts, limits=limits)
        except (TypeError, ValueError) as exc:
            issues.append(f"replay transport parts invalid: {exc}")
            source_parts = []
        normalized_ledger_parts: list[dict[str, object]] = [
            {"category": part["category"], "text": part["text"]} for part in source_parts
        ]
    else:
        source_parts = []
        normalized_ledger_parts = []
        if isinstance(claimed_parts, (str, bytes, bytearray)) or not isinstance(
            claimed_parts, Sequence
        ):
            issues.append("transport.parts must be an array")
        elif not claimed_parts:
            issues.append("transport.parts must not be empty")
        elif len(claimed_parts) > limits.max_parts:
            issues.append(f"transport.parts exceeds the {limits.max_parts}-part limit")
        else:
            seen: set[str] = set()
            running_bytes = 0
            for index, item in enumerate(claimed_parts):
                part = _validate_exact_fields(
                    item, _PART_FIELDS, f"transport.parts[{index}]", issues
                )
                if part is None:
                    continue
                category = part.get("category")
                text = part.get("text")
                tokens = part.get("tokens")
                valid_part = True
                if not isinstance(category, str) or category not in _REQUIRED_COMPONENT_SET:
                    issues.append(
                        f"transport.parts[{index}].category is unsupported"
                    )
                    valid_part = False
                if not isinstance(text, str):
                    issues.append(f"transport.parts[{index}].text must be a string")
                    valid_part = False
                else:
                    try:
                        part_size = len(
                            _utf8_bytes(
                                text,
                                label=f"transport.parts[{index}].text",
                            )
                        )
                    except RequestLedgerError as exc:
                        issues.append(str(exc))
                        valid_part = False
                        part_size = 0
                    if part_size > limits.max_part_bytes:
                        issues.append(
                            f"transport.parts[{index}].text exceeds the "
                            f"{limits.max_part_bytes}-byte limit"
                        )
                        valid_part = False
                    running_bytes += part_size
                if not _is_non_negative_int(tokens):
                    issues.append(
                        f"transport.parts[{index}].tokens must be a non-negative integer"
                    )
                    valid_part = False
                if valid_part:
                    if not isinstance(category, str) or not isinstance(text, str):
                        issues.append(
                            f"transport.parts[{index}] failed closed type validation"
                        )
                        continue
                    source_parts.append({"category": category, "text": text})
                    normalized_ledger_parts.append(
                        {"category": category, "text": text, "tokens": int(tokens)}
                    )
                    seen.add(category)
            if running_bytes > limits.max_transport_bytes:
                issues.append(
                    f"transport payload exceeds the {limits.max_transport_bytes}-byte limit"
                )
            missing_categories = sorted(_REQUIRED_COMPONENT_SET - seen)
            if missing_categories:
                issues.append(
                    "transport.parts are missing required categories: "
                    + ", ".join(missing_categories)
                )

    texts = tuple(part["text"] for part in source_parts)
    categories = tuple(part["category"] for part in source_parts)
    payload = "".join(texts)
    payload_bytes = _utf8_bytes(payload, label="transport payload")
    transport_digest = _sha256_bytes(payload_bytes)
    part_counts: tuple[int, ...] = ()
    total: int | None = None
    if source_parts:
        try:
            raw_counts = exact.count_parts(texts)
            raw_total = exact.count_text(payload)
        except (TypeError, ValueError) as exc:
            issues.append(f"exact tokenizer failed during replay: {exc}")
        else:
            if (
                isinstance(raw_counts, (str, bytes, bytearray))
                or not isinstance(raw_counts, Sequence)
                or len(raw_counts) != len(source_parts)
                or any(not _is_non_negative_int(count) for count in raw_counts)
            ):
                issues.append("exact tokenizer returned invalid component counts")
            elif not _is_non_negative_int(raw_total):
                issues.append("exact tokenizer returned an invalid total count")
            else:
                part_counts = tuple(int(count) for count in raw_counts)
                total = int(raw_total)
                if sum(part_counts) != total:
                    issues.append(
                        "exact tokenizer component counts do not equal total count"
                    )

    component_counts = {category: 0 for category in REQUIRED_COMPONENT_CATEGORIES}
    if part_counts:
        for category, count in zip(categories, part_counts, strict=True):
            component_counts[category] += count

    if override_parts is None and part_counts:
        for index, (part, recomputed) in enumerate(
            zip(normalized_ledger_parts, part_counts, strict=True)
        ):
            if part.get("tokens") != recomputed:
                issues.append(
                    f"transport.parts[{index}].tokens mismatch: "
                    f"expected {recomputed}, found {part.get('tokens')!r}"
                )

    if transport is not None:
        if transport.get("encoding") != "utf-8":
            issues.append("transport.encoding must be utf-8")
        byte_length = transport.get("byte_length")
        if not _is_non_negative_int(byte_length):
            issues.append("transport.byte_length must be a non-negative integer")
        elif byte_length != len(payload_bytes):
            issues.append(
                f"transport.byte_length mismatch: expected {len(payload_bytes)}, "
                f"found {byte_length}"
            )
        claimed_transport_digest = transport.get("sha256")
        if (
            not isinstance(claimed_transport_digest, str)
            or _SHA256.fullmatch(claimed_transport_digest) is None
        ):
            issues.append("transport.sha256 must be 64 lowercase hexadecimal characters")
        elif claimed_transport_digest != transport_digest:
            issues.append("transport digest mismatch")

    accounting = _validate_exact_fields(
        top.get("accounting"), _ACCOUNTING_FIELDS, "accounting", issues
    )
    if accounting is not None:
        claimed_components = accounting.get("component_counts")
        component_obj = _validate_exact_fields(
            claimed_components,
            _REQUIRED_COMPONENT_SET,
            "accounting.component_counts",
            issues,
        )
        if component_obj is not None:
            for category in REQUIRED_COMPONENT_CATEGORIES:
                claimed = component_obj.get(category)
                if not _is_non_negative_int(claimed):
                    issues.append(
                        f"accounting.component_counts.{category} must be "
                        "a non-negative integer"
                    )
                elif part_counts and claimed != component_counts[category]:
                    issues.append(
                        f"component count mismatch for {category}: expected "
                        f"{component_counts[category]}, found {claimed}"
                    )
        for field in (
            "total_tokens",
            "reserved_output_tokens",
            "safety_margin_tokens",
            "hard_limit_tokens",
            "occupied_tokens",
        ):
            if not _is_non_negative_int(accounting.get(field)):
                issues.append(f"accounting.{field} must be a non-negative integer")
        claimed_total = accounting.get("total_tokens")
        if total is not None and _is_non_negative_int(claimed_total) and claimed_total != total:
            issues.append(
                f"accounting.total_tokens mismatch: expected {total}, found {claimed_total}"
            )
        reserved = accounting.get("reserved_output_tokens")
        margin = accounting.get("safety_margin_tokens")
        hard_limit = accounting.get("hard_limit_tokens")
        occupied = accounting.get("occupied_tokens")
        if _is_non_negative_int(hard_limit) and hard_limit == 0:
            issues.append("accounting.hard_limit_tokens must be positive")
        if all(
            _is_non_negative_int(item)
            for item in (claimed_total, reserved, margin, hard_limit, occupied)
        ):
            recomputed_occupied = claimed_total + reserved + margin
            if occupied != recomputed_occupied:
                issues.append(
                    f"accounting.occupied_tokens mismatch: expected "
                    f"{recomputed_occupied}, found {occupied}"
                )
            if recomputed_occupied > hard_limit:
                issues.append(
                    "final request exceeds hard token limit: "
                    f"{recomputed_occupied} > {hard_limit}"
                )

    tool_texts = [
        text for category, text in zip(categories, texts, strict=True)
        if category == "tool_schemas"
    ]
    tool_digest = _tool_schema_digest(tool_texts)
    claimed_tool_digest = top.get("tool_schema_sha256")
    if not isinstance(claimed_tool_digest, str) or _SHA256.fullmatch(
        claimed_tool_digest
    ) is None:
        issues.append("tool_schema_sha256 must be 64 lowercase hexadecimal characters")
    elif claimed_tool_digest != tool_digest:
        issues.append("tool-schema digest mismatch")

    final_digest: str | None = None
    if model is not None and path is not None:
        final_digest = _request_digest(model=model, path=path, payload=payload)
        claimed_final_digest = top.get("final_request_sha256")
        if not isinstance(claimed_final_digest, str) or _SHA256.fullmatch(
            claimed_final_digest
        ) is None:
            issues.append(
                "final_request_sha256 must be 64 lowercase hexadecimal characters"
            )
        elif claimed_final_digest != final_digest:
            issues.append("final request digest mismatch")

    ledger_digest = _ledger_self_digest(top)
    claimed_ledger_digest = top.get("ledger_sha256")
    if not isinstance(claimed_ledger_digest, str) or _SHA256.fullmatch(
        claimed_ledger_digest
    ) is None:
        issues.append("ledger_sha256 must be 64 lowercase hexadecimal characters")
    elif claimed_ledger_digest != ledger_digest:
        issues.append("ledger self-hash mismatch")

    return _LedgerAnalysis(
        issues=tuple(issues),
        component_counts=tuple(
            (category, component_counts[category])
            for category in REQUIRED_COMPONENT_CATEGORIES
        ),
        total_tokens=total,
        tokenizer_identity=exact.identity,
        tokenizer_vector_sha256=exact.vector_sha256,
        tool_schema_sha256=tool_digest,
        transport_sha256=transport_digest,
        final_request_sha256=final_digest,
        ledger_sha256=ledger_digest,
        canonical_bytes=canonical,
    )


def validate_final_request_ledger(
    value: Mapping[str, Any] | FinalRequestLedger,
    *,
    tokenizer: object,
    limits: RequestLedgerLimits | None = None,
) -> FinalRequestLedger:
    """Fail closed unless every bound field, exact count, and digest replays."""

    resolved_limits = limits or RequestLedgerLimits()
    analysis = _analyze_ledger(
        value,
        tokenizer=tokenizer,
        limits=resolved_limits,
    )
    if analysis.issues:
        raise RequestLedgerError("; ".join(analysis.issues))
    if isinstance(value, FinalRequestLedger):
        return value
    if analysis.canonical_bytes is None:  # pragma: no cover - analysis invariant
        raise RequestLedgerError("validated ledger has no canonical representation")
    return FinalRequestLedger(analysis.canonical_bytes)
