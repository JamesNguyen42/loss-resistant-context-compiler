"""Frozen exact-span evaluation for novel English extraction phrasing.

This diagnostic measures the default deterministic compiler without tuning the
production extractor against the corpus. A miss is evidence, not a harness
failure; malformed or non-replayable evidence is a harness failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from context_compiler import (
    CompilationPolicy,
    ContextCompiler,
    MemoryKind,
    SourceRecord,
    __version__,
)
from context_compiler.atomic import atomic_write_text

from .json_io import StrictJsonLimits, load_strict_json_file

PHRASE_CORPUS_SCHEMA = "ctxc-phrase-corpus-0.1"
PHRASE_REPORT_SCHEMA = "ctxc-phrase-eval-report-0.1"
PHRASE_VERIFICATION_SCHEMA = "ctxc-phrase-eval-verification-0.1"
DEFAULT_PHRASE_CORPUS = (
    Path(__file__).with_name("data") / "novel_english_phrases_v1.json"
)
DEFAULT_PHRASE_CORPUS_SHA256 = (
    "19a31bf953f7b8b46a39336d08a18e7011eb2b0d326b7dd4e0ce7947e7e15712"
)

_CORPUS_LIMITS = StrictJsonLimits(
    max_bytes=1024 * 1024,
    max_line_chars=32 * 1024,
    max_depth=12,
)
_REPORT_LIMITS = StrictJsonLimits(
    max_bytes=4 * 1024 * 1024,
    max_line_chars=128 * 1024,
    max_depth=20,
)
_MAX_CASES = 10_000
_MAX_CONTENT_CHARS = 100_000
_MAX_EXPECTED_PER_CASE = 32
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CASE_ID = re.compile(r"[a-z][a-z0-9_-]{0,127}")
_LINE_BREAK = re.compile(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")
_ALLOWED_ROLES = frozenset({"user", "system", "developer", "assistant", "tool"})
_EXPECTED_ROLES: Mapping[MemoryKind, frozenset[str]] = {
    MemoryKind.GOAL: frozenset({"user"}),
    MemoryKind.CONSTRAINT: frozenset({"user", "system", "developer"}),
    MemoryKind.UNRESOLVED: frozenset({"user", "assistant"}),
    MemoryKind.DECISION: frozenset({"user", "assistant"}),
    MemoryKind.CONFIRMED_FACT: frozenset({"user", "assistant"}),
    MemoryKind.DISCARDED_ATTEMPT: frozenset(
        {"user", "assistant", "tool"}
    ),
    MemoryKind.EXACT_ERROR: frozenset({"tool"}),
    MemoryKind.EXACT_REFERENCE: frozenset({"assistant"}),
}
_EXACT_KINDS = frozenset(
    {MemoryKind.EXACT_ERROR, MemoryKind.EXACT_REFERENCE}
)
_POLICY = CompilationPolicy(
    token_budget=100_000,
    minimum_compression_ratio=1.0,
)


class PhraseEvaluationError(ValueError):
    """Phrase corpus or report evidence is invalid."""


_READABLE_PACKAGE_VERSIONS = frozenset({"0.1.0", "0.1.1a1"})


def _validated_package_version(value: Any) -> str:
    if type(value) is not str or value not in _READABLE_PACKAGE_VERSIONS:
        raise PhraseEvaluationError(
            "phrase evaluation package version is unsupported"
        )
    return value


@dataclass(frozen=True, slots=True)
class PhraseAuthoring:
    """Frozen provenance and tuning policy for one phrase corpus."""

    draft_model: str
    quantization: str
    transport: str
    network_model_api: bool
    model_service_cost_usd: float
    annotation: str
    tuning_policy: str


@dataclass(frozen=True, slots=True)
class ExpectedPhraseAtom:
    """One exact-span gold atom in an isolated phrase case."""

    kind: MemoryKind
    text: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class PhraseCase:
    """One isolated source message and its exhaustive expected ledger."""

    id: str
    role: str
    content: str
    expected: tuple[ExpectedPhraseAtom, ...]


@dataclass(frozen=True, slots=True)
class PhraseCorpus:
    """Validated self-hashed phrase-evaluation corpus."""

    schema: str
    scope: str
    authoring: PhraseAuthoring
    cases: tuple[PhraseCase, ...]
    corpus_sha256: str


@dataclass(frozen=True, slots=True, order=True)
class _SpanKey:
    source_id: str
    start: int
    end: int
    quote: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "start": self.start,
            "end": self.end,
            "quote": self.quote,
        }


@dataclass(frozen=True, slots=True, order=True)
class _AtomKey:
    kind: str
    text: str
    provenance: tuple[_SpanKey, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "provenance": [
                span.to_dict() for span in self.provenance
            ],
        }


def _canonical_sha256(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise PhraseEvaluationError(
            "phrase evidence is not canonical finite JSON"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _object(
    value: Any,
    *,
    fields: frozenset[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PhraseEvaluationError(f"{label} must be an object")
    actual = set(value)
    if actual != fields:
        missing = sorted(fields - actual)
        unknown = sorted(actual - fields)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        raise PhraseEvaluationError(
            f"{label} fields are invalid: {'; '.join(details)}"
        )
    return value


def _nonempty_string(
    value: Any,
    *,
    label: str,
    max_chars: int = _MAX_CONTENT_CHARS,
) -> str:
    if not isinstance(value, str) or not value:
        raise PhraseEvaluationError(f"{label} must be a non-empty string")
    if len(value) > max_chars:
        raise PhraseEvaluationError(
            f"{label} exceeds {max_chars} characters"
        )
    return value


def _decode_authoring(value: Any) -> PhraseAuthoring:
    fields = frozenset(
        {
            "draft_model",
            "quantization",
            "transport",
            "network_model_api",
            "model_service_cost_usd",
            "annotation",
            "tuning_policy",
        }
    )
    raw = _object(value, fields=fields, label="phrase authoring")
    network_model_api = raw["network_model_api"]
    if not isinstance(network_model_api, bool):
        raise PhraseEvaluationError(
            "phrase authoring network_model_api must be a boolean"
        )
    cost = raw["model_service_cost_usd"]
    if (
        isinstance(cost, bool)
        or not isinstance(cost, (int, float))
        or not math.isfinite(cost)
        or cost < 0
    ):
        raise PhraseEvaluationError(
            "phrase authoring model_service_cost_usd must be finite and nonnegative"
        )
    return PhraseAuthoring(
        draft_model=_nonempty_string(
            raw["draft_model"],
            label="phrase authoring draft_model",
            max_chars=256,
        ),
        quantization=_nonempty_string(
            raw["quantization"],
            label="phrase authoring quantization",
            max_chars=64,
        ),
        transport=_nonempty_string(
            raw["transport"],
            label="phrase authoring transport",
            max_chars=128,
        ),
        network_model_api=network_model_api,
        model_service_cost_usd=float(cost),
        annotation=_nonempty_string(
            raw["annotation"],
            label="phrase authoring annotation",
            max_chars=2_000,
        ),
        tuning_policy=_nonempty_string(
            raw["tuning_policy"],
            label="phrase authoring tuning_policy",
            max_chars=2_000,
        ),
    )


def _decode_expected(
    value: Any,
    *,
    case_id: str,
    role: str,
    content: str,
) -> tuple[ExpectedPhraseAtom, ...]:
    if not isinstance(value, list):
        raise PhraseEvaluationError(
            f"phrase case {case_id!r} expected must be an array"
        )
    if len(value) > _MAX_EXPECTED_PER_CASE:
        raise PhraseEvaluationError(
            f"phrase case {case_id!r} has too many expected atoms"
        )
    expected: list[ExpectedPhraseAtom] = []
    seen: set[tuple[MemoryKind, str, int, int]] = set()
    fields = frozenset({"kind", "text"})
    for index, atom_value in enumerate(value):
        raw = _object(
            atom_value,
            fields=fields,
            label=f"phrase case {case_id!r} expected atom {index}",
        )
        kind_value = _nonempty_string(
            raw["kind"],
            label=f"phrase case {case_id!r} expected kind",
            max_chars=64,
        )
        try:
            kind = MemoryKind(kind_value)
        except ValueError as exc:
            raise PhraseEvaluationError(
                f"phrase case {case_id!r} has unknown expected kind {kind_value!r}"
            ) from exc
        allowed_roles = _EXPECTED_ROLES.get(kind)
        if allowed_roles is None:
            raise PhraseEvaluationError(
                f"phrase case {case_id!r} kind {kind.value!r} is outside "
                "the diagnostic scope"
            )
        if role not in allowed_roles:
            raise PhraseEvaluationError(
                f"phrase case {case_id!r} role {role!r} cannot author "
                f"expected kind {kind.value!r}"
            )
        text = _nonempty_string(
            raw["text"],
            label=f"phrase case {case_id!r} expected text",
        )
        if content.count(text) != 1:
            raise PhraseEvaluationError(
                f"phrase case {case_id!r} expected text must occur exactly once"
            )
        if kind not in _EXACT_KINDS and text != content:
            raise PhraseEvaluationError(
                f"phrase case {case_id!r} ordinary expected atom must equal "
                "the complete isolated message"
            )
        start = content.index(text)
        atom = ExpectedPhraseAtom(
            kind=kind,
            text=text,
            start=start,
            end=start + len(text),
        )
        key = (atom.kind, atom.text, atom.start, atom.end)
        if key in seen:
            raise PhraseEvaluationError(
                f"phrase case {case_id!r} repeats an expected atom"
            )
        seen.add(key)
        expected.append(atom)
    return tuple(expected)


def decode_phrase_corpus(document: Any) -> PhraseCorpus:
    """Validate and decode one self-hashed phrase corpus document."""

    fields = frozenset(
        {"schema", "scope", "authoring", "cases", "corpus_sha256"}
    )
    raw = _object(document, fields=fields, label="phrase corpus")
    if raw["schema"] != PHRASE_CORPUS_SCHEMA:
        raise PhraseEvaluationError("phrase corpus schema is unsupported")
    corpus_sha256 = raw["corpus_sha256"]
    if (
        not isinstance(corpus_sha256, str)
        or _SHA256.fullmatch(corpus_sha256) is None
    ):
        raise PhraseEvaluationError(
            "phrase corpus corpus_sha256 must be lowercase SHA-256"
        )
    unsigned = dict(raw)
    unsigned.pop("corpus_sha256")
    if _canonical_sha256(unsigned) != corpus_sha256:
        raise PhraseEvaluationError("phrase corpus SHA-256 mismatch")

    scope = _nonempty_string(
        raw["scope"],
        label="phrase corpus scope",
        max_chars=256,
    )
    authoring = _decode_authoring(raw["authoring"])
    case_values = raw["cases"]
    if not isinstance(case_values, list):
        raise PhraseEvaluationError("phrase corpus cases must be an array")
    if not case_values or len(case_values) > _MAX_CASES:
        raise PhraseEvaluationError(
            f"phrase corpus must contain 1..{_MAX_CASES} cases"
        )

    case_fields = frozenset({"id", "role", "content", "expected"})
    cases: list[PhraseCase] = []
    seen_ids: set[str] = set()
    for index, case_value in enumerate(case_values):
        case_raw = _object(
            case_value,
            fields=case_fields,
            label=f"phrase case {index}",
        )
        case_id = _nonempty_string(
            case_raw["id"],
            label=f"phrase case {index} id",
            max_chars=128,
        )
        if _CASE_ID.fullmatch(case_id) is None:
            raise PhraseEvaluationError(
                f"phrase case id is invalid: {case_id!r}"
            )
        if case_id in seen_ids:
            raise PhraseEvaluationError(
                f"duplicate phrase case id: {case_id!r}"
            )
        seen_ids.add(case_id)
        role = _nonempty_string(
            case_raw["role"],
            label=f"phrase case {case_id!r} role",
            max_chars=32,
        )
        if role not in _ALLOWED_ROLES:
            raise PhraseEvaluationError(
                f"phrase case {case_id!r} role is unsupported"
            )
        content = _nonempty_string(
            case_raw["content"],
            label=f"phrase case {case_id!r} content",
        )
        if _LINE_BREAK.search(content):
            raise PhraseEvaluationError(
                f"phrase case {case_id!r} must contain one physical line"
            )
        expected = _decode_expected(
            case_raw["expected"],
            case_id=case_id,
            role=role,
            content=content,
        )
        cases.append(
            PhraseCase(
                id=case_id,
                role=role,
                content=content,
                expected=expected,
            )
        )
    return PhraseCorpus(
        schema=PHRASE_CORPUS_SCHEMA,
        scope=scope,
        authoring=authoring,
        cases=tuple(cases),
        corpus_sha256=corpus_sha256,
    )


def load_phrase_corpus(
    path: str | Path = DEFAULT_PHRASE_CORPUS,
) -> PhraseCorpus:
    """Strictly load and decode a bounded phrase corpus file."""

    loaded = load_strict_json_file(
        path,
        limits=_CORPUS_LIMITS,
        label="phrase corpus",
    )
    return decode_phrase_corpus(loaded.value)


def _expected_counter(
    case: PhraseCase,
    *,
    source_id: str,
) -> Counter[_AtomKey]:
    return Counter(
        {
            _AtomKey(
                kind=atom.kind.value,
                text=atom.text,
                provenance=(
                    _SpanKey(
                        source_id=source_id,
                        start=atom.start,
                        end=atom.end,
                        quote=atom.text,
                    ),
                ),
            ): 1
            for atom in case.expected
        }
    )


def _predicted_counter(memory: Any) -> Counter[_AtomKey]:
    return Counter(
        _AtomKey(
            kind=item.kind.value,
            text=item.text,
            provenance=tuple(
                _SpanKey(
                    source_id=span.source_id,
                    start=span.start,
                    end=span.end,
                    quote=span.quote,
                )
                for span in item.provenance
            ),
        )
        for item in memory.items
    )


def _expanded(counter: Counter[_AtomKey]) -> list[dict[str, Any]]:
    return [
        key.to_dict()
        for key in sorted(counter)
        for _ in range(counter[key])
    ]


def _rate(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 1.0
    return round(numerator / denominator, 6)


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return round(2 * precision * recall / (precision + recall), 6)


def run_phrase_evaluation(
    corpus: PhraseCorpus,
    *,
    package_version: str = __version__,
) -> dict[str, Any]:
    """Evaluate a validated corpus through the default deterministic compiler."""

    if not isinstance(corpus, PhraseCorpus):
        raise TypeError("corpus must be a PhraseCorpus value")
    package_version = _validated_package_version(package_version)
    compiler = ContextCompiler(policy=_POLICY)
    case_reports: list[dict[str, Any]] = []
    total_expected: Counter[_AtomKey] = Counter()
    total_predicted: Counter[_AtomKey] = Counter()
    total_matched: Counter[_AtomKey] = Counter()
    positive_cases = 0
    positive_exact = 0
    negative_cases = 0
    negative_clean = 0
    case_exact = 0
    verification_failures = 0

    for case in corpus.cases:
        source_id = f"phrase:{case.id}"
        source = SourceRecord.create(
            id=source_id,
            sequence=0,
            role=case.role,
            content=case.content,
        )
        memory = compiler.compile([source])
        expected = _expected_counter(case, source_id=source_id)
        predicted = _predicted_counter(memory)
        matched = expected & predicted
        false_positives = predicted - expected
        false_negatives = expected - predicted
        verification_passed = memory.verification.passed
        perfect = (
            not false_positives
            and not false_negatives
            and verification_passed
        )

        total_expected.update(expected)
        total_predicted.update(predicted)
        total_matched.update(matched)
        case_exact += int(perfect)
        verification_failures += int(not verification_passed)
        if expected:
            positive_cases += 1
            positive_exact += int(perfect)
        else:
            negative_cases += 1
            negative_clean += int(not predicted and verification_passed)

        case_reports.append(
            {
                "id": case.id,
                "role": case.role,
                "verification_passed": verification_passed,
                "verification_issues": [
                    issue.to_dict() for issue in memory.verification.issues
                ],
                "expected": _expanded(expected),
                "predicted": _expanded(predicted),
                "true_positive_count": sum(matched.values()),
                "false_positive_count": sum(false_positives.values()),
                "false_negative_count": sum(false_negatives.values()),
                "false_positives": _expanded(false_positives),
                "false_negatives": _expanded(false_negatives),
                "exact_match": perfect,
            }
        )

    true_positives = sum(total_matched.values())
    expected_count = sum(total_expected.values())
    predicted_count = sum(total_predicted.values())
    false_positives = predicted_count - true_positives
    false_negatives = expected_count - true_positives
    precision = _rate(true_positives, predicted_count)
    recall = _rate(true_positives, expected_count)

    kinds = sorted(
        {key.kind for key in total_expected}
        | {key.kind for key in total_predicted}
    )
    by_kind: dict[str, Any] = {}
    for kind in kinds:
        kind_expected = sum(
            count for key, count in total_expected.items() if key.kind == kind
        )
        kind_predicted = sum(
            count for key, count in total_predicted.items() if key.kind == kind
        )
        kind_matched = sum(
            count for key, count in total_matched.items() if key.kind == kind
        )
        kind_precision = _rate(kind_matched, kind_predicted)
        kind_recall = _rate(kind_matched, kind_expected)
        by_kind[kind] = {
            "expected": kind_expected,
            "predicted": kind_predicted,
            "true_positives": kind_matched,
            "false_positives": kind_predicted - kind_matched,
            "false_negatives": kind_expected - kind_matched,
            "precision": kind_precision,
            "recall": kind_recall,
            "f1": _f1(kind_precision, kind_recall),
        }

    report: dict[str, Any] = {
        "schema": PHRASE_REPORT_SCHEMA,
        "corpus": {
            "schema": corpus.schema,
            "scope": corpus.scope,
            "corpus_sha256": corpus.corpus_sha256,
            "case_count": len(corpus.cases),
            "positive_case_count": positive_cases,
            "negative_case_count": negative_cases,
        },
        "system": {
            "name": "default-deterministic-compiler",
            "package_version": package_version,
            "extractor_revision": "rules-v1",
            "model_id": "deterministic-no-model",
            "network_model_api": False,
            "model_service_cost_usd": 0.0,
            "token_budget": _POLICY.token_budget,
            "minimum_compression_ratio": _POLICY.minimum_compression_ratio,
        },
        "metrics": {
            "expected_atoms": expected_count,
            "predicted_atoms": predicted_count,
            "true_positives": true_positives,
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "precision": precision,
            "recall": recall,
            "f1": _f1(precision, recall),
            "case_exact_matches": case_exact,
            "case_exact_match_rate": _rate(
                case_exact,
                len(corpus.cases),
            ),
            "positive_case_exact_matches": positive_exact,
            "positive_case_exact_match_rate": _rate(
                positive_exact,
                positive_cases,
            ),
            "negative_cases_without_predictions": negative_clean,
            "negative_case_accuracy": _rate(
                negative_clean,
                negative_cases,
            ),
            "verification_failures": verification_failures,
            "by_kind": by_kind,
        },
        "cases": case_reports,
    }
    report["report_sha256"] = _canonical_sha256(report)
    return report


def load_phrase_report(path: str | Path) -> dict[str, Any]:
    """Strictly load a bounded phrase report for replay."""

    loaded = load_strict_json_file(
        path,
        limits=_REPORT_LIMITS,
        label="phrase evaluation report",
    )
    if not isinstance(loaded.value, dict):
        raise PhraseEvaluationError(
            "phrase evaluation report must be an object"
        )
    return loaded.value


def verify_phrase_report(
    document: Any,
    corpus: PhraseCorpus,
) -> dict[str, Any]:
    """Verify a report self-hash and exact deterministic replay."""

    if not isinstance(document, dict):
        raise PhraseEvaluationError(
            "phrase evaluation report must be an object"
        )
    if document.get("schema") != PHRASE_REPORT_SCHEMA:
        raise PhraseEvaluationError(
            "phrase evaluation report schema is unsupported"
        )
    report_sha256 = document.get("report_sha256")
    if (
        not isinstance(report_sha256, str)
        or _SHA256.fullmatch(report_sha256) is None
    ):
        raise PhraseEvaluationError(
            "phrase evaluation report_sha256 must be lowercase SHA-256"
        )
    unsigned = dict(document)
    unsigned.pop("report_sha256")
    if _canonical_sha256(unsigned) != report_sha256:
        raise PhraseEvaluationError(
            "phrase evaluation report SHA-256 mismatch"
        )
    system = document.get("system")
    if type(system) is not dict:
        raise PhraseEvaluationError(
            "phrase evaluation report system is invalid"
        )
    replay = run_phrase_evaluation(
        corpus,
        package_version=_validated_package_version(
            system.get("package_version")
        ),
    )
    if document != replay:
        raise PhraseEvaluationError(
            "phrase evaluation report does not match deterministic replay"
        )
    return {
        "schema": PHRASE_VERIFICATION_SCHEMA,
        "verified": True,
        "corpus_sha256": corpus.corpus_sha256,
        "report_sha256": report_sha256,
        "case_count": len(corpus.cases),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_PHRASE_CORPUS,
        help="self-hashed phrase corpus (defaults to the bundled diagnostic)",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        help="atomically write the deterministic evaluation report",
    )
    parser.add_argument(
        "--verify-report",
        type=Path,
        help="strictly load and replay-verify an existing report",
    )
    args = parser.parse_args(argv)
    if args.verify_report is not None and args.json_out is not None:
        parser.error("--json-out cannot be combined with --verify-report")

    try:
        corpus = load_phrase_corpus(args.corpus)
        if args.verify_report is not None:
            output = verify_phrase_report(
                load_phrase_report(args.verify_report),
                corpus,
            )
        else:
            output = run_phrase_evaluation(corpus)
        rendered = json.dumps(
            output,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ) + "\n"
        if args.json_out is not None:
            atomic_write_text(args.json_out, rendered)
        sys.stdout.write(rendered)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
