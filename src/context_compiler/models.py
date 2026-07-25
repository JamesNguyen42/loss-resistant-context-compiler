"""Core data model for typed, traceable agent memory.

The module intentionally uses only the Python standard library.  A context
compiler is infrastructure, so its audit path should not disappear when an
optional validation or model-provider dependency is unavailable.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

SCHEMA_VERSION = "1.0"
COMPILATION_METRICS_SCHEMA = "compilation-metrics-0.1"
MAX_SOURCE_ID_CHARS = 1_024
MAX_SOURCE_ROLE_CHARS = 128
MAX_SOURCE_TIMESTAMP_CHARS = 256
AUTHENTICATED_AUTHORITY_METADATA_KEY = "ctxc_authenticated_authority"
PRIMARY_EXTRACTOR_FAILED_MESSAGE = (
    "The primary extractor failed; verified deterministic recovery was used."
)
PRIMARY_EXTRACTOR_DEGRADED_MESSAGE = (
    "The primary extractor returned unusable output; verified deterministic "
    "recovery was used."
)
ADDITIVE_SAFETY_EXTRACTOR_FAILED_MESSAGE = (
    "The additive safety extractor failed; built-in deterministic "
    "certification remained active."
)


class _FrozenDict(dict):
    """JSON-serializable dictionary that rejects mutation after construction."""

    @staticmethod
    def _immutable(*_args: Any, **_kwargs: Any) -> None:
        raise TypeError("verified state is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __ior__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable

    def __deepcopy__(self, _memo: dict[int, Any]) -> _FrozenDict:
        return self

    def __reduce__(self) -> tuple[type[_FrozenDict], tuple[dict[str, Any]]]:
        return _FrozenDict, (dict(self),)


class _FrozenList(list):
    """JSON-serializable list that rejects mutation after construction."""

    @staticmethod
    def _immutable(*_args: Any, **_kwargs: Any) -> None:
        raise TypeError("verified state is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __iadd__ = _immutable
    __imul__ = _immutable
    append = _immutable
    clear = _immutable
    extend = _immutable
    insert = _immutable
    pop = _immutable
    remove = _immutable
    reverse = _immutable
    sort = _immutable

    def __deepcopy__(self, _memo: dict[int, Any]) -> _FrozenList:
        return self

    def __reduce__(self) -> tuple[type[_FrozenList], tuple[list[Any]]]:
        return _FrozenList, (list(self),)


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("source metadata object keys must be strings")
        return _FrozenDict({key: _freeze_json(entry) for key, entry in value.items()})
    if isinstance(value, list):
        return _FrozenList(_freeze_json(entry) for entry in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError("source metadata numbers must be finite")
        return value
    raise TypeError("source metadata must contain only JSON values")


def _thaw_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _thaw_json(entry) for key, entry in value.items()}
    if isinstance(value, list):
        return [_thaw_json(entry) for entry in value]
    return copy.deepcopy(value)


def stable_hash(value: str, *, length: int = 24) -> str:
    """Return a stable, compact SHA-256 identifier for *value*."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def stable_hash_parts(*values: Any, length: int = 24) -> str:
    """Hash a typed tuple without delimiter ambiguity."""

    canonical = json.dumps(
        values,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return stable_hash(canonical, length=length)


def _validate_source_field_length(value: str, *, label: str, maximum: int) -> None:
    if len(value) > maximum:
        raise ValueError(f"{label} exceeds {maximum} characters")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class MemoryKind(StrEnum):
    GOAL = "goal"
    CONSTRAINT = "constraint"
    USER_CORRECTION = "user_correction"
    CONFIRMED_FACT = "confirmed_fact"
    DECISION = "decision"
    UNRESOLVED = "unresolved"
    EXACT_ERROR = "exact_error"
    EXACT_REFERENCE = "exact_reference"
    DISCARDED_ATTEMPT = "discarded_attempt"
    PROGRESS = "progress"
    CONTEXT = "context"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DISCARDED = "discarded"
    CONFLICTING = "conflicting"


class IssueSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


PROTECTED_KINDS = frozenset(
    {
        MemoryKind.GOAL,
        MemoryKind.CONSTRAINT,
        MemoryKind.USER_CORRECTION,
        MemoryKind.UNRESOLVED,
        MemoryKind.EXACT_ERROR,
        MemoryKind.EXACT_REFERENCE,
    }
)


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """An immutable message or tool event in the source history."""

    id: str
    sequence: int
    role: str
    content: str
    timestamp: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    content_sha256: str = ""
    record_sha256: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise TypeError("source record id must be a non-empty string")
        _validate_source_field_length(
            self.id,
            label="source record id",
            maximum=MAX_SOURCE_ID_CHARS,
        )
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise TypeError("source sequence must be an integer")
        if self.sequence < 0:
            raise ValueError("source sequence cannot be negative")
        if not isinstance(self.role, str) or not self.role.strip():
            raise TypeError("source role must be a non-empty string")
        _validate_source_field_length(
            self.role,
            label="source role",
            maximum=MAX_SOURCE_ROLE_CHARS,
        )
        if not isinstance(self.content, str):
            raise TypeError("source content must be a string")
        if self.timestamp is not None and not isinstance(self.timestamp, str):
            raise TypeError("source timestamp must be a string or null")
        if self.timestamp is not None:
            _validate_source_field_length(
                self.timestamp,
                label="source timestamp",
                maximum=MAX_SOURCE_TIMESTAMP_CHARS,
            )
        if not isinstance(self.metadata, dict):
            raise TypeError("source metadata must be an object")
        object.__setattr__(self, "metadata", _freeze_json(self.metadata))
        if not isinstance(self.content_sha256, str):
            raise TypeError("source content_sha256 must be a string")
        if not isinstance(self.record_sha256, str):
            raise TypeError("source record_sha256 must be a string")
        digest = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if self.content_sha256 and self.content_sha256 != digest:
            raise ValueError(f"content hash mismatch for source {self.id}")
        object.__setattr__(self, "content_sha256", digest)
        record_digest = self._compute_record_sha256()
        if self.record_sha256 and self.record_sha256 != record_digest:
            raise ValueError(f"record hash mismatch for source {self.id}")
        object.__setattr__(self, "record_sha256", record_digest)

    @classmethod
    def create(
        cls,
        *,
        sequence: int,
        role: str,
        content: str,
        id: str | None = None,
        timestamp: str | None = None,
        metadata: dict[str, Any] | None = None,
        content_sha256: str = "",
        record_sha256: str = "",
    ) -> SourceRecord:
        if id is not None and (not isinstance(id, str) or not id):
            raise ValueError("source id must be null or a non-empty string")
        if id is not None:
            _validate_source_field_length(
                id,
                label="source id",
                maximum=MAX_SOURCE_ID_CHARS,
            )
        if isinstance(role, str):
            _validate_source_field_length(
                role,
                label="source role",
                maximum=MAX_SOURCE_ROLE_CHARS,
            )
        if isinstance(timestamp, str):
            _validate_source_field_length(
                timestamp,
                label="source timestamp",
                maximum=MAX_SOURCE_TIMESTAMP_CHARS,
            )
        metadata_value = {} if metadata is None else copy.deepcopy(metadata)
        generated_hash = stable_hash_parts(
            sequence,
            role,
            content,
            timestamp,
            metadata_value,
            length=20,
        )
        record_id = id if id is not None else f"s{sequence:06d}-{generated_hash}"
        return cls(
            id=record_id,
            sequence=sequence,
            role=role,
            content=content,
            timestamp=timestamp,
            metadata=metadata_value,
            content_sha256=content_sha256,
            record_sha256=record_sha256,
        )

    def _compute_record_sha256(self) -> str:
        try:
            canonical = json.dumps(
                [
                    self.id,
                    self.sequence,
                    self.role,
                    self.content,
                    self.timestamp,
                    self.metadata,
                ],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise TypeError("source metadata must contain canonical JSON values") from exc
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def ensure_integrity(self) -> None:
        content_digest = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if content_digest != self.content_sha256:
            raise ValueError(f"content hash mismatch for source {self.id}")
        if self._compute_record_sha256() != self.record_sha256:
            raise ValueError(f"record hash mismatch for source {self.id}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sequence": self.sequence,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": _thaw_json(self.metadata),
            "content_sha256": self.content_sha256,
            "record_sha256": self.record_sha256,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any], *, default_sequence: int = 0) -> SourceRecord:
        if not isinstance(value, dict):
            raise TypeError("source record must be an object")
        if "role" not in value or "content" not in value:
            raise ValueError("source record requires role and content")
        record_id = value.get("id")
        if record_id is not None and not isinstance(record_id, str):
            raise TypeError("source id must be a string or null")
        sequence = value.get("sequence", default_sequence)
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise TypeError("source sequence must be an integer")
        role = value["role"]
        content = value["content"]
        timestamp = value.get("timestamp")
        metadata = value.get("metadata", {})
        content_sha256 = value.get("content_sha256", "")
        record_sha256 = value.get("record_sha256", "")
        return cls.create(
            id=record_id,
            sequence=sequence,
            role=role,
            content=content,
            timestamp=timestamp,
            metadata=metadata,
            content_sha256=content_sha256,
            record_sha256=record_sha256,
        )


def source_is_untrusted_historical(source: SourceRecord) -> bool:
    """Return whether a connector explicitly marked assistant/tool state untrusted.

    Absence of the marker intentionally preserves the standalone API's
    historical authority behavior. Only the optional connector writes it.
    """

    return (
        source.role.strip().casefold() in {"assistant", "tool", "function"}
        and source.metadata.get(AUTHENTICATED_AUTHORITY_METADATA_KEY) is False
    )


@dataclass(frozen=True, slots=True)
class ProvenanceSpan:
    """A byte-independent character span anchored to immutable source text."""

    source_id: str
    start: int
    end: int
    quote: str
    quote_sha256: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not self.source_id:
            raise TypeError("provenance source_id must be a non-empty string")
        _validate_source_field_length(
            self.source_id,
            label="provenance source_id",
            maximum=MAX_SOURCE_ID_CHARS,
        )
        if isinstance(self.start, bool) or not isinstance(self.start, int):
            raise TypeError("provenance start must be an integer")
        if isinstance(self.end, bool) or not isinstance(self.end, int):
            raise TypeError("provenance end must be an integer")
        if not isinstance(self.quote, str):
            raise TypeError("provenance quote must be a string")
        if not isinstance(self.quote_sha256, str):
            raise TypeError("provenance quote_sha256 must be a string")
        if self.start < 0 or self.end < self.start:
            raise ValueError("invalid provenance offsets")
        digest = hashlib.sha256(self.quote.encode("utf-8")).hexdigest()
        if self.quote_sha256 and self.quote_sha256 != digest:
            raise ValueError("provenance quote hash mismatch")
        object.__setattr__(self, "quote_sha256", digest)

    @classmethod
    def from_source(cls, source: SourceRecord, start: int, end: int) -> ProvenanceSpan:
        if end > len(source.content):
            raise ValueError("provenance span exceeds source content")
        return cls(source_id=source.id, start=start, end=end, quote=source.content[start:end])

    def validates(self, sources: dict[str, SourceRecord]) -> bool:
        source = sources.get(self.source_id)
        if source is None or self.end > len(source.content):
            return False
        actual = source.content[self.start : self.end]
        actual_digest = hashlib.sha256(actual.encode("utf-8")).hexdigest()
        return actual == self.quote and actual_digest == self.quote_sha256

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "start": self.start,
            "end": self.end,
            "quote": self.quote,
            "quote_sha256": self.quote_sha256,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ProvenanceSpan:
        if not isinstance(value, dict):
            raise TypeError("provenance span must be an object")
        expected = {"source_id", "start", "end", "quote", "quote_sha256"}
        if set(value) != expected:
            missing = sorted(expected - set(value))
            extra = sorted(set(value) - expected)
            detail = []
            if missing:
                detail.append("missing " + ", ".join(missing))
            if extra:
                detail.append("unknown " + ", ".join(extra))
            raise ValueError("invalid provenance fields: " + "; ".join(detail))
        source_id = value.get("source_id")
        start = value.get("start")
        end = value.get("end")
        quote = value.get("quote")
        quote_sha256 = value.get("quote_sha256", "")
        if not isinstance(source_id, str) or not source_id:
            raise TypeError("provenance source_id must be a non-empty string")
        if isinstance(start, bool) or not isinstance(start, int):
            raise TypeError("provenance start must be an integer")
        if isinstance(end, bool) or not isinstance(end, int):
            raise TypeError("provenance end must be an integer")
        if not isinstance(quote, str):
            raise TypeError("provenance quote must be a string")
        if not isinstance(quote_sha256, str):
            raise TypeError("provenance quote_sha256 must be a string")
        return cls(
            source_id=source_id,
            start=start,
            end=end,
            quote=quote,
            quote_sha256=quote_sha256,
        )


_ATOM_PREFIX = re.compile(
    r"^\s*(?:(?:[-*+]|\d+[.)])\s+|[A-Za-z_ -]+?\s*:\s*)?$"
)
_LINE_BREAK = re.compile(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")


def provenance_span_is_atomic(source: SourceRecord, span: ProvenanceSpan) -> bool:
    """Return whether a span covers a whole line atom or sentence-like clause."""

    if span.source_id != source.id or span.end > len(source.content):
        return False
    if _LINE_BREAK.search(source.content, span.start, span.end):
        return False
    line_start = 0
    for boundary in _LINE_BREAK.finditer(source.content, 0, span.start):
        line_start = boundary.end()
    next_boundary = _LINE_BREAK.search(source.content, span.end)
    line_end = (
        next_boundary.start()
        if next_boundary is not None
        else len(source.content)
    )
    left = source.content[line_start:span.start]
    right = source.content[span.end:line_end]
    left_trimmed = left.rstrip()
    left_ok = bool(_ATOM_PREFIX.fullmatch(left)) or left_trimmed.endswith(
        (".", "?", "!", ";")
    ) or bool(re.search(r"\b(?:and|but|or)$", left_trimmed, re.I))
    right_trimmed = right.lstrip()
    right_ok = (
        not right_trimmed
        or span.quote.rstrip().endswith((".", "?", "!", ";"))
        or right_trimmed.startswith((".", "?", "!", ";"))
        or bool(re.match(r"(?:and|but|or)\b", right_trimmed, re.I))
    )
    return left_ok and right_ok


@dataclass(slots=True)
class MemoryItem:
    """One typed semantic commitment with source-level provenance."""

    id: str
    kind: MemoryKind
    text: str
    provenance: list[ProvenanceSpan]
    status: MemoryStatus = MemoryStatus.ACTIVE
    priority: int = 50
    confidence: float = 1.0
    exact: bool = False
    tags: list[str] = field(default_factory=list)
    supersedes: list[str] = field(default_factory=list)
    conflicts_with: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    _sealed: bool = field(default=False, init=False, repr=False, compare=False)

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_sealed", False):
            raise TypeError("verified memory item is immutable")
        object.__setattr__(self, name, value)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("memory id cannot be empty")
        if not isinstance(self.kind, MemoryKind):
            raise TypeError("memory kind must be a MemoryKind")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("memory text cannot be empty")
        if not isinstance(self.provenance, list) or not self.provenance:
            raise ValueError("memory item requires provenance")
        if not all(isinstance(span, ProvenanceSpan) for span in self.provenance):
            raise TypeError("memory provenance must contain ProvenanceSpan values")
        if not isinstance(self.status, MemoryStatus):
            raise TypeError("memory status must be a MemoryStatus")
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise TypeError("priority must be an integer")
        if not 0 <= self.priority <= 100:
            raise ValueError("priority must be between 0 and 100")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise TypeError("confidence must be numeric")
        if not math.isfinite(float(self.confidence)):
            raise ValueError("confidence must be finite")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if not isinstance(self.exact, bool):
            raise TypeError("exact must be a boolean")
        for name in ("tags", "supersedes", "conflicts_with"):
            collection = getattr(self, name)
            if not isinstance(collection, list) or not all(
                isinstance(entry, str) for entry in collection
            ):
                raise TypeError(f"memory {name} must be a list of strings")
            setattr(self, name, list(collection))
        if not isinstance(self.metadata, dict):
            raise TypeError("memory metadata must be an object")
        self.provenance = list(self.provenance)
        self.metadata = copy.deepcopy(self.metadata)

    def seal(self) -> MemoryItem:
        """Recursively freeze this item after it has passed resolution and verification."""

        if self._sealed:
            return self
        object.__setattr__(self, "provenance", _FrozenList(self.provenance))
        object.__setattr__(self, "tags", _FrozenList(self.tags))
        object.__setattr__(self, "supersedes", _FrozenList(self.supersedes))
        object.__setattr__(self, "conflicts_with", _FrozenList(self.conflicts_with))
        object.__setattr__(self, "metadata", _freeze_json(self.metadata))
        object.__setattr__(self, "_sealed", True)
        return self

    @property
    def protected(self) -> bool:
        return self.kind in PROTECTED_KINDS or "resolves-protected" in self.tags

    @property
    def source_sequence(self) -> int:
        value = self.metadata.get("source_sequence", -1)
        if isinstance(value, bool) or not isinstance(value, int):
            return -1
        return value

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "text": self.text,
            "status": self.status.value,
            "priority": self.priority,
            "confidence": self.confidence,
            "exact": self.exact,
            "protected": self.protected,
            "tags": list(self.tags),
            "supersedes": list(self.supersedes),
            "conflicts_with": list(self.conflicts_with),
            "metadata": _thaw_json(self.metadata),
            "provenance": [span.to_dict() for span in self.provenance],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MemoryItem:
        if not isinstance(value, dict):
            raise TypeError("memory item must be an object")
        expected = {
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
        if set(value) != expected:
            missing = sorted(expected - set(value))
            extra = sorted(set(value) - expected)
            detail = []
            if missing:
                detail.append("missing " + ", ".join(missing))
            if extra:
                detail.append("unknown " + ", ".join(extra))
            raise ValueError("invalid memory item fields: " + "; ".join(detail))
        item_id = value.get("id")
        kind = value.get("kind")
        text = value.get("text")
        provenance = value.get("provenance")
        status = value.get("status", "active")
        priority = value.get("priority", 50)
        confidence = value.get("confidence", 1.0)
        exact = value.get("exact", False)
        tags = value.get("tags", [])
        supersedes = value.get("supersedes", [])
        conflicts_with = value.get("conflicts_with", [])
        metadata = value.get("metadata", {})
        protected = value.get("protected")
        if not isinstance(item_id, str) or not item_id:
            raise TypeError("memory id must be a non-empty string")
        if not isinstance(kind, str):
            raise TypeError("memory kind must be a string")
        if not isinstance(text, str):
            raise TypeError("memory text must be a string")
        if not isinstance(provenance, list):
            raise TypeError("memory provenance must be a list")
        if not isinstance(status, str):
            raise TypeError("memory status must be a string")
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise TypeError("memory priority must be an integer")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise TypeError("memory confidence must be numeric")
        if not isinstance(exact, bool):
            raise TypeError("memory exact must be a boolean")
        if not isinstance(protected, bool):
            raise TypeError("memory protected must be a boolean")
        for name, collection in (
            ("tags", tags),
            ("supersedes", supersedes),
            ("conflicts_with", conflicts_with),
        ):
            if not isinstance(collection, list) or not all(
                isinstance(entry, str) for entry in collection
            ):
                raise TypeError(f"memory {name} must be a list of strings")
        if not isinstance(metadata, dict):
            raise TypeError("memory metadata must be an object")
        item = cls(
            id=item_id,
            kind=MemoryKind(kind),
            text=text,
            provenance=[ProvenanceSpan.from_dict(span) for span in provenance],
            status=MemoryStatus(status),
            priority=priority,
            confidence=float(confidence),
            exact=exact,
            tags=list(tags),
            supersedes=list(supersedes),
            conflicts_with=list(conflicts_with),
            metadata=copy.deepcopy(metadata),
        )
        if protected is not item.protected:
            raise ValueError("serialized protected flag disagrees with typed semantics")
        return item


_LABELED_WRAPPER_PREFIX = re.compile(
    r"^\s*(?:(?:[A-Za-z][A-Za-z0-9_ -]{0,127}\s*:\s*)?"
    r"(?:(?:[-*+]|\d+[.)])\s+)?)$"
)


def memory_item_covers_candidate(
    candidate: MemoryItem,
    retained: MemoryItem,
) -> bool:
    """Return whether a retained atom satisfies one extracted candidate.

    Besides the historical exact-text/span relation, an explicit ASCII label
    or bullet may wrap an otherwise identical ordinary atom. This lets a
    domain-label extractor retain the clean value without a built-in recovery
    pass reintroducing the label as a second claim.
    """

    if candidate.kind != retained.kind:
        return False
    candidate_spans = {
        (span.source_id, span.start, span.end)
        for span in candidate.provenance
    }
    retained_spans = {
        (span.source_id, span.start, span.end)
        for span in retained.provenance
    }
    if (
        candidate.text.strip() == retained.text.strip()
        and candidate_spans <= retained_spans
    ):
        return True
    if (
        candidate.exact
        or retained.exact
        or len(candidate.provenance) != 1
        or len(retained.provenance) != 1
    ):
        return False
    outer = candidate.provenance[0]
    inner = retained.provenance[0]
    if (
        outer.source_id != inner.source_id
        or outer.start > inner.start
        or outer.end < inner.end
        or candidate.text.strip() != outer.quote.strip()
        or retained.text.strip() != inner.quote.strip()
    ):
        return False
    relative_start = inner.start - outer.start
    relative_end = relative_start + len(inner.quote)
    if outer.quote[relative_start:relative_end] != inner.quote:
        return False
    prefix = outer.quote[:relative_start]
    suffix = outer.quote[relative_end:]
    return (
        _LABELED_WRAPPER_PREFIX.fullmatch(prefix) is not None
        and not suffix.strip()
    )


@dataclass(frozen=True, slots=True)
class VerificationIssue:
    code: str
    severity: IssueSeverity
    message: str
    item_id: str | None = None
    source_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "item_id": self.item_id,
            "source_id": self.source_id,
        }


@dataclass(frozen=True, slots=True)
class VerificationReport:
    passed: bool
    issues: list[VerificationIssue] = field(default_factory=list)
    protected_candidates: int = 0
    protected_retained: int = 0
    provenance_valid: int = 0
    provenance_total: int = 0
    recovered_items: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "issues", _FrozenList(self.issues))

    @property
    def protected_recall(self) -> float:
        if not self.protected_candidates:
            return 1.0
        return self.protected_retained / self.protected_candidates

    @property
    def provenance_validity(self) -> float:
        if not self.provenance_total:
            return 1.0
        return self.provenance_valid / self.provenance_total

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "protected_candidates": self.protected_candidates,
            "protected_retained": self.protected_retained,
            "protected_recall": self.protected_recall,
            "provenance_valid": self.provenance_valid,
            "provenance_total": self.provenance_total,
            "provenance_validity": self.provenance_validity,
            "recovered_items": self.recovered_items,
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True, slots=True)
class CompilationPolicy:
    """Safety and budget policy.

    Protected semantic commitments are never removed to make the output fit.
    If they exceed ``token_budget``, compilation succeeds with an explicit
    overflow signal unless ``fail_on_budget_overflow`` is enabled.
    """

    token_budget: int = 4_000
    minimum_compression_ratio: float = 5.0
    fail_on_budget_overflow: bool = False
    include_superseded: bool = False
    include_discarded: bool = True
    verify: bool = True
    recover_missed_protected: bool = True
    fail_on_primary_extractor_error: bool = False
    chars_per_token: float = 4.0

    def __post_init__(self) -> None:
        for name in (
            "fail_on_budget_overflow",
            "include_superseded",
            "include_discarded",
            "verify",
            "recover_missed_protected",
            "fail_on_primary_extractor_error",
        ):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a boolean")
        if isinstance(self.token_budget, bool) or not isinstance(self.token_budget, int):
            raise TypeError("token_budget must be an integer")
        if self.token_budget <= 0:
            raise ValueError("token_budget must be positive")
        if isinstance(self.minimum_compression_ratio, bool) or not isinstance(
            self.minimum_compression_ratio, (int, float)
        ):
            raise TypeError("minimum_compression_ratio must be numeric")
        if not math.isfinite(float(self.minimum_compression_ratio)):
            raise ValueError("minimum_compression_ratio must be finite")
        if self.minimum_compression_ratio < 1:
            raise ValueError("minimum_compression_ratio must be at least 1")
        if isinstance(self.chars_per_token, bool) or not isinstance(
            self.chars_per_token, (int, float)
        ):
            raise TypeError("chars_per_token must be numeric")
        if not math.isfinite(float(self.chars_per_token)):
            raise ValueError("chars_per_token must be finite")
        if self.chars_per_token <= 0:
            raise ValueError("chars_per_token must be positive")


@dataclass(frozen=True, slots=True)
class CompressionStats:
    source_chars: int
    active_chars: int
    source_tokens_estimate: int
    active_tokens_estimate: int
    compression_ratio: float
    token_budget: int
    budget_overflow: int
    target_met: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_chars": self.source_chars,
            "active_chars": self.active_chars,
            "source_tokens_estimate": self.source_tokens_estimate,
            "active_tokens_estimate": self.active_tokens_estimate,
            "compression_ratio": self.compression_ratio,
            "token_budget": self.token_budget,
            "budget_overflow": self.budget_overflow,
            "target_met": self.target_met,
        }


@dataclass(frozen=True, slots=True)
class CompilationMetrics:
    """Bounded operational telemetry for one completed compilation."""

    source_records: int
    primary_extracted_items: int
    recovery_candidate_items: int
    certification_candidate_items: int
    recovery_added_items: int
    resolved_items: int
    selected_items: int
    active_items: int
    superseded_items: int
    discarded_items: int
    conflicting_items: int
    detected_conflicts: int
    protected_items: int
    protected_selected_items: int
    protected_prompt_tokens: int
    protected_budget_overflow: int
    verification_error_count: int
    verification_warning_count: int
    verification_info_count: int
    compile_duration_seconds: float

    def __post_init__(self) -> None:
        integer_fields = (
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
        )
        for name in integer_fields:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
        duration = self.compile_duration_seconds
        try:
            finite_duration = (
                not isinstance(duration, bool)
                and isinstance(duration, (int, float))
                and math.isfinite(float(duration))
                and duration >= 0
            )
        except OverflowError:
            finite_duration = False
        if not finite_duration:
            raise ValueError(
                "compile_duration_seconds must be finite and non-negative"
            )
        if (
            self.active_items
            + self.superseded_items
            + self.discarded_items
            + self.conflicting_items
            != self.resolved_items
        ):
            raise ValueError("status item counts must sum to resolved_items")
        if self.selected_items > self.resolved_items:
            raise ValueError("selected_items cannot exceed resolved_items")
        if self.recovery_added_items > self.resolved_items:
            raise ValueError("recovery_added_items cannot exceed resolved_items")
        if self.certification_candidate_items > self.recovery_candidate_items:
            raise ValueError(
                "certification_candidate_items cannot exceed "
                "recovery_candidate_items"
            )
        if self.protected_items > self.resolved_items:
            raise ValueError("protected_items cannot exceed resolved_items")
        if self.protected_selected_items > min(
            self.protected_items,
            self.selected_items,
        ):
            raise ValueError(
                "protected_selected_items exceeds protected or selected items"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": COMPILATION_METRICS_SCHEMA,
            "source_records": self.source_records,
            "primary_extracted_items": self.primary_extracted_items,
            "recovery_candidate_items": self.recovery_candidate_items,
            "certification_candidate_items": self.certification_candidate_items,
            "recovery_added_items": self.recovery_added_items,
            "resolved_items": self.resolved_items,
            "selected_items": self.selected_items,
            "active_items": self.active_items,
            "superseded_items": self.superseded_items,
            "discarded_items": self.discarded_items,
            "conflicting_items": self.conflicting_items,
            "detected_conflicts": self.detected_conflicts,
            "protected_items": self.protected_items,
            "protected_selected_items": self.protected_selected_items,
            "protected_prompt_tokens": self.protected_prompt_tokens,
            "protected_budget_overflow": self.protected_budget_overflow,
            "verification_error_count": self.verification_error_count,
            "verification_warning_count": self.verification_warning_count,
            "verification_info_count": self.verification_info_count,
            "compile_duration_seconds": round(
                float(self.compile_duration_seconds),
                6,
            ),
        }


@dataclass(slots=True)
class CompiledMemory:
    schema_version: str
    compiled_at: str
    source_digest: str
    source_count: int
    items: list[MemoryItem]
    selected_item_ids: list[str]
    verification: VerificationReport
    compression: CompressionStats
    compiler_metadata: dict[str, Any] = field(default_factory=dict)
    _snapshot_sha256: str = field(default="", init=False, repr=False, compare=False)
    _sealed: bool = field(default=False, init=False, repr=False, compare=False)

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_sealed", False):
            raise TypeError("compiled memory is immutable")
        object.__setattr__(self, name, value)

    def _snapshot_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "compiled_at": self.compiled_at,
            "source_digest": self.source_digest,
            "source_count": self.source_count,
            "items": [item.to_dict() for item in self.items],
            "selected_item_ids": list(self.selected_item_ids),
            "verification": self.verification.to_dict(),
            "compression": self.compression.to_dict(),
            "compiler_metadata": _thaw_json(self.compiler_metadata),
        }

    def _compute_snapshot_sha256(self) -> str:
        canonical = json.dumps(
            self._snapshot_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _assert_snapshot_integrity(self) -> None:
        if self._sealed and self._compute_snapshot_sha256() != self._snapshot_sha256:
            raise ValueError("compiled memory changed after verification")

    def seal(self) -> CompiledMemory:
        """Freeze every verified field and bind renderers to the final snapshot."""

        if self._sealed:
            self._assert_snapshot_integrity()
            return self
        object.__setattr__(
            self,
            "items",
            _FrozenList(item.seal() for item in self.items),
        )
        object.__setattr__(
            self,
            "selected_item_ids",
            _FrozenList(self.selected_item_ids),
        )
        object.__setattr__(
            self,
            "compiler_metadata",
            _freeze_json(self.compiler_metadata),
        )
        object.__setattr__(self, "_snapshot_sha256", self._compute_snapshot_sha256())
        object.__setattr__(self, "_sealed", True)
        return self

    @property
    def active_items(self) -> list[MemoryItem]:
        self._assert_snapshot_integrity()
        selected = set(self.selected_item_ids)
        return [item for item in self.items if item.id in selected]

    def by_kind(self, *, selected_only: bool = True) -> dict[MemoryKind, list[MemoryItem]]:
        self._assert_snapshot_integrity()
        grouped: dict[MemoryKind, list[MemoryItem]] = {kind: [] for kind in MemoryKind}
        items = self.active_items if selected_only else self.items
        for item in items:
            grouped[item.kind].append(item)
        return {kind: values for kind, values in grouped.items() if values}

    def to_dict(self, *, include_all_items: bool = True) -> dict[str, Any]:
        self._assert_snapshot_integrity()
        chosen = self.items if include_all_items else self.active_items
        payload = {
            "schema_version": self.schema_version,
            "ledger_complete": include_all_items,
            "compiled_at": self.compiled_at,
            "source_digest": self.source_digest,
            "source_count": self.source_count,
            "items": [item.to_dict() for item in chosen],
            "selected_item_ids": list(self.selected_item_ids),
            "verification": self.verification.to_dict(),
            "compression": self.compression.to_dict(),
            "compiler_metadata": _thaw_json(self.compiler_metadata),
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        payload["artifact_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return payload

    def to_json(self, *, indent: int = 2, include_all_items: bool = True) -> str:
        return json.dumps(
            self.to_dict(include_all_items=include_all_items),
            indent=indent,
            ensure_ascii=False,
            sort_keys=False,
        )

    def to_prompt(self, *, allow_unverified: bool = False) -> str:
        """Render typed JSON lines with inline, hash-anchored provenance.

        JSON encoding plus angle-bracket escaping prevents source text from
        terminating the outer memory envelope. Consumers must still treat all
        item text as historical data rather than executable instructions.
        """

        if not self.verification.passed and not allow_unverified:
            raise ValueError("refusing to render active context from unverified memory")

        self._assert_snapshot_integrity()
        return render_typed_memory(self.items, self.selected_item_ids)


def render_prompt_item(item: MemoryItem) -> str:
    refs = [
        f"{span.source_id}:{span.start}-{span.end}#{span.quote_sha256[:10]}"
        for span in item.provenance
    ]
    raw_roles = str(item.metadata.get("source_role", "unknown")).split(",")
    payload = {
        "text": item.text,
        "status": item.status.value,
        "exact": item.exact,
        "source_roles": sorted({role for role in raw_roles if role}),
        "provenance": refs,
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    encoded = (
        encoded.replace("\u0085", "\\u0085")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return f"- {encoded}"


def render_typed_memory(
    items: Iterable[MemoryItem],
    selected_item_ids: Iterable[str],
) -> str:
    """Render the canonical active-context envelope without trusting reports."""

    selected = set(selected_item_ids)
    grouped: dict[MemoryKind, list[MemoryItem]] = {kind: [] for kind in MemoryKind}
    for item in items:
        if item.id in selected:
            grouped[item.kind].append(item)
    lines = ['<typed_memory schema="1.0" content="untrusted-jsonl">']
    for kind, kind_items in grouped.items():
        if not kind_items:
            continue
        lines.append(f"{kind.value}:")
        lines.extend(render_prompt_item(item) for item in kind_items)
    lines.append("</typed_memory>")
    return "\n".join(lines)


def source_digest(sources: Iterable[SourceRecord]) -> str:
    ordered_sources = sorted(sources, key=lambda value: value.sequence)
    for source in ordered_sources:
        source.ensure_integrity()
    canonical = json.dumps(
        [
            [source.sequence, source.id, source.record_sha256]
            for source in ordered_sources
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
