"""Optional, length-preserving preprocessing for common content secrets.

Redaction happens before compilation. The returned sources are new immutable
records whose provenance offsets refer to redacted content. Source ids,
timestamps, roles, and metadata are deliberately outside this first scope.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .models import MAX_SOURCE_ID_CHARS, SourceRecord, source_digest

REDACTION_REPORT_SCHEMA = "ctxc-redaction-report-0.1"
REDACTION_VERIFICATION_SCHEMA = "ctxc-redaction-verification-0.1"

_MASK_CHARACTERS = frozenset({"*", "#", "█", "■"})
_LINE_BOUNDARY_CHARACTERS = frozenset(
    "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_REPORT_SCOPE = {
    "content_redacted": True,
    "metadata_redacted": False,
    "source_ids_redacted": False,
    "timestamps_redacted": False,
    "length_preserving": True,
    "line_boundaries_preserved": True,
    "original_content_secret_text_included": False,
    "original_content_secret_hashes_included": False,
}


@dataclass(frozen=True, slots=True)
class _Detector:
    name: str
    pattern: re.Pattern[str] | None
    groups: tuple[str, ...] = ("secret",)
    minimum_chars: int = 1

    def secret_span(self, match: re.Match[str]) -> tuple[int, int] | None:
        for group in self.groups:
            value = match.groupdict().get(group)
            if value is None:
                continue
            start, end = match.span(group)
            if end - start >= self.minimum_chars:
                return start, end
        return None


_DETECTORS = (
    _Detector(
        "pem_private_key",
        None,
    ),
    _Detector(
        "openai_style_key",
        re.compile(
            r"(?<![A-Za-z0-9_-])"
            r"(?P<secret>sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,})"
            r"(?![A-Za-z0-9_-])"
        ),
    ),
    _Detector(
        "github_token",
        re.compile(
            r"(?<![A-Za-z0-9_])"
            r"(?P<secret>gh[pousr]_[A-Za-z0-9]{20,})"
            r"(?![A-Za-z0-9_])"
        ),
    ),
    _Detector(
        "aws_access_key_id",
        re.compile(
            r"(?<![A-Z0-9])(?P<secret>(?:AKIA|ASIA)[A-Z0-9]{16})(?![A-Z0-9])"
        ),
    ),
    _Detector(
        "jwt",
        re.compile(
            r"(?<![A-Za-z0-9_-])"
            r"(?P<secret>eyJ[A-Za-z0-9_-]{7,}\."
            r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})"
            r"(?![A-Za-z0-9_-])"
        ),
    ),
    _Detector(
        "bearer_token",
        re.compile(
            r"\bBearer[ \t]+"
            r"(?P<secret>[A-Za-z0-9._~+/-]{12,}={0,2})",
            re.IGNORECASE,
        ),
    ),
    _Detector(
        "basic_authorization",
        re.compile(
            r"\bBasic[ \t]+(?P<secret>[A-Za-z0-9+/]{12,}={0,2})",
            re.IGNORECASE,
        ),
    ),
    _Detector(
        "url_userinfo",
        re.compile(
            r"(?<=://)(?P<secret>[^/\s:@]{1,128}:[^/\s@]{1,256})(?=@)"
        ),
        minimum_chars=3,
    ),
    _Detector(
        "credential_assignment",
        re.compile(
            r"(?:[\"']?)\b(?:"
            r"api[_-]?key|access[_-]?key(?:[_-]?id)?|"
            r"access[_-]?token|auth[_-]?token|password|passwd|"
            r"client[_-]?secret|private[_-]?key"
            r")\b(?:[\"']?)[ \t]*[:=][ \t]*"
            r"(?:"
            r"\"(?P<double_quoted>[^\"\r\n]{6,})\"|"
            r"'(?P<single_quoted>[^'\r\n]{6,})'|"
            r"(?P<bare>[^\s,;#\"']{6,})"
            r")",
            re.IGNORECASE,
        ),
        groups=("double_quoted", "single_quoted", "bare"),
        minimum_chars=6,
    ),
)

SECRET_DETECTOR_NAMES = tuple(detector.name for detector in _DETECTORS)
_DETECTOR_BY_NAME = {detector.name: detector for detector in _DETECTORS}
_DETECTOR_PRIORITY = {
    detector.name: index for index, detector in enumerate(_DETECTORS)
}
_PEM_BOUNDARY = re.compile(
    r"-----(?P<kind>BEGIN|END) "
    r"(?P<label>(?:RSA |EC |OPENSSH )?PRIVATE KEY)-----"
)


class RedactionError(ValueError):
    """Secret redaction input or evidence is invalid."""


class RedactionLimitError(RedactionError):
    """Secret redaction exceeded an explicit resource limit."""


@dataclass(frozen=True, slots=True)
class RedactionPolicy:
    """Fixed-detector and resource policy for content redaction."""

    enabled_detectors: tuple[str, ...] = SECRET_DETECTOR_NAMES
    mask_character: str = "*"
    max_sources: int = 100_000
    max_findings_per_source: int = 1_000
    max_total_findings: int = 10_000
    max_source_chars: int = 2_000_000
    max_total_chars: int = 16_000_000

    def __post_init__(self) -> None:
        enabled = self.enabled_detectors
        if not isinstance(enabled, (tuple, list)):
            raise TypeError("enabled_detectors must be a sequence of names")
        if not enabled or not all(
            isinstance(name, str) and name
            for name in enabled
        ):
            raise ValueError(
                "enabled_detectors must contain non-empty strings"
            )
        if len(enabled) != len(set(enabled)):
            raise ValueError("enabled_detectors cannot contain duplicates")
        unknown = sorted(set(enabled) - set(SECRET_DETECTOR_NAMES))
        if unknown:
            raise ValueError(
                "unknown secret detectors: " + ", ".join(unknown)
            )
        canonical = tuple(
            name for name in SECRET_DETECTOR_NAMES if name in set(enabled)
        )
        object.__setattr__(self, "enabled_detectors", canonical)
        if (
            not isinstance(self.mask_character, str)
            or self.mask_character not in _MASK_CHARACTERS
        ):
            raise ValueError(
                "mask_character must be '*', '#', U+2588, or U+25A0"
            )
        for name in (
            "max_sources",
            "max_findings_per_source",
            "max_total_findings",
            "max_source_chars",
            "max_total_chars",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.max_total_findings < self.max_findings_per_source:
            raise ValueError(
                "max_total_findings cannot be smaller than "
                "max_findings_per_source"
            )
        if self.max_total_chars < self.max_source_chars:
            raise ValueError(
                "max_total_chars cannot be smaller than max_source_chars"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled_detectors": list(self.enabled_detectors),
            "mask_character": self.mask_character,
            "max_sources": self.max_sources,
            "max_findings_per_source": self.max_findings_per_source,
            "max_total_findings": self.max_total_findings,
            "max_source_chars": self.max_source_chars,
            "max_total_chars": self.max_total_chars,
        }


@dataclass(frozen=True, slots=True, order=True)
class RedactionFinding:
    """One secret span without the original text or its digest."""

    source_sequence: int
    source_id: str
    start: int
    end: int
    detector: str
    redacted_characters: int
    line_boundaries_preserved: int

    def __post_init__(self) -> None:
        integer_fields = (
            "source_sequence",
            "start",
            "end",
            "redacted_characters",
            "line_boundaries_preserved",
        )
        for name in integer_fields:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"redaction finding {name} must be an integer")
        if self.source_sequence < 0:
            raise ValueError(
                "redaction finding source_sequence cannot be negative"
            )
        if not isinstance(self.source_id, str) or not self.source_id:
            raise TypeError(
                "redaction finding source_id must be a non-empty string"
            )
        if len(self.source_id) > MAX_SOURCE_ID_CHARS:
            raise ValueError(
                f"redaction finding source_id exceeds {MAX_SOURCE_ID_CHARS} characters"
            )
        if self.detector not in SECRET_DETECTOR_NAMES:
            raise ValueError("redaction finding detector is unknown")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("redaction finding span is invalid")
        if self.redacted_characters <= 0:
            raise ValueError(
                "redaction finding must mask at least one character"
            )
        if self.line_boundaries_preserved < 0:
            raise ValueError(
                "redaction finding line boundary count cannot be negative"
            )
        if (
            self.redacted_characters + self.line_boundaries_preserved
            != self.end - self.start
        ):
            raise ValueError(
                "redaction finding counts must cover its exact span"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_sequence": self.source_sequence,
            "source_id": self.source_id,
            "start": self.start,
            "end": self.end,
            "detector": self.detector,
            "redacted_characters": self.redacted_characters,
            "line_boundaries_preserved": self.line_boundaries_preserved,
        }


@dataclass(frozen=True, slots=True)
class RedactionResult:
    """Redacted immutable sources and non-secret audit evidence."""

    sources: tuple[SourceRecord, ...]
    findings: tuple[RedactionFinding, ...]
    policy: RedactionPolicy
    _report_json: str = field(repr=False)

    def __post_init__(self) -> None:
        sources = tuple(self.sources)
        findings = tuple(self.findings)
        if not all(isinstance(source, SourceRecord) for source in sources):
            raise TypeError("redaction result sources must be SourceRecord values")
        if not all(
            isinstance(finding, RedactionFinding) for finding in findings
        ):
            raise TypeError(
                "redaction result findings must be RedactionFinding values"
            )
        if not isinstance(self.policy, RedactionPolicy):
            raise TypeError("redaction result policy must be RedactionPolicy")
        if not isinstance(self._report_json, str):
            raise TypeError("redaction result report must be serialized JSON")
        if sources != tuple(sorted(sources, key=lambda source: source.sequence)):
            raise RedactionError(
                "redaction result sources must be sorted by sequence"
            )
        source_ids = [source.id for source in sources]
        source_sequences = [source.sequence for source in sources]
        if len(source_ids) != len(set(source_ids)):
            raise RedactionError("redaction result source ids must be unique")
        if len(source_sequences) != len(set(source_sequences)):
            raise RedactionError(
                "redaction result source sequences must be unique"
            )
        for source in sources:
            source.ensure_integrity()
        if findings != tuple(sorted(findings)):
            raise RedactionError("redaction result findings must be sorted")
        source_by_identity = {
            (source.sequence, source.id): source for source in sources
        }
        previous_end: dict[tuple[int, str], int] = {}
        for finding in findings:
            identity = (finding.source_sequence, finding.source_id)
            source = source_by_identity.get(identity)
            if source is None:
                raise RedactionError(
                    "redaction finding references an unknown source"
                )
            if (
                finding.start < previous_end.get(identity, 0)
                or finding.end > len(source.content)
            ):
                raise RedactionError(
                    "redaction finding span is invalid or overlapping"
                )
            span = source.content[finding.start:finding.end]
            redacted_characters = sum(
                character == self.policy.mask_character
                for character in span
            )
            line_boundaries = sum(
                character in _LINE_BOUNDARY_CHARACTERS
                for character in span
            )
            if (
                redacted_characters != finding.redacted_characters
                or line_boundaries != finding.line_boundaries_preserved
                or redacted_characters + line_boundaries != len(span)
            ):
                raise RedactionError(
                    "redaction finding does not match masked source content"
                )
            previous_end[identity] = finding.end

        try:
            report_value = json.loads(self._report_json)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise RedactionError(
                "redaction result report is invalid JSON"
            ) from exc
        report = verify_redaction_report_hash(report_value)
        if report["policy"] != self.policy.to_dict():
            raise RedactionError("redaction result policy does not match report")
        if report["source_count"] != len(sources):
            raise RedactionError(
                "redaction result source count does not match report"
            )
        if report["findings"] != [
            finding.to_dict() for finding in findings
        ]:
            raise RedactionError(
                "redaction result findings do not match report"
            )
        if report["redacted_source_digest"] != source_digest(sources):
            raise RedactionError(
                "redaction result source digest does not match report"
            )
        object.__setattr__(self, "sources", sources)
        object.__setattr__(self, "findings", findings)
        object.__setattr__(
            self,
            "_report_json",
            _canonical_json(report),
        )

    def to_report(self) -> dict[str, Any]:
        """Return a detached JSON-compatible report."""

        return json.loads(self._report_json)


@dataclass(frozen=True, slots=True, order=True)
class _Match:
    start: int
    end: int
    detector: str


def _pem_secret_spans(content: str) -> Iterable[tuple[int, int]]:
    """Pair private-key boundaries in one linear regex scan."""

    openers: dict[str, int] = {}
    for boundary in _PEM_BOUNDARY.finditer(content):
        label = boundary.group("label")
        if boundary.group("kind") == "BEGIN":
            openers.setdefault(label, boundary.start())
            continue
        start = openers.pop(label, None)
        if start is not None:
            yield start, boundary.end()


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise RedactionError(
            "redaction evidence is not canonical finite JSON"
        ) from exc


def _source_matches(
    source: SourceRecord,
    policy: RedactionPolicy,
) -> list[_Match]:
    candidates: list[_Match] = []
    candidate_limit = (
        policy.max_findings_per_source * len(policy.enabled_detectors)
    )
    for detector_name in policy.enabled_detectors:
        detector = _DETECTOR_BY_NAME[detector_name]
        if detector.name == "pem_private_key":
            spans = _pem_secret_spans(source.content)
        else:
            if detector.pattern is None:
                raise RuntimeError("secret detector pattern is unavailable")
            spans = (
                span
                for match in detector.pattern.finditer(source.content)
                if (span := detector.secret_span(match)) is not None
            )
        for span in spans:
            candidates.append(
                _Match(
                    start=span[0],
                    end=span[1],
                    detector=detector.name,
                )
            )
            if len(candidates) > candidate_limit:
                raise RedactionLimitError(
                    f"secret detector candidates exceed the per-source "
                    f"limit for source {source.id!r}"
                )
    candidates.sort(
        key=lambda match: (
            match.start,
            -(match.end - match.start),
            _DETECTOR_PRIORITY[match.detector],
            match.detector,
        )
    )
    selected: list[_Match] = []
    for match in candidates:
        if selected and match.start < selected[-1].end:
            continue
        selected.append(match)
        if len(selected) > policy.max_findings_per_source:
            raise RedactionLimitError(
                f"redaction findings exceed the per-source limit for "
                f"source {source.id!r}"
            )
    return selected


def _mask_source(
    source: SourceRecord,
    matches: list[_Match],
    policy: RedactionPolicy,
) -> tuple[SourceRecord, list[RedactionFinding]]:
    if not matches:
        return source, []
    characters = list(source.content)
    findings: list[RedactionFinding] = []
    for match in matches:
        redacted_characters = 0
        line_boundaries = 0
        for index in range(match.start, match.end):
            if characters[index] in _LINE_BOUNDARY_CHARACTERS:
                line_boundaries += 1
                continue
            characters[index] = policy.mask_character
            redacted_characters += 1
        findings.append(
            RedactionFinding(
                source_sequence=source.sequence,
                source_id=source.id,
                start=match.start,
                end=match.end,
                detector=match.detector,
                redacted_characters=redacted_characters,
                line_boundaries_preserved=line_boundaries,
            )
        )
    redacted_content = "".join(characters)
    if len(redacted_content) != len(source.content):
        raise RuntimeError("length-preserving redaction changed source length")
    redacted = SourceRecord.create(
        id=source.id,
        sequence=source.sequence,
        role=source.role,
        content=redacted_content,
        timestamp=source.timestamp,
        metadata=source.to_dict()["metadata"],
    )
    return redacted, findings


def _report(
    sources: tuple[SourceRecord, ...],
    findings: tuple[RedactionFinding, ...],
    policy: RedactionPolicy,
) -> dict[str, Any]:
    detector_counts = Counter(finding.detector for finding in findings)
    report: dict[str, Any] = {
        "schema": REDACTION_REPORT_SCHEMA,
        "scope": dict(_REPORT_SCOPE),
        "policy": policy.to_dict(),
        "source_count": len(sources),
        "sources_changed": len(
            {finding.source_id for finding in findings}
        ),
        "finding_count": len(findings),
        "detector_counts": {
            name: detector_counts[name]
            for name in SECRET_DETECTOR_NAMES
            if detector_counts[name]
        },
        "redacted_source_digest": source_digest(list(sources)),
        "findings": [finding.to_dict() for finding in findings],
    }
    report["report_sha256"] = _canonical_sha256(report)
    return report


def redact_sources(
    sources: Iterable[SourceRecord],
    *,
    policy: RedactionPolicy | None = None,
) -> RedactionResult:
    """Return sorted, immutable, length-preserving redacted sources."""

    resolved_policy = policy if policy is not None else RedactionPolicy()
    if not isinstance(resolved_policy, RedactionPolicy):
        raise TypeError("policy must be a RedactionPolicy value")
    prepared: list[SourceRecord] = []
    ids: set[str] = set()
    sequences: set[int] = set()
    total_chars = 0
    for index, source in enumerate(sources):
        if index >= resolved_policy.max_sources:
            raise RedactionLimitError(
                "redaction sources exceed max_sources"
            )
        if not isinstance(source, SourceRecord):
            raise TypeError("redaction sources must be SourceRecord values")
        if source.id in ids:
            raise RedactionError("redaction source ids must be unique")
        if source.sequence in sequences:
            raise RedactionError("redaction source sequences must be unique")
        source.ensure_integrity()
        source_chars = len(source.content)
        if source_chars > resolved_policy.max_source_chars:
            raise RedactionLimitError(
                f"source {source.id!r} exceeds max_source_chars"
            )
        total_chars += source_chars
        if total_chars > resolved_policy.max_total_chars:
            raise RedactionLimitError(
                "redaction sources exceed max_total_chars"
            )
        ids.add(source.id)
        sequences.add(source.sequence)
        prepared.append(source)
    prepared.sort(key=lambda source: source.sequence)

    output: list[SourceRecord] = []
    findings: list[RedactionFinding] = []
    for source in prepared:
        redacted, source_findings = _mask_source(
            source,
            _source_matches(source, resolved_policy),
            resolved_policy,
        )
        output.append(redacted)
        findings.extend(source_findings)
        if len(findings) > resolved_policy.max_total_findings:
            raise RedactionLimitError(
                "redaction findings exceed max_total_findings"
            )

    output_tuple = tuple(output)
    findings_tuple = tuple(sorted(findings))
    return RedactionResult(
        sources=output_tuple,
        findings=findings_tuple,
        policy=resolved_policy,
        _report_json=_canonical_json(
            _report(
                output_tuple,
                findings_tuple,
                resolved_policy,
            )
        ),
    )


def verify_redaction_report_hash(document: Any) -> dict[str, Any]:
    """Verify the exact redaction-report shape and canonical self-hash."""

    if not isinstance(document, dict):
        raise RedactionError("redaction report must be an object")
    fields = {
        "schema",
        "scope",
        "policy",
        "source_count",
        "sources_changed",
        "finding_count",
        "detector_counts",
        "redacted_source_digest",
        "findings",
        "report_sha256",
    }
    if set(document) != fields:
        raise RedactionError("redaction report fields are invalid")
    if document["schema"] != REDACTION_REPORT_SCHEMA:
        raise RedactionError("redaction report schema is unsupported")
    if document["scope"] != _REPORT_SCOPE:
        raise RedactionError("redaction report scope is invalid")

    raw_policy = document["policy"]
    policy_fields = {
        "enabled_detectors",
        "mask_character",
        "max_sources",
        "max_findings_per_source",
        "max_total_findings",
        "max_source_chars",
        "max_total_chars",
    }
    if not isinstance(raw_policy, dict) or set(raw_policy) != policy_fields:
        raise RedactionError("redaction report policy fields are invalid")
    try:
        policy = RedactionPolicy(**raw_policy)
    except (TypeError, ValueError) as exc:
        raise RedactionError("redaction report policy is invalid") from exc
    if policy.to_dict() != raw_policy:
        raise RedactionError("redaction report policy is not canonical")

    def require_count(name: str) -> int:
        value = document[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RedactionError(
                f"redaction report {name} must be a non-negative integer"
            )
        return value

    source_count = require_count("source_count")
    sources_changed = require_count("sources_changed")
    finding_count = require_count("finding_count")
    if sources_changed > source_count:
        raise RedactionError(
            "redaction report sources_changed exceeds source_count"
        )
    if source_count > policy.max_sources:
        raise RedactionError(
            "redaction report source_count exceeds its policy"
        )
    if finding_count > policy.max_total_findings:
        raise RedactionError(
            "redaction report finding_count exceeds its policy"
        )

    digest = document["redacted_source_digest"]
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        raise RedactionError(
            "redaction report source digest must be lowercase SHA-256"
        )

    raw_findings = document["findings"]
    if not isinstance(raw_findings, list):
        raise RedactionError("redaction report findings must be a list")
    if len(raw_findings) != finding_count:
        raise RedactionError(
            "redaction report finding_count does not match findings"
        )
    finding_fields = {
        "source_sequence",
        "source_id",
        "start",
        "end",
        "detector",
        "redacted_characters",
        "line_boundaries_preserved",
    }
    findings: list[RedactionFinding] = []
    source_id_to_sequence: dict[str, int] = {}
    sequence_to_source_id: dict[int, str] = {}
    per_source = Counter[tuple[int, str]]()
    for raw_finding in raw_findings:
        if (
            not isinstance(raw_finding, dict)
            or set(raw_finding) != finding_fields
        ):
            raise RedactionError("redaction report finding fields are invalid")
        integer_fields = (
            "source_sequence",
            "start",
            "end",
            "redacted_characters",
            "line_boundaries_preserved",
        )
        if any(
            isinstance(raw_finding[name], bool)
            or not isinstance(raw_finding[name], int)
            for name in integer_fields
        ):
            raise RedactionError(
                "redaction report finding coordinates must be integers"
            )
        source_sequence = raw_finding["source_sequence"]
        source_id = raw_finding["source_id"]
        start = raw_finding["start"]
        end = raw_finding["end"]
        detector = raw_finding["detector"]
        redacted_characters = raw_finding["redacted_characters"]
        line_boundaries = raw_finding["line_boundaries_preserved"]
        if source_sequence < 0:
            raise RedactionError(
                "redaction report source sequence cannot be negative"
            )
        if not isinstance(source_id, str) or not source_id:
            raise RedactionError(
                "redaction report source id must be a non-empty string"
            )
        if len(source_id) > MAX_SOURCE_ID_CHARS:
            raise RedactionError(
                "redaction report source id exceeds "
                f"{MAX_SOURCE_ID_CHARS} characters"
            )
        if detector not in policy.enabled_detectors:
            raise RedactionError(
                "redaction report finding uses a disabled detector"
            )
        if (
            start < 0
            or end <= start
            or end > policy.max_source_chars
            or redacted_characters <= 0
            or line_boundaries < 0
            or redacted_characters + line_boundaries != end - start
        ):
            raise RedactionError(
                "redaction report finding span or counts are invalid"
            )
        if (
            source_id in source_id_to_sequence
            and source_id_to_sequence[source_id] != source_sequence
        ):
            raise RedactionError(
                "redaction report source id maps to multiple sequences"
            )
        if (
            source_sequence in sequence_to_source_id
            and sequence_to_source_id[source_sequence] != source_id
        ):
            raise RedactionError(
                "redaction report source sequence maps to multiple ids"
            )
        source_id_to_sequence[source_id] = source_sequence
        sequence_to_source_id[source_sequence] = source_id
        finding = RedactionFinding(
            source_sequence=source_sequence,
            source_id=source_id,
            start=start,
            end=end,
            detector=detector,
            redacted_characters=redacted_characters,
            line_boundaries_preserved=line_boundaries,
        )
        findings.append(finding)
        identity = (source_sequence, source_id)
        per_source[identity] += 1
        if per_source[identity] > policy.max_findings_per_source:
            raise RedactionError(
                "redaction report exceeds per-source finding policy"
            )
    if findings != sorted(findings):
        raise RedactionError("redaction report findings are not sorted")
    previous_end: dict[tuple[int, str], int] = {}
    for finding in findings:
        identity = (finding.source_sequence, finding.source_id)
        if finding.start < previous_end.get(identity, 0):
            raise RedactionError("redaction report findings overlap")
        previous_end[identity] = finding.end
    if len(per_source) != sources_changed:
        raise RedactionError(
            "redaction report sources_changed does not match findings"
        )

    raw_detector_counts = document["detector_counts"]
    if not isinstance(raw_detector_counts, dict) or any(
        not isinstance(name, str)
        or name not in policy.enabled_detectors
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count <= 0
        for name, count in raw_detector_counts.items()
    ):
        raise RedactionError("redaction report detector_counts is invalid")
    expected_detector_counts = Counter(
        finding.detector for finding in findings
    )
    if raw_detector_counts != dict(expected_detector_counts):
        raise RedactionError(
            "redaction report detector_counts does not match findings"
        )

    claimed = document["report_sha256"]
    if not isinstance(claimed, str) or _SHA256.fullmatch(claimed) is None:
        raise RedactionError(
            "redaction report_sha256 must be lowercase SHA-256"
        )
    unsigned = dict(document)
    unsigned.pop("report_sha256")
    if _canonical_sha256(unsigned) != claimed:
        raise RedactionError("redaction report SHA-256 mismatch")
    return json.loads(_canonical_json(document))


def verify_redaction_result(
    original_sources: Iterable[SourceRecord],
    result: RedactionResult,
) -> dict[str, Any]:
    """Rerun preprocessing and require exact source/finding/report equality."""

    if not isinstance(result, RedactionResult):
        raise TypeError("result must be a RedactionResult value")
    replay = redact_sources(original_sources, policy=result.policy)
    if replay.sources != result.sources:
        raise RedactionError("redacted sources do not match replay")
    if replay.findings != result.findings:
        raise RedactionError("redaction findings do not match replay")
    if replay.to_report() != result.to_report():
        raise RedactionError("redaction report does not match replay")
    report = verify_redaction_report_hash(result.to_report())
    return {
        "schema": REDACTION_VERIFICATION_SCHEMA,
        "verified": True,
        "source_count": len(result.sources),
        "finding_count": len(result.findings),
        "redacted_source_digest": report["redacted_source_digest"],
        "report_sha256": report["report_sha256"],
    }
