"""Strict contracts for a future held-out natural-history evidence cohort.

The bundled documents are contract fixtures, not a collected natural corpus
and not claim-bearing evaluation evidence.  This module intentionally stays in
the dependency-free benchmark layer.  It validates bounded self-hashed corpus,
annotation, adjudication, split, gold-free export, and report documents.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from context_compiler.atomic import atomic_write_text

from .json_io import StrictJsonLimits, load_strict_json_file

CORPUS_SCHEMA = "ctxc-natural-history-corpus-0.1"
ANNOTATION_SCHEMA = "ctxc-natural-history-annotation-0.1"
ADJUDICATION_SCHEMA = "ctxc-natural-history-adjudication-0.1"
SPLIT_SCHEMA = "ctxc-natural-history-split-0.1"
GOLD_FREE_SCHEMA = "ctxc-natural-history-gold-free-0.1"
REPORT_SCHEMA = "ctxc-natural-history-report-0.1"
VERIFICATION_SCHEMA = "ctxc-natural-history-kit-verification-0.1"

_DATA_DIRECTORY = Path(__file__).with_name("data") / "natural_history"
DEFAULT_CORPUS = _DATA_DIRECTORY / "corpus_v1.json"
DEFAULT_ANNOTATIONS = (
    _DATA_DIRECTORY / "annotation_annotator_a_v1.json",
    _DATA_DIRECTORY / "annotation_annotator_b_v1.json",
)
DEFAULT_ADJUDICATION = _DATA_DIRECTORY / "adjudication_v1.json"
DEFAULT_SPLIT = _DATA_DIRECTORY / "split_v1.json"
DEFAULT_GOLD_FREE = _DATA_DIRECTORY / "gold_free_v1.json"
DEFAULT_REPORT = _DATA_DIRECTORY / "report_v1.json"

_DOCUMENT_LIMITS = StrictJsonLimits(
    max_bytes=32 * 1024 * 1024,
    max_line_chars=8 * 1024 * 1024,
    max_depth=32,
)
_MAX_HISTORIES = 10_000
_MAX_INTAKE_HISTORIES = 100_000
_MAX_SOURCES = 100_000
_MAX_SOURCE_CHARS = 1_000_000
_MAX_TOTAL_SOURCE_CHARS = 64 * 1024 * 1024
_MAX_LABELS_PER_HISTORY = 10_000
_MAX_TOTAL_LABELS = 1_000_000
_MAX_SPANS_PER_LABEL = 32
_MAX_STRING_CHARS = 2_048
_MAX_DIRECT_JSON_NODES = 2_000_000

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._:-]{0,255}\Z")
_SPDX_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,127}\Z")
_UTC_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z"
)
_ROLES = frozenset(
    {"user", "system", "developer", "assistant", "tool", "function"}
)
_KINDS = frozenset(
    {
        "goal",
        "constraint",
        "user_correction",
        "confirmed_fact",
        "decision",
        "unresolved",
        "exact_error",
        "exact_reference",
        "discarded_attempt",
        "progress",
        "context",
    }
)
_PROTECTED_KINDS = frozenset(
    {
        "goal",
        "constraint",
        "user_correction",
        "unresolved",
        "exact_error",
        "exact_reference",
    }
)
_EXACT_KINDS = frozenset({"exact_error", "exact_reference"})
_STATUSES = frozenset({"active", "superseded", "discarded", "conflicting"})
_SPLITS = ("train", "development", "test")
_GOLD_KEYS = frozenset(
    {
        "adjudication",
        "annotation",
        "annotations",
        "exact",
        "kind",
        "label",
        "labels",
        "protected",
        "provenance",
        "quote",
        "quote_sha256",
        "status",
    }
)


class NaturalHistoryError(ValueError):
    """A natural-history evidence contract is malformed or inconsistent."""


@dataclass(frozen=True, slots=True)
class Source:
    """One bounded immutable source event in a history."""

    id: str
    sequence: int
    role: str
    content: str
    timestamp: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sequence": self.sequence,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True, slots=True)
class History:
    """One repository/task-bound history plus release evidence."""

    id: str
    repository_id: str
    task_group_id: str
    task_id: str
    origin: Mapping[str, Any]
    privacy_review: Mapping[str, Any]
    sources: tuple[Source, ...]


@dataclass(frozen=True, slots=True)
class Corpus:
    """Validated versioned natural-history corpus envelope."""

    evidence_state: Mapping[str, Any]
    collection_accounting: Mapping[str, Any]
    histories: tuple[History, ...]
    corpus_sha256: str

    @property
    def history_by_id(self) -> dict[str, History]:
        return {history.id: history for history in self.histories}


@dataclass(frozen=True, slots=True)
class Span:
    """One exact Python-character provenance span."""

    source_id: str
    start: int
    end: int
    quote: str
    quote_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "start": self.start,
            "end": self.end,
            "quote": self.quote,
            "quote_sha256": self.quote_sha256,
        }


@dataclass(frozen=True, slots=True)
class Label:
    """One exact-span independently annotated or adjudicated label."""

    id: str
    kind: str
    text: str
    status: str
    exact: bool
    protected: bool
    provenance: tuple[Span, ...]

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "status": self.status,
            "exact": self.exact,
            "protected": self.protected,
            "provenance": [span.to_dict() for span in self.provenance],
        }


@dataclass(frozen=True, slots=True)
class AnnotationSet:
    """One annotator's complete independent pass over the corpus."""

    annotator: Mapping[str, Any]
    histories: tuple[tuple[str, tuple[Label, ...]], ...]
    annotation_sha256: str

    @property
    def labels_by_history(self) -> dict[str, tuple[Label, ...]]:
        return dict(self.histories)


@dataclass(frozen=True, slots=True)
class Adjudication:
    """Complete resolution of two independent annotation passes."""

    annotation_sha256s: tuple[str, str]
    adjudicator: Mapping[str, Any]
    histories: tuple[tuple[str, tuple[Label, ...]], ...]
    adjudication_sha256: str

    @property
    def labels_by_history(self) -> dict[str, tuple[Label, ...]]:
        return dict(self.histories)


@dataclass(frozen=True, slots=True)
class SplitManifest:
    """Repository- and task-group-disjoint assignment of every history."""

    policy: Mapping[str, Any]
    assignments: tuple[tuple[str, str], ...]
    split_sha256: str

    @property
    def split_by_history(self) -> dict[str, str]:
        return dict(self.assignments)


def _canonical_sha256(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (MemoryError, OverflowError, TypeError, ValueError, RecursionError) as exc:
        raise NaturalHistoryError(
            "natural-history evidence is not canonical finite JSON"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_json(nested) for key, nested in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(nested) for nested in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(nested) for nested in value]
    return value


def _encoded_json_string_size(
    value: str,
    *,
    label: str,
    max_chars: int,
) -> int:
    if len(value) > max_chars:
        raise NaturalHistoryError(
            f"{label} exceeds {max_chars} characters before hashing"
        )
    size = 2
    try:
        for character in value:
            codepoint = ord(character)
            if character in {'"', "\\"}:
                size += 2
            elif codepoint < 0x20:
                size += 6
            else:
                size += len(character.encode("utf-8"))
    except UnicodeError as exc:
        raise NaturalHistoryError(
            f"{label} is not valid UTF-8 JSON text"
        ) from exc
    return size


def _preflight_json(value: Any, *, label: str) -> None:
    """Bound a direct in-memory JSON tree before canonical hashing."""

    stack: list[tuple[Any, int]] = [(value, 1)]
    seen_containers: set[int] = set()
    node_count = 0
    encoded_bytes = 0
    while stack:
        current, depth = stack.pop()
        node_count += 1
        if node_count > _MAX_DIRECT_JSON_NODES:
            raise NaturalHistoryError(
                f"{label} exceeds {_MAX_DIRECT_JSON_NODES} JSON nodes"
            )
        if isinstance(current, dict):
            if len(current) > _MAX_DIRECT_JSON_NODES:
                raise NaturalHistoryError(
                    f"{label} exceeds {_MAX_DIRECT_JSON_NODES} JSON nodes"
                )
            if depth > _DOCUMENT_LIMITS.max_depth:
                raise NaturalHistoryError(
                    f"{label} exceeds JSON depth {_DOCUMENT_LIMITS.max_depth}"
                )
            identity = id(current)
            if identity in seen_containers:
                raise NaturalHistoryError(
                    f"{label} must be an acyclic JSON tree without shared containers"
                )
            seen_containers.add(identity)
            encoded_bytes += 2 + max(0, len(current) - 1) + len(current)
            for key, nested in current.items():
                if not isinstance(key, str):
                    raise NaturalHistoryError(
                        f"{label} JSON object keys must be strings"
                    )
                encoded_bytes += _encoded_json_string_size(
                    key,
                    label=f"{label} JSON object key",
                    max_chars=_MAX_STRING_CHARS,
                )
                stack.append((nested, depth + 1))
        elif isinstance(current, list):
            if len(current) > _MAX_DIRECT_JSON_NODES:
                raise NaturalHistoryError(
                    f"{label} exceeds {_MAX_DIRECT_JSON_NODES} JSON nodes"
                )
            if depth > _DOCUMENT_LIMITS.max_depth:
                raise NaturalHistoryError(
                    f"{label} exceeds JSON depth {_DOCUMENT_LIMITS.max_depth}"
                )
            identity = id(current)
            if identity in seen_containers:
                raise NaturalHistoryError(
                    f"{label} must be an acyclic JSON tree without shared containers"
                )
            seen_containers.add(identity)
            encoded_bytes += 2 + max(0, len(current) - 1)
            stack.extend((nested, depth + 1) for nested in current)
        elif isinstance(current, str):
            encoded_bytes += _encoded_json_string_size(
                current,
                label=f"{label} JSON string",
                max_chars=_MAX_SOURCE_CHARS,
            )
        elif current is None:
            encoded_bytes += 4
        elif isinstance(current, bool):
            encoded_bytes += 4 if current else 5
        elif isinstance(current, int):
            max_digits = _DOCUMENT_LIMITS.max_integer_digits
            if abs(current).bit_length() > int(max_digits * 3.322) + 8:
                raise NaturalHistoryError(
                    f"{label} exceeds {max_digits} JSON integer digits"
                )
            rendered = str(current)
            digits = rendered[1:] if rendered.startswith("-") else rendered
            if len(digits) > max_digits:
                raise NaturalHistoryError(
                    f"{label} exceeds {max_digits} JSON integer digits"
                )
            encoded_bytes += len(rendered)
        elif isinstance(current, float):
            if not math.isfinite(current):
                raise NaturalHistoryError(
                    f"{label} JSON numbers must be finite"
                )
            encoded_bytes += len(json.dumps(current, allow_nan=False))
        else:
            raise NaturalHistoryError(f"{label} contains a non-JSON value")
        if encoded_bytes > _DOCUMENT_LIMITS.max_bytes:
            raise NaturalHistoryError(
                f"{label} exceeds {_DOCUMENT_LIMITS.max_bytes} canonical JSON bytes"
            )


def _object(
    value: Any,
    *,
    fields: frozenset[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise NaturalHistoryError(f"{label} must be an object")
    actual = set(value)
    if actual != fields:
        missing = sorted(fields - actual)
        unknown = sorted(actual - fields)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        raise NaturalHistoryError(
            f"{label} fields are invalid: {'; '.join(details)}"
        )
    return value


def _string(
    value: Any,
    *,
    label: str,
    max_chars: int = _MAX_STRING_CHARS,
    identifier: bool = False,
) -> str:
    if not isinstance(value, str) or not value:
        raise NaturalHistoryError(f"{label} must be a non-empty string")
    if len(value) > max_chars:
        raise NaturalHistoryError(f"{label} exceeds {max_chars} characters")
    if identifier and _IDENTIFIER.fullmatch(value) is None:
        raise NaturalHistoryError(f"{label} is not a canonical identifier")
    return value


def _nullable_string(
    value: Any,
    *,
    label: str,
    max_chars: int = _MAX_STRING_CHARS,
) -> str | None:
    if value is None:
        return None
    return _string(value, label=label, max_chars=max_chars)


def _sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise NaturalHistoryError(f"{label} must be lowercase SHA-256")
    return value


def _nullable_sha256(value: Any, *, label: str) -> str | None:
    if value is None:
        return None
    return _sha256(value, label=label)


def _integer(
    value: Any,
    *,
    label: str,
    minimum: int = 0,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NaturalHistoryError(f"{label} must be an integer")
    if value < minimum:
        raise NaturalHistoryError(f"{label} must be at least {minimum}")
    return value


def _timestamp(value: Any, *, label: str) -> str:
    result = _string(value, label=label, max_chars=20)
    if _UTC_TIMESTAMP.fullmatch(result) is None:
        raise NaturalHistoryError(
            f"{label} must be a valid UTC whole-second timestamp"
        )
    try:
        datetime.strptime(result, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise NaturalHistoryError(
            f"{label} must be a valid UTC whole-second timestamp"
        ) from exc
    return result


def _verify_self_hash(
    raw: dict[str, Any],
    *,
    field: str,
    label: str,
) -> str:
    claimed = _sha256(raw[field], label=f"{label} {field}")
    unsigned = dict(raw)
    unsigned.pop(field)
    if _canonical_sha256(unsigned) != claimed:
        raise NaturalHistoryError(f"{label} SHA-256 mismatch")
    return claimed


def _decode_origin(value: Any, *, history_id: str) -> dict[str, Any]:
    fields = frozenset(
        {
            "kind",
            "license_state",
            "license_spdx",
            "license_evidence_sha256",
            "consent_state",
            "consent_evidence_sha256",
        }
    )
    raw = _object(
        value,
        fields=fields,
        label=f"history {history_id!r} origin",
    )
    kind = raw["kind"]
    if kind not in {"public", "explicit-consent", "synthetic"}:
        raise NaturalHistoryError(
            f"history {history_id!r} origin kind is unsupported"
        )
    license_state = raw["license_state"]
    license_spdx = _nullable_string(
        raw["license_spdx"],
        label=f"history {history_id!r} license_spdx",
        max_chars=128,
    )
    if (
        license_spdx is not None
        and _SPDX_IDENTIFIER.fullmatch(license_spdx) is None
    ):
        raise NaturalHistoryError(
            f"history {history_id!r} license_spdx is not a canonical "
            "SPDX license identifier"
        )
    license_digest = _nullable_sha256(
        raw["license_evidence_sha256"],
        label=f"history {history_id!r} license_evidence_sha256",
    )
    consent_state = raw["consent_state"]
    consent_digest = _nullable_sha256(
        raw["consent_evidence_sha256"],
        label=f"history {history_id!r} consent_evidence_sha256",
    )
    if kind == "public":
        if (
            license_state != "verified-compatible"
            or license_spdx is None
            or license_digest is None
            or consent_state != "not-required-public"
            or consent_digest is not None
        ):
            raise NaturalHistoryError(
                f"history {history_id!r} public origin lacks verified "
                "license evidence"
            )
    elif kind == "explicit-consent":
        if (
            license_state != "authorized-by-consent"
            or license_spdx is not None
            or license_digest is not None
            or consent_state != "documented"
            or consent_digest is None
        ):
            raise NaturalHistoryError(
                f"history {history_id!r} consent origin lacks documented "
                "consent evidence"
            )
    elif (
        license_state != "not-applicable-synthetic"
        or license_spdx is not None
        or license_digest is not None
        or consent_state != "not-applicable-synthetic"
        or consent_digest is not None
    ):
        raise NaturalHistoryError(
            f"history {history_id!r} synthetic origin state is inconsistent"
        )
    return dict(raw)


def _decode_privacy_review(
    value: Any,
    *,
    history_id: str,
) -> dict[str, Any]:
    fields = frozenset(
        {
            "state",
            "reviewer_id",
            "reviewed_at_utc",
            "content_secret_review",
            "personal_data_review",
            "publication_scope",
            "evidence_sha256",
        }
    )
    raw = _object(
        value,
        fields=fields,
        label=f"history {history_id!r} privacy review",
    )
    if raw["state"] != "approved":
        raise NaturalHistoryError(
            f"history {history_id!r} privacy review is not approved"
        )
    _string(
        raw["reviewer_id"],
        label=f"history {history_id!r} privacy reviewer_id",
        max_chars=256,
        identifier=True,
    )
    _timestamp(
        raw["reviewed_at_utc"],
        label=f"history {history_id!r} privacy reviewed_at_utc",
    )
    if raw["content_secret_review"] != "completed":
        raise NaturalHistoryError(
            f"history {history_id!r} content-secret review is incomplete"
        )
    if raw["personal_data_review"] != "completed":
        raise NaturalHistoryError(
            f"history {history_id!r} personal-data review is incomplete"
        )
    if raw["publication_scope"] != "repository":
        raise NaturalHistoryError(
            f"history {history_id!r} publication scope is not repository"
        )
    _sha256(
        raw["evidence_sha256"],
        label=f"history {history_id!r} privacy evidence_sha256",
    )
    return dict(raw)


def _decode_sources(
    value: Any,
    *,
    history_id: str,
) -> tuple[Source, ...]:
    if not isinstance(value, list) or not value:
        raise NaturalHistoryError(
            f"history {history_id!r} sources must be a non-empty array"
        )
    fields = frozenset({"id", "sequence", "role", "content", "timestamp"})
    sources: list[Source] = []
    seen_ids: set[str] = set()
    for index, source_value in enumerate(value):
        raw = _object(
            source_value,
            fields=fields,
            label=f"history {history_id!r} source {index}",
        )
        source_id = _string(
            raw["id"],
            label=f"history {history_id!r} source id",
            max_chars=256,
            identifier=True,
        )
        if source_id in seen_ids:
            raise NaturalHistoryError(
                f"history {history_id!r} has duplicate source id {source_id!r}"
            )
        seen_ids.add(source_id)
        sequence = _integer(
            raw["sequence"],
            label=f"history {history_id!r} source sequence",
        )
        if sequence != index:
            raise NaturalHistoryError(
                f"history {history_id!r} source sequences must be contiguous "
                "and in array order"
            )
        role = raw["role"]
        if role not in _ROLES:
            raise NaturalHistoryError(
                f"history {history_id!r} source role is unsupported"
            )
        content = _string(
            raw["content"],
            label=f"history {history_id!r} source content",
            max_chars=_MAX_SOURCE_CHARS,
        )
        timestamp = raw["timestamp"]
        if timestamp is not None:
            timestamp = _timestamp(
                timestamp,
                label=f"history {history_id!r} source timestamp",
            )
        sources.append(
            Source(
                id=source_id,
                sequence=sequence,
                role=role,
                content=content,
                timestamp=timestamp,
            )
        )
    return tuple(sources)


def decode_corpus(document: Any) -> Corpus:
    """Validate a bounded, self-hashed natural-history corpus."""

    _preflight_json(document, label="natural-history corpus")
    fields = frozenset(
        {"schema", "evidence_state", "collection_accounting", "histories", "corpus_sha256"}
    )
    raw = _object(document, fields=fields, label="natural-history corpus")
    if raw["schema"] != CORPUS_SCHEMA:
        raise NaturalHistoryError("natural-history corpus schema is unsupported")
    corpus_sha256 = _verify_self_hash(
        raw,
        field="corpus_sha256",
        label="natural-history corpus",
    )
    state_fields = frozenset(
        {
            "kind",
            "natural_history_claimed",
            "frozen_for_evaluation",
            "semantic_completeness_claimed",
        }
    )
    state = _object(
        raw["evidence_state"],
        fields=state_fields,
        label="natural-history corpus evidence_state",
    )
    if state["kind"] not in {"contract-fixture", "natural-corpus"}:
        raise NaturalHistoryError(
            "natural-history corpus evidence kind is unsupported"
        )
    for name in (
        "natural_history_claimed",
        "frozen_for_evaluation",
        "semantic_completeness_claimed",
    ):
        if not isinstance(state[name], bool):
            raise NaturalHistoryError(
                f"natural-history corpus {name} must be a boolean"
            )
    if state["semantic_completeness_claimed"]:
        raise NaturalHistoryError(
            "natural-history corpus cannot claim semantic completeness"
        )
    if state["kind"] == "contract-fixture" and (
        state["natural_history_claimed"] or state["frozen_for_evaluation"]
    ):
        raise NaturalHistoryError(
            "contract fixture cannot claim a natural or frozen corpus"
        )
    if state["kind"] == "natural-corpus" and not state["natural_history_claimed"]:
        raise NaturalHistoryError(
            "natural corpus must explicitly claim its evidence kind"
        )
    history_values = raw["histories"]
    if (
        not isinstance(history_values, list)
        or not history_values
        or len(history_values) > _MAX_HISTORIES
    ):
        raise NaturalHistoryError(
            f"natural-history corpus must contain 1..{_MAX_HISTORIES} histories"
        )
    history_fields = frozenset(
        {
            "id",
            "repository_id",
            "task_group_id",
            "task_id",
            "origin",
            "privacy_review",
            "sources",
        }
    )
    histories: list[History] = []
    seen_history_ids: set[str] = set()
    total_sources = 0
    total_source_chars = 0
    for index, history_value in enumerate(history_values):
        history_raw = _object(
            history_value,
            fields=history_fields,
            label=f"natural-history corpus history {index}",
        )
        history_id = _string(
            history_raw["id"],
            label=f"natural-history corpus history {index} id",
            max_chars=256,
            identifier=True,
        )
        if history_id in seen_history_ids:
            raise NaturalHistoryError(
                f"duplicate natural-history id: {history_id!r}"
            )
        seen_history_ids.add(history_id)
        repository_id = _string(
            history_raw["repository_id"],
            label=f"history {history_id!r} repository_id",
            max_chars=256,
            identifier=True,
        )
        task_group_id = _string(
            history_raw["task_group_id"],
            label=f"history {history_id!r} task_group_id",
            max_chars=256,
            identifier=True,
        )
        task_id = _string(
            history_raw["task_id"],
            label=f"history {history_id!r} task_id",
            max_chars=256,
            identifier=True,
        )
        origin = _decode_origin(history_raw["origin"], history_id=history_id)
        privacy_review = _decode_privacy_review(
            history_raw["privacy_review"],
            history_id=history_id,
        )
        sources = _decode_sources(
            history_raw["sources"],
            history_id=history_id,
        )
        reviewed_at = privacy_review["reviewed_at_utc"]
        if any(
            source.timestamp is not None and source.timestamp > reviewed_at
            for source in sources
        ):
            raise NaturalHistoryError(
                f"history {history_id!r} privacy review precedes dated source content"
            )
        total_sources += len(sources)
        total_source_chars += sum(len(source.content) for source in sources)
        if total_sources > _MAX_SOURCES:
            raise NaturalHistoryError(
                f"natural-history corpus exceeds {_MAX_SOURCES} sources"
            )
        if total_source_chars > _MAX_TOTAL_SOURCE_CHARS:
            raise NaturalHistoryError(
                "natural-history corpus exceeds the total source-character limit"
            )
        histories.append(
            History(
                id=history_id,
                repository_id=repository_id,
                task_group_id=task_group_id,
                task_id=task_id,
                origin=_freeze_json(origin),
                privacy_review=_freeze_json(privacy_review),
                sources=sources,
            )
        )
    accounting_fields = frozenset(
        {
            "intake_manifest_sha256",
            "attempted_history_count",
            "included_history_ids",
            "exclusions",
        }
    )
    accounting = _object(
        raw["collection_accounting"],
        fields=accounting_fields,
        label="natural-history corpus collection_accounting",
    )
    _sha256(
        accounting["intake_manifest_sha256"],
        label="natural-history corpus intake_manifest_sha256",
    )
    attempted = _integer(
        accounting["attempted_history_count"],
        label="natural-history corpus attempted_history_count",
        minimum=1,
    )
    if attempted > _MAX_INTAKE_HISTORIES:
        raise NaturalHistoryError(
            "natural-history corpus attempted history count exceeds "
            f"{_MAX_INTAKE_HISTORIES}"
        )
    included_ids = accounting["included_history_ids"]
    expected_included_ids = [history.id for history in histories]
    if included_ids != expected_included_ids:
        raise NaturalHistoryError(
            "natural-history corpus collection accounting must list every "
            "included history canonically"
        )
    exclusion_values = accounting["exclusions"]
    if (
        not isinstance(exclusion_values, list)
        or len(exclusion_values) > _MAX_INTAKE_HISTORIES
    ):
        raise NaturalHistoryError(
            "natural-history corpus exclusions must be a bounded array"
        )
    exclusion_fields = frozenset(
        {"history_id", "reason_code", "review_state", "evidence_sha256"}
    )
    excluded_ids: set[str] = set()
    for index, exclusion_value in enumerate(exclusion_values):
        exclusion = _object(
            exclusion_value,
            fields=exclusion_fields,
            label=f"natural-history corpus exclusion {index}",
        )
        excluded_id = _string(
            exclusion["history_id"],
            label=f"natural-history corpus exclusion {index} history_id",
            max_chars=256,
            identifier=True,
        )
        if excluded_id in excluded_ids or excluded_id in seen_history_ids:
            raise NaturalHistoryError(
                "natural-history corpus exclusion ids must be unique and "
                "disjoint from included histories"
            )
        excluded_ids.add(excluded_id)
        _string(
            exclusion["reason_code"],
            label=f"natural-history corpus exclusion {index} reason_code",
            max_chars=256,
            identifier=True,
        )
        if exclusion["review_state"] != "documented":
            raise NaturalHistoryError(
                "natural-history corpus exclusions require documented review"
            )
        _sha256(
            exclusion["evidence_sha256"],
            label=f"natural-history corpus exclusion {index} evidence_sha256",
        )
    if attempted != len(histories) + len(exclusion_values):
        raise NaturalHistoryError(
            "natural-history corpus collection accounting does not reconcile "
            "attempted, included, and excluded histories"
        )
    return Corpus(
        evidence_state=_freeze_json(dict(state)),
        collection_accounting=_freeze_json(dict(accounting)),
        histories=tuple(histories),
        corpus_sha256=corpus_sha256,
    )


def load_corpus(path: str | Path = DEFAULT_CORPUS) -> Corpus:
    """Strictly load one natural-history corpus regular file."""

    loaded = load_strict_json_file(
        path,
        limits=_DOCUMENT_LIMITS,
        label="natural-history corpus",
    )
    return decode_corpus(loaded.value)


def _decode_span(
    value: Any,
    *,
    history: History,
    label_id: str,
    index: int,
) -> Span:
    fields = frozenset(
        {"source_id", "start", "end", "quote", "quote_sha256"}
    )
    raw = _object(
        value,
        fields=fields,
        label=f"label {label_id!r} provenance {index}",
    )
    source_id = _string(
        raw["source_id"],
        label=f"label {label_id!r} provenance source_id",
        max_chars=256,
        identifier=True,
    )
    source_by_id = {source.id: source for source in history.sources}
    source = source_by_id.get(source_id)
    if source is None:
        raise NaturalHistoryError(
            f"label {label_id!r} cites unknown source {source_id!r}"
        )
    start = _integer(
        raw["start"],
        label=f"label {label_id!r} provenance start",
    )
    end = _integer(
        raw["end"],
        label=f"label {label_id!r} provenance end",
        minimum=1,
    )
    if start >= end or end > len(source.content):
        raise NaturalHistoryError(
            f"label {label_id!r} provenance offsets are out of bounds"
        )
    quote = _string(
        raw["quote"],
        label=f"label {label_id!r} provenance quote",
        max_chars=_MAX_SOURCE_CHARS,
    )
    if source.content[start:end] != quote:
        raise NaturalHistoryError(
            f"label {label_id!r} provenance is not an exact character span"
        )
    quote_sha256 = _sha256(
        raw["quote_sha256"],
        label=f"label {label_id!r} provenance quote_sha256",
    )
    if hashlib.sha256(quote.encode("utf-8")).hexdigest() != quote_sha256:
        raise NaturalHistoryError(
            f"label {label_id!r} provenance quote SHA-256 mismatch"
        )
    return Span(
        source_id=source_id,
        start=start,
        end=end,
        quote=quote,
        quote_sha256=quote_sha256,
    )


def _decode_labels(
    value: Any,
    *,
    history: History,
    context: str,
    seen_document_ids: set[str],
) -> tuple[Label, ...]:
    if not isinstance(value, list) or len(value) > _MAX_LABELS_PER_HISTORY:
        raise NaturalHistoryError(
            f"{context} labels must be an array with at most "
            f"{_MAX_LABELS_PER_HISTORY} entries"
        )
    fields = frozenset(
        {
            "id",
            "kind",
            "text",
            "status",
            "exact",
            "protected",
            "provenance",
        }
    )
    labels: list[Label] = []
    seen_semantic: set[str] = set()
    for index, label_value in enumerate(value):
        raw = _object(
            label_value,
            fields=fields,
            label=f"{context} label {index}",
        )
        label_id = _string(
            raw["id"],
            label=f"{context} label id",
            max_chars=256,
            identifier=True,
        )
        if label_id in seen_document_ids:
            raise NaturalHistoryError(
                f"{context} has duplicate label id {label_id!r}"
            )
        seen_document_ids.add(label_id)
        kind = raw["kind"]
        if kind not in _KINDS:
            raise NaturalHistoryError(
                f"label {label_id!r} kind is unsupported"
            )
        text = _string(
            raw["text"],
            label=f"label {label_id!r} text",
            max_chars=_MAX_SOURCE_CHARS,
        )
        status = raw["status"]
        if status not in _STATUSES:
            raise NaturalHistoryError(
                f"label {label_id!r} status is unsupported"
            )
        exact = raw["exact"]
        protected = raw["protected"]
        if not isinstance(exact, bool) or not isinstance(protected, bool):
            raise NaturalHistoryError(
                f"label {label_id!r} exact and protected must be booleans"
            )
        if exact != (kind in _EXACT_KINDS):
            raise NaturalHistoryError(
                f"label {label_id!r} exact flag disagrees with its kind"
            )
        if protected != (kind in _PROTECTED_KINDS):
            raise NaturalHistoryError(
                f"label {label_id!r} protected flag disagrees with its kind"
            )
        span_values = raw["provenance"]
        if (
            not isinstance(span_values, list)
            or not span_values
            or len(span_values) > _MAX_SPANS_PER_LABEL
        ):
            raise NaturalHistoryError(
                f"label {label_id!r} must have 1..{_MAX_SPANS_PER_LABEL} spans"
            )
        spans = tuple(
            _decode_span(
                span_value,
                history=history,
                label_id=label_id,
                index=span_index,
            )
            for span_index, span_value in enumerate(span_values)
        )
        span_keys = {
            (span.source_id, span.start, span.end, span.quote_sha256)
            for span in spans
        }
        if len(span_keys) != len(spans):
            raise NaturalHistoryError(
                f"label {label_id!r} repeats a provenance span"
            )
        source_sequence = {
            source.id: source.sequence for source in history.sources
        }
        span_order = [
            (
                source_sequence[span.source_id],
                span.start,
                span.end,
                span.quote_sha256,
            )
            for span in spans
        ]
        if span_order != sorted(span_order):
            raise NaturalHistoryError(
                f"label {label_id!r} provenance is not in canonical source order"
            )
        if any(span.quote != text for span in spans):
            raise NaturalHistoryError(
                f"label {label_id!r} text must equal every exact source span"
            )
        label = Label(
            id=label_id,
            kind=kind,
            text=text,
            status=status,
            exact=exact,
            protected=protected,
            provenance=spans,
        )
        semantic_sha256 = _label_sha256(label)
        if semantic_sha256 in seen_semantic:
            raise NaturalHistoryError(
                f"{context} repeats a semantic label"
            )
        seen_semantic.add(semantic_sha256)
        labels.append(label)
    return tuple(labels)


def _label_sha256(label: Label) -> str:
    return _canonical_sha256(label.semantic_dict())


def decode_annotation(document: Any, corpus: Corpus) -> AnnotationSet:
    """Validate one complete independent annotation pass."""

    _preflight_json(document, label="natural-history annotation")
    fields = frozenset(
        {
            "schema",
            "corpus_sha256",
            "annotator",
            "histories",
            "annotation_sha256",
        }
    )
    raw = _object(document, fields=fields, label="natural-history annotation")
    if raw["schema"] != ANNOTATION_SCHEMA:
        raise NaturalHistoryError(
            "natural-history annotation schema is unsupported"
        )
    annotation_sha256 = _verify_self_hash(
        raw,
        field="annotation_sha256",
        label="natural-history annotation",
    )
    if raw["corpus_sha256"] != corpus.corpus_sha256:
        raise NaturalHistoryError(
            "natural-history annotation corpus_sha256 does not match corpus"
        )
    annotator_fields = frozenset(
        {
            "id",
            "independent",
            "blind_to_peer_labels",
            "completed_at_utc",
            "attestation_sha256",
        }
    )
    annotator = _object(
        raw["annotator"],
        fields=annotator_fields,
        label="natural-history annotator",
    )
    _string(
        annotator["id"],
        label="natural-history annotator id",
        max_chars=256,
        identifier=True,
    )
    if (
        annotator["independent"] is not True
        or annotator["blind_to_peer_labels"] is not True
    ):
        raise NaturalHistoryError(
            "natural-history annotation must attest independent blinded work"
        )
    _timestamp(
        annotator["completed_at_utc"],
        label="natural-history annotation completed_at_utc",
    )
    _sha256(
        annotator["attestation_sha256"],
        label="natural-history annotation attestation_sha256",
    )
    latest_privacy_review = max(
        history.privacy_review["reviewed_at_utc"]
        for history in corpus.histories
    )
    if annotator["completed_at_utc"] < latest_privacy_review:
        raise NaturalHistoryError(
            "natural-history annotation precedes corpus privacy review"
        )
    history_values = raw["histories"]
    if not isinstance(history_values, list):
        raise NaturalHistoryError(
            "natural-history annotation histories must be an array"
        )
    if len(history_values) != len(corpus.histories):
        raise NaturalHistoryError(
            "natural-history annotation must include every history exactly once"
        )
    history_fields = frozenset({"history_id", "labels"})
    decoded: list[tuple[str, tuple[Label, ...]]] = []
    total_labels = 0
    seen_label_ids: set[str] = set()
    for index, (history_value, expected_history) in enumerate(
        zip(history_values, corpus.histories, strict=True)
    ):
        history_raw = _object(
            history_value,
            fields=history_fields,
            label=f"natural-history annotation history {index}",
        )
        if history_raw["history_id"] != expected_history.id:
            raise NaturalHistoryError(
                "natural-history annotation history coverage is not canonical"
            )
        labels = _decode_labels(
            history_raw["labels"],
            history=expected_history,
            context=f"annotation history {expected_history.id!r}",
            seen_document_ids=seen_label_ids,
        )
        total_labels += len(labels)
        if total_labels > _MAX_TOTAL_LABELS:
            raise NaturalHistoryError(
                f"natural-history annotation exceeds {_MAX_TOTAL_LABELS} labels"
            )
        decoded.append((expected_history.id, labels))
    return AnnotationSet(
        annotator=_freeze_json(dict(annotator)),
        histories=tuple(decoded),
        annotation_sha256=annotation_sha256,
    )


def load_annotation(
    path: str | Path,
    corpus: Corpus,
) -> AnnotationSet:
    """Strictly load one complete annotation regular file."""

    loaded = load_strict_json_file(
        path,
        limits=_DOCUMENT_LIMITS,
        label="natural-history annotation",
    )
    return decode_annotation(loaded.value, corpus)


def _validate_independent_pair(
    first: AnnotationSet,
    second: AnnotationSet,
) -> None:
    first_id = first.annotator["id"]
    second_id = second.annotator["id"]
    if first_id == second_id:
        raise NaturalHistoryError(
            "two independent annotations require distinct annotator ids"
        )
    if first.annotator["attestation_sha256"] == second.annotator[
        "attestation_sha256"
    ]:
        raise NaturalHistoryError(
            "two independent annotations require distinct attestations"
        )
    if first.annotation_sha256 == second.annotation_sha256:
        raise NaturalHistoryError(
            "two independent annotation envelopes must be distinct"
        )


def decode_adjudication(
    document: Any,
    corpus: Corpus,
    first: AnnotationSet,
    second: AnnotationSet,
) -> Adjudication:
    """Validate complete adjudication of exactly two independent passes."""

    _preflight_json(document, label="natural-history adjudication")
    _validate_independent_pair(first, second)
    fields = frozenset(
        {
            "schema",
            "corpus_sha256",
            "annotation_sha256s",
            "adjudicator",
            "histories",
            "adjudication_sha256",
        }
    )
    raw = _object(
        document,
        fields=fields,
        label="natural-history adjudication",
    )
    if raw["schema"] != ADJUDICATION_SCHEMA:
        raise NaturalHistoryError(
            "natural-history adjudication schema is unsupported"
        )
    adjudication_sha256 = _verify_self_hash(
        raw,
        field="adjudication_sha256",
        label="natural-history adjudication",
    )
    if raw["corpus_sha256"] != corpus.corpus_sha256:
        raise NaturalHistoryError(
            "natural-history adjudication corpus_sha256 does not match corpus"
        )
    expected_annotation_hashes = tuple(
        sorted((first.annotation_sha256, second.annotation_sha256))
    )
    annotation_hashes = raw["annotation_sha256s"]
    if (
        not isinstance(annotation_hashes, list)
        or tuple(annotation_hashes) != expected_annotation_hashes
    ):
        raise NaturalHistoryError(
            "natural-history adjudication must bind both annotations canonically"
        )
    adjudicator_fields = frozenset(
        {"id", "complete", "completed_at_utc", "attestation_sha256"}
    )
    adjudicator = _object(
        raw["adjudicator"],
        fields=adjudicator_fields,
        label="natural-history adjudicator",
    )
    adjudicator_id = _string(
        adjudicator["id"],
        label="natural-history adjudicator id",
        max_chars=256,
        identifier=True,
    )
    if adjudicator_id in {first.annotator["id"], second.annotator["id"]}:
        raise NaturalHistoryError(
            "adjudicator must be distinct from both independent annotators"
        )
    if adjudicator["complete"] is not True:
        raise NaturalHistoryError(
            "natural-history adjudication must attest completeness"
        )
    _timestamp(
        adjudicator["completed_at_utc"],
        label="natural-history adjudication completed_at_utc",
    )
    _sha256(
        adjudicator["attestation_sha256"],
        label="natural-history adjudication attestation_sha256",
    )
    if adjudicator["attestation_sha256"] in {
        first.annotator["attestation_sha256"],
        second.annotator["attestation_sha256"],
    }:
        raise NaturalHistoryError(
            "adjudicator requires distinct attestation evidence"
        )
    if adjudicator["completed_at_utc"] < max(
        first.annotator["completed_at_utc"],
        second.annotator["completed_at_utc"],
    ):
        raise NaturalHistoryError(
            "adjudication cannot precede either independent annotation"
        )
    history_values = raw["histories"]
    if not isinstance(history_values, list) or len(history_values) != len(
        corpus.histories
    ):
        raise NaturalHistoryError(
            "natural-history adjudication must include every history exactly once"
        )
    history_fields = frozenset({"history_id", "final_labels", "decisions"})
    decision_fields = frozenset(
        {
            "disagreement_label_sha256",
            "resolution",
            "final_label_sha256",
        }
    )
    first_by_history = first.labels_by_history
    second_by_history = second.labels_by_history
    decoded: list[tuple[str, tuple[Label, ...]]] = []
    total_final_labels = 0
    total_decisions = 0
    seen_final_label_ids: set[str] = set()
    for index, (history_value, history) in enumerate(
        zip(history_values, corpus.histories, strict=True)
    ):
        history_raw = _object(
            history_value,
            fields=history_fields,
            label=f"natural-history adjudication history {index}",
        )
        if history_raw["history_id"] != history.id:
            raise NaturalHistoryError(
                "natural-history adjudication history coverage is not canonical"
            )
        final_labels = _decode_labels(
            history_raw["final_labels"],
            history=history,
            context=f"adjudication history {history.id!r}",
            seen_document_ids=seen_final_label_ids,
        )
        total_final_labels += len(final_labels)
        if total_final_labels > _MAX_TOTAL_LABELS:
            raise NaturalHistoryError(
                f"natural-history adjudication exceeds "
                f"{_MAX_TOTAL_LABELS} final labels"
            )
        final_hashes = {_label_sha256(label) for label in final_labels}
        first_hashes = {
            _label_sha256(label)
            for label in first_by_history[history.id]
        }
        second_hashes = {
            _label_sha256(label)
            for label in second_by_history[history.id]
        }
        consensus = first_hashes & second_hashes
        disagreements = first_hashes ^ second_hashes
        decision_values = history_raw["decisions"]
        if (
            not isinstance(decision_values, list)
            or len(decision_values) > 2 * _MAX_LABELS_PER_HISTORY
        ):
            raise NaturalHistoryError(
                f"adjudication history {history.id!r} decisions must be a bounded array"
            )
        total_decisions += len(decision_values)
        if total_decisions > 2 * _MAX_TOTAL_LABELS:
            raise NaturalHistoryError(
                "natural-history adjudication exceeds the total decision limit"
            )
        seen_disagreements: set[str] = set()
        resolved_final = set(consensus)
        for decision_index, decision_value in enumerate(decision_values):
            decision = _object(
                decision_value,
                fields=decision_fields,
                label=(
                    f"adjudication history {history.id!r} "
                    f"decision {decision_index}"
                ),
            )
            disagreement_sha256 = _sha256(
                decision["disagreement_label_sha256"],
                label="adjudication disagreement_label_sha256",
            )
            if disagreement_sha256 not in disagreements:
                raise NaturalHistoryError(
                    f"adjudication history {history.id!r} decision does not "
                    "reference a disagreement"
                )
            if disagreement_sha256 in seen_disagreements:
                raise NaturalHistoryError(
                    f"adjudication history {history.id!r} repeats a decision"
                )
            seen_disagreements.add(disagreement_sha256)
            resolution = decision["resolution"]
            final_sha256 = _nullable_sha256(
                decision["final_label_sha256"],
                label="adjudication final_label_sha256",
            )
            if resolution == "accept":
                if final_sha256 != disagreement_sha256:
                    raise NaturalHistoryError(
                        "accepted disagreement must retain that semantic label"
                    )
                resolved_final.add(disagreement_sha256)
            elif resolution == "reject":
                if final_sha256 is not None:
                    raise NaturalHistoryError(
                        "rejected disagreement cannot cite a final label"
                    )
            elif resolution == "replace":
                if final_sha256 is None or final_sha256 == disagreement_sha256:
                    raise NaturalHistoryError(
                        "replacement decision must cite a different final label"
                    )
                resolved_final.add(final_sha256)
            else:
                raise NaturalHistoryError(
                    "adjudication resolution is unsupported"
                )
        if seen_disagreements != disagreements:
            raise NaturalHistoryError(
                f"adjudication history {history.id!r} has incomplete decisions"
            )
        if final_hashes != resolved_final:
            raise NaturalHistoryError(
                f"adjudication history {history.id!r} final labels do not "
                "match consensus and decisions"
            )
        decoded.append((history.id, final_labels))
    return Adjudication(
        annotation_sha256s=expected_annotation_hashes,
        adjudicator=_freeze_json(dict(adjudicator)),
        histories=tuple(decoded),
        adjudication_sha256=adjudication_sha256,
    )


def load_adjudication(
    path: str | Path,
    corpus: Corpus,
    first: AnnotationSet,
    second: AnnotationSet,
) -> Adjudication:
    """Strictly load and validate one adjudication regular file."""

    loaded = load_strict_json_file(
        path,
        limits=_DOCUMENT_LIMITS,
        label="natural-history adjudication",
    )
    return decode_adjudication(loaded.value, corpus, first, second)


def decode_split(document: Any, corpus: Corpus) -> SplitManifest:
    """Validate one frozen repository/task-group-disjoint split."""

    _preflight_json(document, label="natural-history split")
    fields = frozenset(
        {"schema", "corpus_sha256", "policy", "assignments", "split_sha256"}
    )
    raw = _object(document, fields=fields, label="natural-history split")
    if raw["schema"] != SPLIT_SCHEMA:
        raise NaturalHistoryError("natural-history split schema is unsupported")
    split_sha256 = _verify_self_hash(
        raw,
        field="split_sha256",
        label="natural-history split",
    )
    if raw["corpus_sha256"] != corpus.corpus_sha256:
        raise NaturalHistoryError(
            "natural-history split corpus_sha256 does not match corpus"
        )
    policy_fields = frozenset(
        {
            "name",
            "assignment_frozen_before_tuning",
            "test_gold_sealed",
            "created_at_utc",
            "seed_commitment_sha256",
        }
    )
    policy = _object(
        raw["policy"],
        fields=policy_fields,
        label="natural-history split policy",
    )
    if policy["name"] != "repository-and-task-group-disjoint-v1":
        raise NaturalHistoryError("natural-history split policy is unsupported")
    if (
        policy["assignment_frozen_before_tuning"] is not True
        or policy["test_gold_sealed"] is not True
    ):
        raise NaturalHistoryError(
            "natural-history split must freeze assignments and seal test gold"
        )
    _timestamp(
        policy["created_at_utc"],
        label="natural-history split created_at_utc",
    )
    _sha256(
        policy["seed_commitment_sha256"],
        label="natural-history split seed_commitment_sha256",
    )
    latest_privacy_review = max(
        history.privacy_review["reviewed_at_utc"]
        for history in corpus.histories
    )
    if policy["created_at_utc"] < latest_privacy_review:
        raise NaturalHistoryError(
            "natural-history split precedes corpus privacy review"
        )
    assignment_values = raw["assignments"]
    if not isinstance(assignment_values, list) or len(
        assignment_values
    ) != len(corpus.histories):
        raise NaturalHistoryError(
            "natural-history split must assign every history exactly once"
        )
    assignment_fields = frozenset({"history_id", "split"})
    assignments: list[tuple[str, str]] = []
    repository_splits: dict[str, str] = {}
    task_group_splits: dict[str, str] = {}
    observed_splits: set[str] = set()
    for index, (assignment_value, history) in enumerate(
        zip(assignment_values, corpus.histories, strict=True)
    ):
        assignment = _object(
            assignment_value,
            fields=assignment_fields,
            label=f"natural-history split assignment {index}",
        )
        if assignment["history_id"] != history.id:
            raise NaturalHistoryError(
                "natural-history split coverage is not canonical"
            )
        split = assignment["split"]
        if split not in _SPLITS:
            raise NaturalHistoryError(
                f"history {history.id!r} split is unsupported"
            )
        prior_repository_split = repository_splits.setdefault(
            history.repository_id, split
        )
        if prior_repository_split != split:
            raise NaturalHistoryError(
                f"repository {history.repository_id!r} leaks across splits"
            )
        prior_task_group_split = task_group_splits.setdefault(
            history.task_group_id, split
        )
        if prior_task_group_split != split:
            raise NaturalHistoryError(
                f"task group {history.task_group_id!r} leaks across splits"
            )
        observed_splits.add(split)
        assignments.append((history.id, split))
    if observed_splits != set(_SPLITS):
        raise NaturalHistoryError(
            "natural-history split must contain train, development, and test"
        )
    return SplitManifest(
        policy=_freeze_json(dict(policy)),
        assignments=tuple(assignments),
        split_sha256=split_sha256,
    )


def load_split(
    path: str | Path,
    corpus: Corpus,
) -> SplitManifest:
    """Strictly load one split-manifest regular file."""

    loaded = load_strict_json_file(
        path,
        limits=_DOCUMENT_LIMITS,
        label="natural-history split",
    )
    return decode_split(loaded.value, corpus)


def gold_free_document(
    corpus: Corpus,
    split: SplitManifest,
) -> Mapping[str, Any]:
    """Build the canonical all-history export with no annotation structure."""

    split_by_history = split.split_by_history
    document: dict[str, Any] = {
        "schema": GOLD_FREE_SCHEMA,
        "corpus_sha256": corpus.corpus_sha256,
        "split_sha256": split.split_sha256,
        "export_scope": "all-splits",
        "histories": [
            {
                "history_id": history.id,
                "repository_id": history.repository_id,
                "task_group_id": history.task_group_id,
                "task_id": history.task_id,
                "split": split_by_history[history.id],
                "sources": [source.to_dict() for source in history.sources],
            }
            for history in corpus.histories
        ],
    }
    _assert_no_gold_keys(document)
    document["gold_free_sha256"] = _canonical_sha256(document)
    return _freeze_json(document)


def _assert_no_gold_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        leaked = set(value) & _GOLD_KEYS
        if leaked:
            raise NaturalHistoryError(
                "gold-free export contains label-bearing keys: "
                + ", ".join(sorted(leaked))
            )
        for nested in value.values():
            _assert_no_gold_keys(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _assert_no_gold_keys(nested)


def decode_gold_free(
    document: Any,
    corpus: Corpus,
    split: SplitManifest,
) -> Mapping[str, Any]:
    """Verify exact canonical gold-free export equivalence."""

    _preflight_json(document, label="natural-history gold-free export")
    if not isinstance(document, dict):
        raise NaturalHistoryError("natural-history gold-free export must be an object")
    if document.get("schema") != GOLD_FREE_SCHEMA:
        raise NaturalHistoryError(
            "natural-history gold-free schema is unsupported"
        )
    _assert_no_gold_keys(document)
    claimed = document.get("gold_free_sha256")
    _sha256(
        claimed,
        label="natural-history gold-free gold_free_sha256",
    )
    unsigned = dict(document)
    unsigned.pop("gold_free_sha256")
    if _canonical_sha256(unsigned) != claimed:
        raise NaturalHistoryError("natural-history gold-free SHA-256 mismatch")
    expected = _thaw_json(gold_free_document(corpus, split))
    if document != expected:
        raise NaturalHistoryError(
            "natural-history gold-free export does not match corpus and split"
        )
    return _freeze_json(document)


def load_gold_free(
    path: str | Path,
    corpus: Corpus,
    split: SplitManifest,
) -> Mapping[str, Any]:
    """Strictly load and verify a gold-free export regular file."""

    loaded = load_strict_json_file(
        path,
        limits=_DOCUMENT_LIMITS,
        label="natural-history gold-free export",
    )
    return decode_gold_free(loaded.value, corpus, split)


def write_gold_free(
    path: str | Path,
    corpus: Corpus,
    split: SplitManifest,
) -> Mapping[str, Any]:
    """Atomically write a canonical gold-free export."""

    document = gold_free_document(corpus, split)
    rendered = (
        json.dumps(
            _thaw_json(document),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )
    atomic_write_text(path, rendered)
    return document


def _decode_run(value: Any) -> dict[str, Any]:
    fields = frozenset(
        {
            "run_id",
            "system_id",
            "system_revision",
            "started_at_utc",
            "finished_at_utc",
            "tokenizer_id",
            "claim_scope",
            "semantic_completeness_claimed",
        }
    )
    raw = _object(value, fields=fields, label="natural-history report run")
    for name in ("run_id", "system_id", "tokenizer_id"):
        _string(
            raw[name],
            label=f"natural-history report {name}",
            max_chars=256,
            identifier=True,
        )
    _sha256(
        raw["system_revision"],
        label="natural-history report system_revision",
    )
    started_at = _timestamp(
        raw["started_at_utc"],
        label="natural-history report started_at_utc",
    )
    finished_at = _timestamp(
        raw["finished_at_utc"],
        label="natural-history report finished_at_utc",
    )
    if finished_at < started_at:
        raise NaturalHistoryError(
            "natural-history report finished_at_utc precedes started_at_utc"
        )
    if raw["claim_scope"] != "diagnostic-only":
        raise NaturalHistoryError(
            "natural-history report claim_scope must remain diagnostic-only"
        )
    if raw["semantic_completeness_claimed"] is not False:
        raise NaturalHistoryError(
            "natural-history report cannot claim semantic completeness"
        )
    return dict(raw)


def _nullable_integer(value: Any, *, label: str) -> int | None:
    if value is None:
        return None
    return _integer(value, label=label)


def decode_report(
    document: Any,
    corpus: Corpus,
    adjudication: Adjudication,
    split: SplitManifest,
) -> Mapping[str, Any]:
    """Validate a report that retains every success and failure."""

    _preflight_json(document, label="natural-history report")
    fields = frozenset(
        {
            "schema",
            "corpus_sha256",
            "adjudication_sha256",
            "split_sha256",
            "run",
            "histories",
            "summary",
            "report_sha256",
        }
    )
    raw = _object(document, fields=fields, label="natural-history report")
    if raw["schema"] != REPORT_SCHEMA:
        raise NaturalHistoryError("natural-history report schema is unsupported")
    report_sha256 = _verify_self_hash(
        raw,
        field="report_sha256",
        label="natural-history report",
    )
    if raw["corpus_sha256"] != corpus.corpus_sha256:
        raise NaturalHistoryError(
            "natural-history report corpus_sha256 does not match corpus"
        )
    if raw["adjudication_sha256"] != adjudication.adjudication_sha256:
        raise NaturalHistoryError(
            "natural-history report adjudication_sha256 does not match"
        )
    if raw["split_sha256"] != split.split_sha256:
        raise NaturalHistoryError(
            "natural-history report split_sha256 does not match"
        )
    run = _decode_run(raw["run"])
    if run["started_at_utc"] < adjudication.adjudicator["completed_at_utc"]:
        raise NaturalHistoryError(
            "natural-history report starts before adjudication completed"
        )
    if run["started_at_utc"] < split.policy["created_at_utc"]:
        raise NaturalHistoryError(
            "natural-history report starts before split creation"
        )
    history_values = raw["histories"]
    if not isinstance(history_values, list) or len(history_values) != len(
        corpus.histories
    ):
        raise NaturalHistoryError(
            "natural-history report must retain every history exactly once"
        )
    history_fields = frozenset(
        {
            "history_id",
            "split",
            "outcome",
            "failure_code",
            "expected_label_count",
            "protected_expected",
            "exact_expected",
            "observed_label_count",
            "matched_label_count",
            "protected_matched",
            "exact_matched",
            "provenance_errors",
            "authority_violations",
            "stale_claims",
            "unresolved_to_fact",
            "source_characters",
            "active_characters",
        }
    )
    split_by_history = split.split_by_history
    labels_by_history = adjudication.labels_by_history
    summary_counts = {
        "history_count": len(corpus.histories),
        "scored_history_count": 0,
        "failed_history_count": 0,
        "expected_label_count": 0,
        "matched_label_count": 0,
        "protected_expected": 0,
        "protected_matched": 0,
        "exact_expected": 0,
        "exact_matched": 0,
    }
    for index, (history_value, history) in enumerate(
        zip(history_values, corpus.histories, strict=True)
    ):
        result = _object(
            history_value,
            fields=history_fields,
            label=f"natural-history report history {index}",
        )
        if result["history_id"] != history.id:
            raise NaturalHistoryError(
                "natural-history report history coverage is not canonical"
            )
        if result["split"] != split_by_history[history.id]:
            raise NaturalHistoryError(
                f"natural-history report history {history.id!r} split mismatch"
            )
        labels = labels_by_history[history.id]
        expected_count = len(labels)
        protected_expected = sum(label.protected for label in labels)
        exact_expected = sum(label.exact for label in labels)
        for name, minimum in (
            ("expected_label_count", 0),
            ("protected_expected", 0),
            ("exact_expected", 0),
            ("source_characters", 1),
        ):
            _integer(
                result[name],
                label=(
                    f"natural-history report history {history.id!r} {name}"
                ),
                minimum=minimum,
            )
        if result["expected_label_count"] != expected_count:
            raise NaturalHistoryError(
                f"natural-history report history {history.id!r} expected "
                "label count mismatch"
            )
        if result["protected_expected"] != protected_expected:
            raise NaturalHistoryError(
                f"natural-history report history {history.id!r} protected "
                "count mismatch"
            )
        if result["exact_expected"] != exact_expected:
            raise NaturalHistoryError(
                f"natural-history report history {history.id!r} exact "
                "count mismatch"
            )
        source_characters = sum(
            len(source.content) for source in history.sources
        )
        if result["source_characters"] != source_characters:
            raise NaturalHistoryError(
                f"natural-history report history {history.id!r} source "
                "character count mismatch"
            )
        summary_counts["expected_label_count"] += expected_count
        summary_counts["protected_expected"] += protected_expected
        summary_counts["exact_expected"] += exact_expected
        outcome = result["outcome"]
        metric_names = (
            "observed_label_count",
            "matched_label_count",
            "protected_matched",
            "exact_matched",
            "provenance_errors",
            "authority_violations",
            "stale_claims",
            "unresolved_to_fact",
            "active_characters",
        )
        metrics = {
            name: _nullable_integer(
                result[name],
                label=(
                    f"natural-history report history {history.id!r} {name}"
                ),
            )
            for name in metric_names
        }
        if outcome == "failed":
            _string(
                result["failure_code"],
                label=(
                    f"natural-history report history {history.id!r} "
                    "failure_code"
                ),
                max_chars=256,
                identifier=True,
            )
            if any(value is not None for value in metrics.values()):
                raise NaturalHistoryError(
                    f"failed history {history.id!r} cannot carry scored metrics"
                )
            summary_counts["failed_history_count"] += 1
        elif outcome == "scored":
            if result["failure_code"] is not None:
                raise NaturalHistoryError(
                    f"scored history {history.id!r} cannot carry failure_code"
                )
            if any(value is None for value in metrics.values()):
                raise NaturalHistoryError(
                    f"scored history {history.id!r} has missing metrics"
                )
            matched_count = metrics["matched_label_count"]
            protected_matched = metrics["protected_matched"]
            exact_matched = metrics["exact_matched"]
            observed_count = metrics["observed_label_count"]
            assert matched_count is not None
            assert protected_matched is not None
            assert exact_matched is not None
            assert observed_count is not None
            if matched_count > min(expected_count, observed_count):
                raise NaturalHistoryError(
                    f"history {history.id!r} matched count is impossible"
                )
            if protected_matched > protected_expected:
                raise NaturalHistoryError(
                    f"history {history.id!r} protected matched count is impossible"
                )
            if exact_matched > exact_expected:
                raise NaturalHistoryError(
                    f"history {history.id!r} exact matched count is impossible"
                )
            if protected_matched > matched_count:
                raise NaturalHistoryError(
                    f"history {history.id!r} protected matched count exceeds "
                    "all matched labels"
                )
            if exact_matched > protected_matched:
                raise NaturalHistoryError(
                    f"history {history.id!r} exact matched count exceeds "
                    "protected matched labels"
                )
            summary_counts["scored_history_count"] += 1
            summary_counts["matched_label_count"] += matched_count
            summary_counts["protected_matched"] += protected_matched
            summary_counts["exact_matched"] += exact_matched
        else:
            raise NaturalHistoryError(
                f"natural-history report history {history.id!r} outcome "
                "is unsupported"
            )
    summary_fields = frozenset(
        {
            "history_count",
            "scored_history_count",
            "failed_history_count",
            "expected_label_count",
            "matched_label_count",
            "protected_expected",
            "protected_matched",
            "exact_expected",
            "exact_matched",
            "all_histories_retained",
            "complete_without_failures",
            "natural_history_claimed",
            "semantic_completeness_claimed",
            "claim_ready",
        }
    )
    summary = _object(
        raw["summary"],
        fields=summary_fields,
        label="natural-history report summary",
    )
    for name in summary_counts:
        _integer(
            summary[name],
            label=f"natural-history report summary {name}",
        )
    for name in (
        "all_histories_retained",
        "complete_without_failures",
        "natural_history_claimed",
        "semantic_completeness_claimed",
        "claim_ready",
    ):
        if not isinstance(summary[name], bool):
            raise NaturalHistoryError(
                f"natural-history report summary {name} must be a boolean"
            )
    expected_summary = {
        **summary_counts,
        "all_histories_retained": True,
        "complete_without_failures": (
            summary_counts["failed_history_count"] == 0
        ),
        "natural_history_claimed": corpus.evidence_state[
            "natural_history_claimed"
        ],
        "semantic_completeness_claimed": False,
        "claim_ready": False,
    }
    if summary != expected_summary:
        raise NaturalHistoryError(
            "natural-history report summary does not reconcile every history"
        )
    result = dict(raw)
    result["report_sha256"] = report_sha256
    return _freeze_json(result)


def load_report(
    path: str | Path,
    corpus: Corpus,
    adjudication: Adjudication,
    split: SplitManifest,
) -> Mapping[str, Any]:
    """Strictly load and verify a natural-history report regular file."""

    loaded = load_strict_json_file(
        path,
        limits=_DOCUMENT_LIMITS,
        label="natural-history report",
    )
    return decode_report(loaded.value, corpus, adjudication, split)


def verify_kit(
    *,
    corpus_path: str | Path = DEFAULT_CORPUS,
    annotation_paths: Sequence[str | Path] = DEFAULT_ANNOTATIONS,
    adjudication_path: str | Path = DEFAULT_ADJUDICATION,
    split_path: str | Path = DEFAULT_SPLIT,
    gold_free_path: str | Path = DEFAULT_GOLD_FREE,
    report_path: str | Path = DEFAULT_REPORT,
) -> dict[str, Any]:
    """Run the standalone conformance check over all six contracts."""

    if len(annotation_paths) != 2:
        raise NaturalHistoryError(
            "natural-history conformance requires exactly two annotations"
        )
    corpus = load_corpus(corpus_path)
    first = load_annotation(annotation_paths[0], corpus)
    second = load_annotation(annotation_paths[1], corpus)
    adjudication = load_adjudication(
        adjudication_path,
        corpus,
        first,
        second,
    )
    split = load_split(split_path, corpus)
    gold_free = load_gold_free(gold_free_path, corpus, split)
    report = load_report(
        report_path,
        corpus,
        adjudication,
        split,
    )
    final_labels = sum(
        len(labels) for _, labels in adjudication.histories
    )
    return {
        "schema": VERIFICATION_SCHEMA,
        "verified": True,
        "fixture_only": corpus.evidence_state["kind"] == "contract-fixture",
        "natural_history_claimed": corpus.evidence_state[
            "natural_history_claimed"
        ],
        "semantic_completeness_claimed": False,
        "history_count": len(corpus.histories),
        "source_count": sum(
            len(history.sources) for history in corpus.histories
        ),
        "attempted_history_count": corpus.collection_accounting[
            "attempted_history_count"
        ],
        "explicit_exclusion_count": len(
            corpus.collection_accounting["exclusions"]
        ),
        "independent_annotator_count": 2,
        "adjudicated_label_count": final_labels,
        "corpus_sha256": corpus.corpus_sha256,
        "annotation_sha256s": list(adjudication.annotation_sha256s),
        "adjudication_sha256": adjudication.adjudication_sha256,
        "split_sha256": split.split_sha256,
        "gold_free_sha256": gold_free["gold_free_sha256"],
        "report_sha256": report["report_sha256"],
        "claim_ready": False,
    }


def _render(value: Any) -> str:
    return (
        json.dumps(
            _thaw_json(value),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run standalone kit verification or emit a canonical gold-free export."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument(
        "--annotation",
        type=Path,
        action="append",
        dest="annotations",
        help="repeat exactly twice; defaults to the two contract fixtures",
    )
    parser.add_argument(
        "--adjudication",
        type=Path,
        default=DEFAULT_ADJUDICATION,
    )
    parser.add_argument("--split", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument(
        "--gold-free",
        type=Path,
        default=DEFAULT_GOLD_FREE,
        help="gold-free export to verify",
    )
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--export-gold-free",
        type=Path,
        help="atomically write a fresh canonical gold-free export",
    )
    args = parser.parse_args(argv)
    annotations = (
        tuple(args.annotations)
        if args.annotations is not None
        else DEFAULT_ANNOTATIONS
    )
    try:
        if args.export_gold_free is not None:
            corpus = load_corpus(args.corpus)
            split = load_split(args.split, corpus)
            output = write_gold_free(args.export_gold_free, corpus, split)
        else:
            output = verify_kit(
                corpus_path=args.corpus,
                annotation_paths=annotations,
                adjudication_path=args.adjudication,
                split_path=args.split,
                gold_free_path=args.gold_free,
                report_path=args.report,
            )
        sys.stdout.write(_render(output))
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
