"""Canonicalization and conservative temporal-state resolution."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from types import MappingProxyType

from .limits import (
    CompilationLimitError,
    CompilationLimits,
    _CompilationWorkBudget,
)
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
    r"(?:(?:must|shall)\s+)?(?:is|be|use|using|stays|remains)\s+"
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


def _validate_resolution_collection(
    items: list[MemoryItem],
    *,
    limits: CompilationLimits,
    phase: str,
) -> None:
    if len(items) > limits.max_resolved_items:
        raise CompilationLimitError(
            f"{phase} exceeds {limits.max_resolved_items} resolved items"
        )
    provenance_spans = sum(len(item.provenance) for item in items)
    if provenance_spans > limits.max_provenance_spans:
        raise CompilationLimitError(
            f"{phase} exceeds {limits.max_provenance_spans} provenance spans"
        )


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


def resolve_corrections(
    items: list[MemoryItem],
    *,
    limits: CompilationLimits,
    work_budget: _CompilationWorkBudget,
) -> list[MemoryItem]:
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
        ranked: list[tuple[float, MemoryItem]] = []
        for prior in state:
            work_budget.consume(1, phase="correction resolution")
            if (
                prior.kind not in candidates_by_kind
                or prior.source_sequence >= correction.source_sequence
                or prior.status != MemoryStatus.ACTIVE
                or not _has_replacement_evidence(correction, prior)
            ):
                continue
            ranked.append((_replacement_score(correction, prior), prior))
        ranked.sort(
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
        replacements: list[MemoryItem] = []
        for item in state:
            work_budget.consume(1, phase="correction replacement lookup")
            if (
                item.id != correction.id
                and item.kind == prior.kind
                and item.source_sequence == correction.source_sequence
                and "correction-derived" in item.tags
            ):
                replacements.append(item)
        if _REVOCATION.search(correction.text):
            continue
        if replacements:
            replacement = replacements[0]
            replacement.supersedes = sorted(set(replacement.supersedes) | {prior.id})
        else:
            state.append(_make_replacement(correction, prior))
            _validate_resolution_collection(
                state,
                limits=limits,
                phase="correction resolution",
            )
    resolved = canonicalize(state)
    _validate_resolution_collection(
        resolved,
        limits=limits,
        phase="correction canonicalization",
    )
    return resolved


def resolve_unresolved(
    items: list[MemoryItem],
    *,
    limits: CompilationLimits,
    work_budget: _CompilationWorkBudget,
) -> list[MemoryItem]:
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
            work_budget.consume(1, phase="unresolved-state resolution")
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
    resolved = canonicalize(ordered)
    _validate_resolution_collection(
        resolved,
        limits=limits,
        phase="unresolved-state canonicalization",
    )
    return resolved


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


@dataclass(frozen=True, slots=True)
class _ConflictFacts:
    """Immutable text facts reused by the exact internal conflict loop."""

    tokens: frozenset[str]
    words: frozenset[str]
    negated: bool
    numbers: frozenset[str]
    scope: str | None
    database: str | None
    numeric_subject: frozenset[str]
    nonexclusive: bool
    exclusive: bool


@dataclass(frozen=True, slots=True)
class _ConflictDependencies:
    token_pattern: re.Pattern[str]
    stopwords: frozenset[str]
    scope_values: MappingProxyType[str, str]
    negation_pattern: re.Pattern[str]
    database_pattern: re.Pattern[str]
    reverse_database_pattern: re.Pattern[str]
    nonexclusive_pattern: re.Pattern[str]
    exclusive_pattern: re.Pattern[str]
    generic_value_words: frozenset[str]
    antonym_pairs: tuple[tuple[str, str], ...]


def _conflict_facts(
    item: MemoryItem,
    dependencies: _ConflictDependencies,
    *,
    _facts_type: type[_ConflictFacts] = _ConflictFacts,
) -> _ConflictFacts:
    tokens: set[str] = set()
    for raw_token in dependencies.token_pattern.findall(item.text):
        token = raw_token.casefold().strip("._-")
        if token and token not in dependencies.stopwords and len(token) > 1:
            tokens.add(token)
    frozen_tokens = frozenset(tokens)
    numbers = frozenset(
        token
        for token in frozen_tokens
        if any(character.isdigit() for character in token)
    )
    scope_values = {
        dependencies.scope_values[token]
        for token in frozen_tokens
        if token in dependencies.scope_values
    }
    text = item.text.casefold()
    database_match = dependencies.database_pattern.search(
        text
    ) or dependencies.reverse_database_pattern.search(text)
    database = (
        database_match.group("value").casefold().strip(".,;:")
        if database_match
        else None
    )
    return _facts_type(
        tokens=frozen_tokens,
        words=frozenset(
            token for token in frozen_tokens if token not in numbers
        ),
        negated=bool(dependencies.negation_pattern.search(item.text)),
        numbers=numbers,
        scope=next(iter(scope_values)) if len(scope_values) == 1 else None,
        database=database,
        numeric_subject=frozenset(
            set(frozen_tokens)
            - set(numbers)
            - dependencies.generic_value_words
        ),
        nonexclusive=bool(dependencies.nonexclusive_pattern.search(item.text)),
        exclusive=bool(dependencies.exclusive_pattern.search(item.text)),
    )


def _facts_have_different_scopes(
    left: _ConflictFacts,
    right: _ConflictFacts,
) -> bool:
    return bool(left.scope and right.scope and left.scope != right.scope)


def _facts_have_numeric_conflict(
    left: _ConflictFacts,
    right: _ConflictFacts,
) -> bool:
    if (
        not left.numbers
        or not right.numbers
        or not left.numbers.isdisjoint(right.numbers)
    ):
        return False
    if left.nonexclusive or right.nonexclusive:
        return False
    if not left.exclusive or not right.exclusive:
        return False
    return bool(
        left.numeric_subject
        and left.numeric_subject == right.numeric_subject
    )


def _facts_have_structured_conflict(
    left: _ConflictFacts,
    right: _ConflictFacts,
    *,
    dependencies: _ConflictDependencies,
) -> bool:
    if bool(left.scope and right.scope and left.scope != right.scope):
        return False
    for first, second in dependencies.antonym_pairs:
        opposed = (first in left.tokens and second in right.tokens) or (
            second in left.tokens and first in right.tokens
        )
        left_base = left.tokens - {first, second}
        right_base = right.tokens - {first, second}
        if opposed and left_base and left_base == right_base:
            return True
    left_subject = left.tokens - {
        left.database or "",
        "database",
        "db",
        "engine",
        "backend",
    }
    right_subject = right.tokens - {
        right.database or "",
        "database",
        "db",
        "engine",
        "backend",
    }
    return bool(
        left.database
        and right.database
        and left.database != right.database
        and left_subject == right_subject
    )


_DEFAULT_PROPOSITION_SHAPE = _proposition_shape
_DEFAULT_DIFFERENT_SCOPES = _different_scopes
_DEFAULT_NUMERIC_CONFLICT = _numeric_conflict
_DEFAULT_STRUCTURED_CONFLICT = _structured_conflict
_DEFAULT_SIGNIFICANT_TOKENS = significant_tokens
_DEFAULT_DATABASE_VALUE = _database_value
_DEFAULT_CONFLICT_FACTS_TYPE = _ConflictFacts
_DEFAULT_CONFLICT_DEPENDENCIES_TYPE = _ConflictDependencies
_DEFAULT_CONFLICT_FACTS = _conflict_facts
_DEFAULT_FACTS_DIFFERENT_SCOPES = _facts_have_different_scopes
_DEFAULT_FACTS_NUMERIC_CONFLICT = _facts_have_numeric_conflict
_DEFAULT_FACTS_STRUCTURED_CONFLICT = _facts_have_structured_conflict
_DEFAULT_WORK_BUDGET_CONSUME = _CompilationWorkBudget.consume
_DEFAULT_CONFLICT_DEPENDENCIES = _ConflictDependencies(
    token_pattern=_TOKEN,
    stopwords=frozenset(_STOPWORDS),
    scope_values=MappingProxyType(dict(_SCOPE_VALUES)),
    negation_pattern=_NEGATION,
    database_pattern=_DATABASE_VALUE,
    reverse_database_pattern=_REVERSE_DATABASE_VALUE,
    nonexclusive_pattern=_NONEXCLUSIVE_VALUE,
    exclusive_pattern=_EXCLUSIVE_VALUE,
    generic_value_words=frozenset(_GENERIC_VALUE_WORDS),
    antonym_pairs=tuple(_ANTONYM_PAIRS),
)


def _can_reuse_conflict_facts(
    items: list[MemoryItem],
    *,
    limits: CompilationLimits,
    work_budget: _CompilationWorkBudget,
) -> bool:
    """Exclude caller-defined dispatch and non-exact text from the fast path."""

    dependencies = _DEFAULT_CONFLICT_DEPENDENCIES
    if (
        type(items) is not list
        or type(limits) is not CompilationLimits
        or type(work_budget) is not _CompilationWorkBudget
        or _CompilationWorkBudget.consume is not _DEFAULT_WORK_BUDGET_CONSUME
        or _ConflictFacts is not _DEFAULT_CONFLICT_FACTS_TYPE
        or _ConflictDependencies is not _DEFAULT_CONFLICT_DEPENDENCIES_TYPE
        or _proposition_shape is not _DEFAULT_PROPOSITION_SHAPE
        or _different_scopes is not _DEFAULT_DIFFERENT_SCOPES
        or _numeric_conflict is not _DEFAULT_NUMERIC_CONFLICT
        or _structured_conflict is not _DEFAULT_STRUCTURED_CONFLICT
        or significant_tokens is not _DEFAULT_SIGNIFICANT_TOKENS
        or _database_value is not _DEFAULT_DATABASE_VALUE
        or _conflict_facts is not _DEFAULT_CONFLICT_FACTS
        or _facts_have_different_scopes is not _DEFAULT_FACTS_DIFFERENT_SCOPES
        or _facts_have_numeric_conflict is not _DEFAULT_FACTS_NUMERIC_CONFLICT
        or _facts_have_structured_conflict
        is not _DEFAULT_FACTS_STRUCTURED_CONFLICT
        or _TOKEN is not dependencies.token_pattern
        or _NEGATION is not dependencies.negation_pattern
        or _DATABASE_VALUE is not dependencies.database_pattern
        or _REVERSE_DATABASE_VALUE is not dependencies.reverse_database_pattern
        or _NONEXCLUSIVE_VALUE is not dependencies.nonexclusive_pattern
        or _EXCLUSIVE_VALUE is not dependencies.exclusive_pattern
        or type(_STOPWORDS) is not set
        or frozenset(_STOPWORDS) != dependencies.stopwords
        or type(_SCOPE_VALUES) is not dict
        or dependencies.scope_values != _SCOPE_VALUES
        or type(_GENERIC_VALUE_WORDS) is not set
        or frozenset(_GENERIC_VALUE_WORDS)
        != dependencies.generic_value_words
        or type(_ANTONYM_PAIRS) is not tuple
        or dependencies.antonym_pairs != _ANTONYM_PAIRS
    ):
        return False
    if len({id(item) for item in items}) != len(items):
        return False
    return all(
        type(item) is MemoryItem
        and type(item.text) is str
        and type(item.id) is str
        and type(item.kind) is MemoryKind
        and type(item.status) is MemoryStatus
        for item in items
    )


def mark_conflicts(
    items: list[MemoryItem],
    *,
    limits: CompilationLimits,
    work_budget: _CompilationWorkBudget,
) -> list[MemoryItem]:
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

    conflict_facts_type = _DEFAULT_CONFLICT_FACTS_TYPE
    reuse_facts = _can_reuse_conflict_facts(
        items,
        limits=limits,
        work_budget=work_budget,
    )
    fact_builder = _DEFAULT_CONFLICT_FACTS
    scope_checker = _DEFAULT_FACTS_DIFFERENT_SCOPES
    numeric_checker = _DEFAULT_FACTS_NUMERIC_CONFLICT
    structured_checker = _DEFAULT_FACTS_STRUCTURED_CONFLICT
    work_consumer = _DEFAULT_WORK_BUDGET_CONSUME
    conflict_dependencies = _DEFAULT_CONFLICT_DEPENDENCIES
    facts_by_identity: dict[int, _ConflictFacts] = {}
    seen_pairs: set[tuple[str, str]] = set()
    provenance_spans = sum(len(item.provenance) for item in items)
    for kind_items in by_kind.values():
        for index, left in enumerate(kind_items):
            if reuse_facts:
                left_facts = facts_by_identity.get(id(left))
                left_words = frozenset()
                left_negated = False
                left_numbers = ()
            else:
                left_facts = None
                left_words, left_negated, left_numbers = _proposition_shape(left)
            for right in kind_items[index + 1 :]:
                if reuse_facts:
                    work_consumer(work_budget, 1, phase="conflict detection")
                else:
                    work_budget.consume(1, phase="conflict detection")
                if reuse_facts:
                    if left_facts is None:
                        left_facts = fact_builder(
                            left,
                            conflict_dependencies,
                            _facts_type=conflict_facts_type,
                        )
                        facts_by_identity[id(left)] = left_facts
                    left_words = left_facts.words
                    left_negated = left_facts.negated
                    left_numbers = left_facts.numbers
                    right_facts = facts_by_identity.get(id(right))
                    if right_facts is None:
                        right_facts = fact_builder(
                            right,
                            conflict_dependencies,
                            _facts_type=conflict_facts_type,
                        )
                        facts_by_identity[id(right)] = right_facts
                    assert left_facts is not None
                    if scope_checker(left_facts, right_facts):
                        continue
                    right_words = right_facts.words
                    right_negated = right_facts.negated
                    right_numbers = right_facts.numbers
                else:
                    right_facts = None
                    if _different_scopes(left, right):
                        continue
                    right_words, right_negated, right_numbers = _proposition_shape(
                        right
                    )
                overlap = left_words & right_words
                min_words = max(1, min(len(left_words), len(right_words)))
                related = len(overlap) / min_words >= 0.65 and len(overlap) >= 1
                polarity_conflict = left_negated != right_negated
                if reuse_facts:
                    assert left_facts is not None and right_facts is not None
                    value_conflict = numeric_checker(
                        left_facts,
                        right_facts,
                    )
                else:
                    value_conflict = _numeric_conflict(
                        left,
                        right,
                        left_numbers,
                        right_numbers,
                    )
                lexical_conflict = related and (polarity_conflict or value_conflict)
                if not lexical_conflict:
                    if reuse_facts:
                        assert left_facts is not None and right_facts is not None
                        if not structured_checker(
                            left_facts,
                            right_facts,
                            dependencies=conflict_dependencies,
                        ):
                            continue
                    elif not _structured_conflict(left, right):
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
                if len(items) + len(additions) >= limits.max_resolved_items:
                    raise CompilationLimitError(
                        "conflict detection exceeds "
                        f"{limits.max_resolved_items} resolved items"
                    )
                conflict_provenance = [
                    *left.provenance,
                    *right.provenance,
                ]
                provenance_spans += len(conflict_provenance)
                if provenance_spans > limits.max_provenance_spans:
                    raise CompilationLimitError(
                        "conflict detection exceeds "
                        f"{limits.max_provenance_spans} provenance spans"
                    )
                additions.append(
                    MemoryItem(
                        id=f"m-{stable_hash_parts('conflict', pair[0], pair[1])}",
                        kind=MemoryKind.UNRESOLVED,
                        text=text,
                        provenance=conflict_provenance,
                        priority=100,
                        confidence=1.0,
                        tags=["detected-conflict"],
                        conflicts_with=list(pair),
                        metadata={
                            "source_sequence": max(
                                left.source_sequence,
                                right.source_sequence,
                            ),
                            "source_role": ",".join(
                                sorted(
                                    {
                                        str(
                                            left.metadata.get(
                                                "source_role",
                                                "unknown",
                                            )
                                        ),
                                        str(
                                            right.metadata.get(
                                                "source_role",
                                                "unknown",
                                            )
                                        ),
                                    }
                                )
                            ),
                            "extractor": "temporal-resolver",
                        },
                    )
                )
    resolved = canonicalize([*items, *additions])
    _validate_resolution_collection(
        resolved,
        limits=limits,
        phase="conflict canonicalization",
    )
    return resolved


def resolve_temporal_state(
    items: list[MemoryItem],
    *,
    limits: CompilationLimits,
    work_budget: _CompilationWorkBudget,
) -> list[MemoryItem]:
    canonical = canonicalize(items)
    _validate_resolution_collection(
        canonical,
        limits=limits,
        phase="initial canonicalization",
    )
    corrected = resolve_corrections(
        canonical,
        limits=limits,
        work_budget=work_budget,
    )
    unresolved = resolve_unresolved(
        corrected,
        limits=limits,
        work_budget=work_budget,
    )
    return mark_conflicts(
        unresolved,
        limits=limits,
        work_budget=work_budget,
    )
