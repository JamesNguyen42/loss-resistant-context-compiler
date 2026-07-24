"""Canonicalization and conservative temporal-state resolution."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable

from .models import MemoryItem, MemoryKind, MemoryStatus, stable_hash_parts

_TOKEN = re.compile(
    r"[^\W\d_][\w.-]*|\d+(?:\.\d+)+(?:[-+][\w.]+)?|\d+",
    re.UNICODE,
)
_NEGATION = re.compile(
    r"\b(?:not|never|without|isn't|aren't|doesn't|don't|cannot|can't|"
    r"no(?!\s+(?:later|earlier|more|less|greater|fewer)\b))\b",
    re.I,
)
_CORRECTION_MARKER = re.compile(
    r"(?:\bcorrection\s*(?::|,)|\bcorrection\s+(?:is|was|use|set|make|keep)\b|"
    r"\b(?:actually|scratch that|instead|rather than|ignore|no longer|I meant|to clarify)\b)",
    re.I,
)
_EXPLICIT_REPLACEMENT = re.compile(
    r"\b(?:scratch that|instead(?: of)?|rather than|no longer|"
    r"ignore (?:my |the )?(?:earlier|previous)|changed? (?:the )?requirement|"
    r"I meant|replace .{1,80} with|from .{1,80} to|not .{1,80},? but)\b",
    re.I,
)
_ADDITIVE_MARKER = re.compile(
    r"\b(?:also|additionally|in addition|as well|too)\b",
    re.I,
)
_REVOCATION = re.compile(
    r"\b(?:ignore (?:my |the )?(?:earlier|previous).{0,80}(?:requirement|constraint)|"
    r"no longer required|remove .{0,80}(?:requirement|constraint)|"
    r"drop .{0,80}(?:requirement|constraint)|(?:requirement|constraint) (?:is )?revoked)\b",
    re.I,
)
_COMMITMENT_MARKER = re.compile(
    r"\b(?:must|shall|do not|don't|never|cannot|can't|required|forbidden)\b",
    re.I,
)
_POSITIVE_CONFIRMATION = re.compile(
    r"\b(?:confirmed|verified|established|resolved|determined|answer\s*(?:is|:))",
    re.I,
)
_DATABASE_VALUE = re.compile(
    r"\b(?:database|db)(?:\s+(?:engine|backend))?\s+"
    r"(?:(?:must|shall)\s+)?(?:is|be|use|using)\s+"
    r"(?P<value>[A-Za-z][\w.-]*)",
    re.I,
)
_REVERSE_DATABASE_VALUE = re.compile(
    r"\b(?:use|using)\s+(?P<value>[A-Za-z][\w.-]*)\s+"
    r"(?:as\s+)?(?:the\s+)?(?:database|db)(?:\s+(?:engine|backend))?\b"
    r"(?!\s+[A-Za-z])",
    re.I,
)
_ANTONYM_PAIRS = (
    ("enable", "disable"),
    ("enabled", "disabled"),
    ("allow", "forbid"),
    ("allowed", "forbidden"),
    ("include", "exclude"),
    ("included", "excluded"),
)
_RESOLUTION_ROLES = frozenset({"user", "system", "developer", "assistant"})
_SCOPE_VALUES = {
    "dev": "development",
    "development": "development",
    "local": "local",
    "prod": "production",
    "production": "production",
    "staging": "staging",
    "test": "testing",
    "testing": "testing",
}
_NONEXCLUSIVE_VALUE = re.compile(
    r"\b(?:compatible with|support(?:s|ed|ing)?|at least|at most|"
    r"no (?:more|less) than|minimum|maximum|between)\b",
    re.I,
)
_EXCLUSIVE_VALUE = re.compile(
    r"\b(?:exactly|(?:must|shall)\s+(?:be|equal)|equals?|set\s+to)\b",
    re.I,
)
_GENERIC_VALUE_WORDS = {
    "count",
    "exactly",
    "limit",
    "milliseconds",
    "ms",
    "number",
    "port",
    "run",
    "second",
    "seconds",
    "service",
    "set",
    "timeout",
    "use",
    "using",
    "value",
}
_STATUS_RANK = {
    MemoryStatus.ACTIVE: 0,
    MemoryStatus.DISCARDED: 1,
    MemoryStatus.CONFLICTING: 2,
    MemoryStatus.SUPERSEDED: 3,
}
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "change",
    "changed",
    "correction",
    "do",
    "for",
    "from",
    "i",
    "in",
    "instead",
    "is",
    "it",
    "must",
    "my",
    "no",
    "not",
    "of",
    "on",
    "or",
    "previous",
    "requirement",
    "required",
    "rather",
    "the",
    "this",
    "to",
    "use",
    "we",
    "with",
}


def normalize_text(text: str) -> str:
    return " ".join(text.casefold().split()).strip(" .;,:-`")


def significant_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw_token in _TOKEN.findall(text):
        token = raw_token.casefold().strip("._-")
        if token and token not in _STOPWORDS and len(token) > 1:
            tokens.add(token)
    return tokens


def canonicalize(items: Iterable[MemoryItem]) -> list[MemoryItem]:
    """Deduplicate same-kind/same-meaning items while unioning provenance."""

    groups: dict[tuple[MemoryKind, str], MemoryItem] = {}
    for item in sorted(items, key=lambda value: (value.source_sequence, value.id)):
        key = (item.kind, normalize_text(item.text))
        existing = groups.get(key)
        if existing is None:
            item.id = f"m-{stable_hash_parts(item.kind.value, key[1])}"
            groups[key] = item
            continue
        existing_sequence = existing.source_sequence
        known_spans = {
            (span.source_id, span.start, span.end, span.quote_sha256)
            for span in existing.provenance
        }
        for span in item.provenance:
            identity = (span.source_id, span.start, span.end, span.quote_sha256)
            if identity not in known_spans:
                existing.provenance.append(span)
                known_spans.add(identity)
        existing.priority = max(existing.priority, item.priority)
        existing.confidence = max(existing.confidence, item.confidence)
        existing.exact = existing.exact or item.exact
        existing.tags = sorted(set(existing.tags) | set(item.tags))
        existing.supersedes = sorted(set(existing.supersedes) | set(item.supersedes))
        existing.conflicts_with = sorted(
            set(existing.conflicts_with) | set(item.conflicts_with)
        )
        if item.source_sequence > existing_sequence or (
            item.source_sequence == existing_sequence
            and _STATUS_RANK[item.status] > _STATUS_RANK[existing.status]
        ):
            existing.status = item.status
        existing.metadata["source_sequence"] = max(existing.source_sequence, item.source_sequence)
        roles = set(str(existing.metadata.get("source_role", "")).split(","))
        roles.update(str(item.metadata.get("source_role", "")).split(","))
        existing.metadata["source_role"] = ",".join(sorted(role for role in roles if role))
        extractors = set(str(existing.metadata.get("extractor", "")).split(","))
        extractors.update(str(item.metadata.get("extractor", "")).split(","))
        existing.metadata["extractor"] = ",".join(sorted(value for value in extractors if value))
    return sorted(
        groups.values(),
        key=lambda value: (value.source_sequence, value.kind.value, value.id),
    )


def _replacement_score(correction: MemoryItem, prior: MemoryItem) -> float:
    correction_tokens = significant_tokens(correction.text)
    prior_tokens = significant_tokens(prior.text)
    if not correction_tokens or not prior_tokens:
        return 0.0
    overlap = correction_tokens & prior_tokens
    score = len(overlap) / max(1, min(len(correction_tokens), len(prior_tokens)))
    # Versions, counts, paths, symbols, and other exact identifiers are
    # unusually strong signals that the correction refers to the prior item.
    identifiers = {
        token
        for token in overlap
        if any(char.isdigit() for char in token) or "/" in token or "\\" in token or "." in token
    }
    if identifiers:
        score += 0.35
    content_overlap = {
        token for token in overlap if not any(char.isdigit() for char in token)
    }
    prior_fully_mentioned = prior_tokens <= correction_tokens and len(prior_tokens) <= 2
    if (
        prior.kind != MemoryKind.EXACT_REFERENCE
        and len(content_overlap) < 2
        and not prior_fully_mentioned
    ):
        score = min(score, 0.40)
    # A typed value or explicit polarity reversal with the same complete
    # proposition is stronger replacement evidence than raw token overlap.
    if _structured_conflict(correction, prior):
        score = max(score, 0.75)
    if _REVOCATION.search(correction.text) and overlap:
        score = max(score, 0.70)
    return min(1.0, score)


def _has_replacement_evidence(correction: MemoryItem, prior: MemoryItem) -> bool:
    correction_tokens = significant_tokens(correction.text)
    prior_tokens = significant_tokens(prior.text)
    overlap = correction_tokens & prior_tokens
    if (
        prior.kind == MemoryKind.CONSTRAINT
        and len(_COMMITMENT_MARKER.findall(prior.text)) > 1
    ):
        return False
    explicit_replacement = _EXPLICIT_REPLACEMENT.search(correction.text)
    if _ADDITIVE_MARKER.search(correction.text) and not explicit_replacement:
        return False
    if explicit_replacement and overlap:
        return True
    correction_numbers = {token for token in correction_tokens if any(c.isdigit() for c in token)}
    prior_numbers = {token for token in prior_tokens if any(c.isdigit() for c in token)}
    content_overlap = {
        token for token in overlap if not any(character.isdigit() for character in token)
    }
    if (
        correction_numbers
        and prior_numbers
        and correction_numbers.isdisjoint(prior_numbers)
        and content_overlap
    ):
        return True
    if (
        bool(_NEGATION.search(correction.text)) != bool(_NEGATION.search(prior.text))
        and len(content_overlap) >= 2
    ):
        return True
    return _structured_conflict(correction, prior)


def _make_replacement(correction: MemoryItem, prior: MemoryItem) -> MemoryItem:
    replacement_id = stable_hash_parts(
        "replacement",
        prior.kind.value,
        normalize_text(correction.text),
    )
    return MemoryItem(
        id=f"m-{replacement_id}",
        kind=prior.kind,
        text=correction.text,
        provenance=list(correction.provenance),
        priority=max(prior.priority, correction.priority),
        confidence=min(correction.confidence, 0.95),
        exact=correction.exact,
        tags=sorted(set(correction.tags) | {"correction-derived", "current-value"}),
        supersedes=[prior.id],
        metadata={
            **correction.metadata,
            "derived_from": correction.id,
            "replaces_kind": prior.kind.value,
        },
    )


def resolve_corrections(items: list[MemoryItem]) -> list[MemoryItem]:
    """Link explicit corrections to earlier state without guessing silently.

    A high-confidence match supersedes one earlier item and emits a current
    value of the same type.  If no safe match exists, the correction remains a
    standalone protected item and no previous claim is invalidated.
    """

    candidates_by_kind = {
        MemoryKind.GOAL,
        MemoryKind.CONSTRAINT,
        MemoryKind.CONFIRMED_FACT,
        MemoryKind.DECISION,
        MemoryKind.EXACT_REFERENCE,
    }
    state = sorted(items, key=lambda value: (value.source_sequence, value.id))
    corrections = [item for item in state if item.kind == MemoryKind.USER_CORRECTION]
    for correction in corrections:
        if (
            not _CORRECTION_MARKER.search(correction.text)
            and "explicit-correction-label" not in correction.tags
        ):
            continue
        earlier = [
            item
            for item in state
            if item.kind in candidates_by_kind
            and item.source_sequence < correction.source_sequence
            and item.status == MemoryStatus.ACTIVE
        ]
        ranked = sorted(
            (
                (_replacement_score(correction, prior), prior)
                for prior in earlier
                if _has_replacement_evidence(correction, prior)
            ),
            key=lambda pair: (pair[0], pair[1].source_sequence),
            reverse=True,
        )
        if not ranked or ranked[0][0] < 0.45:
            correction.tags = sorted(set(correction.tags) | {"additive-clarification"})
            continue
        best_score, prior = ranked[0]
        # Avoid pretending certainty when two old claims are equally plausible.
        if len(ranked) > 1 and best_score - ranked[1][0] < 0.08:
            correction.tags = sorted(set(correction.tags) | {"ambiguous-supersession"})
            continue
        prior.status = MemoryStatus.SUPERSEDED
        prior.tags = sorted(set(prior.tags) | {"superseded-by-correction"})
        correction.supersedes.append(prior.id)
        correction.supersedes = sorted(set(correction.supersedes))
        correction.metadata["supersession_score"] = round(best_score, 4)
        replacements = [
            item
            for item in state
            if item.id != correction.id
            and item.kind == prior.kind
            and item.source_sequence == correction.source_sequence
            and "correction-derived" in item.tags
        ]
        if _REVOCATION.search(correction.text):
            continue
        if replacements:
            replacement = replacements[0]
            replacement.supersedes = sorted(set(replacement.supersedes) | {prior.id})
        else:
            state.append(_make_replacement(correction, prior))
    return canonicalize(state)


def resolve_unresolved(items: list[MemoryItem]) -> list[MemoryItem]:
    """Close a prior open question only with a later explicit confirmed fact."""

    ordered = sorted(items, key=lambda value: (value.source_sequence, value.id))
    for resolution in (
        item
        for item in ordered
        if item.kind == MemoryKind.CONFIRMED_FACT
        and item.status == MemoryStatus.ACTIVE
        and _POSITIVE_CONFIRMATION.search(item.text)
        and {
            role.strip().casefold()
            for role in str(item.metadata.get("source_role", "")).split(",")
            if role.strip()
        }
        <= _RESOLUTION_ROLES
    ):
        resolution_tokens = significant_tokens(resolution.text)
        ranked: list[tuple[float, MemoryItem]] = []
        for question in ordered:
            if (
                question.kind != MemoryKind.UNRESOLVED
                or question.status != MemoryStatus.ACTIVE
                or question.source_sequence >= resolution.source_sequence
                or "detected-conflict" in question.tags
            ):
                continue
            question_tokens = significant_tokens(question.text)
            overlap = question_tokens & resolution_tokens
            if len(overlap) < 2:
                continue
            score = len(overlap) / max(1, min(len(question_tokens), len(resolution_tokens)))
            ranked.append((score, question))
        ranked.sort(key=lambda pair: (pair[0], pair[1].source_sequence), reverse=True)
        if not ranked or ranked[0][0] < 0.55:
            continue
        if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.08:
            continue
        question = ranked[0][1]
        question.status = MemoryStatus.SUPERSEDED
        resolution.supersedes = sorted(set(resolution.supersedes) | {question.id})
        resolution.tags = sorted(set(resolution.tags) | {"resolves-protected"})
    return canonicalize(ordered)


def _proposition_shape(item: MemoryItem) -> tuple[frozenset[str], bool, frozenset[str]]:
    tokens = significant_tokens(item.text)
    numbers = frozenset(token for token in tokens if any(char.isdigit() for char in token))
    words = frozenset(token for token in tokens if token not in numbers)
    return words, bool(_NEGATION.search(item.text)), numbers


def _database_value(text: str) -> str | None:
    match = _DATABASE_VALUE.search(text) or _REVERSE_DATABASE_VALUE.search(text)
    return match.group("value").casefold().strip(".,;:") if match else None


def _scope_value(text: str) -> str | None:
    tokens = significant_tokens(text)
    values = {_SCOPE_VALUES[token] for token in tokens if token in _SCOPE_VALUES}
    return next(iter(values)) if len(values) == 1 else None


def _different_scopes(left: MemoryItem, right: MemoryItem) -> bool:
    left_scope = _scope_value(left.text)
    right_scope = _scope_value(right.text)
    return bool(left_scope and right_scope and left_scope != right_scope)


def _structured_conflict(left: MemoryItem, right: MemoryItem) -> bool:
    if _different_scopes(left, right):
        return False
    left_text = left.text.casefold()
    right_text = right.text.casefold()
    left_tokens = significant_tokens(left.text)
    right_tokens = significant_tokens(right.text)
    for first, second in _ANTONYM_PAIRS:
        opposed = (first in left_tokens and second in right_tokens) or (
            second in left_tokens and first in right_tokens
        )
        left_base = left_tokens - {first, second}
        right_base = right_tokens - {first, second}
        # A shared noun is not enough: uploads/downloads, reads/writes, and
        # Windows/Linux are distinct scopes. Requiring the complete remaining
        # proposition to match deliberately favors a false negative over
        # inventing a conflict that changes task state.
        if opposed and left_base and left_base == right_base:
            return True
    left_database = _database_value(left_text)
    right_database = _database_value(right_text)
    left_subject = left_tokens - {
        left_database or "",
        "database",
        "db",
        "engine",
        "backend",
    }
    right_subject = right_tokens - {
        right_database or "",
        "database",
        "db",
        "engine",
        "backend",
    }
    return bool(
        left_database
        and right_database
        and left_database != right_database
        and left_subject == right_subject
    )


def _numeric_conflict(
    left: MemoryItem,
    right: MemoryItem,
    left_numbers: frozenset[str],
    right_numbers: frozenset[str],
) -> bool:
    if not left_numbers or not right_numbers or not left_numbers.isdisjoint(right_numbers):
        return False
    if _NONEXCLUSIVE_VALUE.search(left.text) or _NONEXCLUSIVE_VALUE.search(right.text):
        return False
    if not _EXCLUSIVE_VALUE.search(left.text) or not _EXCLUSIVE_VALUE.search(right.text):
        return False
    left_subject = (
        significant_tokens(left.text) - set(left_numbers) - _GENERIC_VALUE_WORDS
    )
    right_subject = (
        significant_tokens(right.text) - set(right_numbers) - _GENERIC_VALUE_WORDS
    )
    return bool(left_subject and left_subject == right_subject)


def mark_conflicts(items: list[MemoryItem]) -> list[MemoryItem]:
    """Flag clear unresolved contradictions; never pick a winner implicitly."""

    additions: list[MemoryItem] = []
    by_kind: dict[MemoryKind, list[MemoryItem]] = defaultdict(list)
    for item in items:
        if item.status == MemoryStatus.ACTIVE and item.kind in {
            MemoryKind.CONSTRAINT,
            MemoryKind.CONFIRMED_FACT,
            MemoryKind.DECISION,
        }:
            by_kind[item.kind].append(item)

    seen_pairs: set[tuple[str, str]] = set()
    for kind_items in by_kind.values():
        for index, left in enumerate(kind_items):
            left_words, left_negated, left_numbers = _proposition_shape(left)
            for right in kind_items[index + 1 :]:
                if _different_scopes(left, right):
                    continue
                right_words, right_negated, right_numbers = _proposition_shape(right)
                overlap = left_words & right_words
                min_words = max(1, min(len(left_words), len(right_words)))
                related = len(overlap) / min_words >= 0.65 and len(overlap) >= 1
                polarity_conflict = left_negated != right_negated
                value_conflict = _numeric_conflict(
                    left,
                    right,
                    left_numbers,
                    right_numbers,
                )
                lexical_conflict = related and (polarity_conflict or value_conflict)
                if not lexical_conflict and not _structured_conflict(left, right):
                    continue
                pair = tuple(sorted((left.id, right.id)))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                left.status = MemoryStatus.CONFLICTING
                right.status = MemoryStatus.CONFLICTING
                left.conflicts_with = sorted(set(left.conflicts_with) | {right.id})
                right.conflicts_with = sorted(set(right.conflicts_with) | {left.id})
                text = f'Unresolved source conflict: "{left.text}" versus "{right.text}"'
                additions.append(
                    MemoryItem(
                        id=f"m-{stable_hash_parts('conflict', pair[0], pair[1])}",
                        kind=MemoryKind.UNRESOLVED,
                        text=text,
                        provenance=[*left.provenance, *right.provenance],
                        priority=100,
                        confidence=1.0,
                        tags=["detected-conflict"],
                        conflicts_with=list(pair),
                        metadata={
                            "source_sequence": max(left.source_sequence, right.source_sequence),
                            "source_role": ",".join(
                                sorted(
                                    {
                                        str(left.metadata.get("source_role", "unknown")),
                                        str(right.metadata.get("source_role", "unknown")),
                                    }
                                )
                            ),
                            "extractor": "temporal-resolver",
                        },
                    )
                )
    return canonicalize([*items, *additions])


def resolve_temporal_state(items: list[MemoryItem]) -> list[MemoryItem]:
    corrected = resolve_corrections(canonicalize(items))
    return mark_conflicts(resolve_unresolved(corrected))
