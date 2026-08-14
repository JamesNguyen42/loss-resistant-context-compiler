"""Independent invariant checks for compiled memory."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .limits import _CompilationWorkBudget
from .models import (
    IssueSeverity,
    MemoryItem,
    MemoryKind,
    MemoryStatus,
    ProvenanceSpan,
    SourceRecord,
    VerificationIssue,
    VerificationReport,
    memory_item_covers_candidate,
    provenance_span_is_atomic,
    source_is_untrusted_historical,
)

_SUPPORT_TOKEN = re.compile(
    r"[^\W\d_][\w.-]*|\d+(?:\.\d+)*",
    re.UNICODE,
)
_SUPPORT_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "this",
    "to",
    "was",
    "were",
    "with",
}
_NEGATION = re.compile(r"\b(?:not|no|never|without|cannot|can't|isn't|doesn't|don't)\b", re.I)
_CORRECTION_MARKER = re.compile(
    r"(?:\bcorrection\s*(?::|,)|\bcorrection\s+(?:is|was|use|set|make|keep)\b|"
    r"\b(?:actually|scratch that|instead|rather than|ignore|no longer|I meant|to clarify)\b)",
    re.I,
)
_REVOCATION = re.compile(
    r"\b(?:ignore (?:my |the )?(?:earlier|previous).{0,80}(?:requirement|constraint)|"
    r"no longer required|remove .{0,80}(?:requirement|constraint)|"
    r"drop .{0,80}(?:requirement|constraint)|(?:requirement|constraint) (?:is )?revoked)\b",
    re.I,
)
_POSITIVE_CONFIRMATION = re.compile(
    r"\b(?:confirmed|verified|established|resolved|determined|answer\s*(?:is|:))",
    re.I,
)
_AUTHORITATIVE_COMMITMENT_ROLES = frozenset({"user", "system", "developer"})
_AUTHORITY_GATED_KINDS = frozenset(
    {MemoryKind.GOAL, MemoryKind.CONSTRAINT, MemoryKind.USER_CORRECTION}
)
_NON_TOOL_KINDS = frozenset({MemoryKind.UNRESOLVED, MemoryKind.DECISION})
_NON_TOOL_ROLES = frozenset({"user", "system", "developer", "assistant"})
_INTRINSICALLY_EXACT_KINDS = frozenset(
    {MemoryKind.EXACT_ERROR, MemoryKind.EXACT_REFERENCE}
)
_UNCERTAINTY = re.compile(
    r"\?|\b(?:whether|unknown|unclear|hypothesis|may|might|could|possibly|"
    r"probably|perhaps|likely|seems?|appears?|suspected|assumed|suggests?|"
    r"not (?:yet )?confirmed|unconfirmed|not established|cannot confirm|"
    r"if|unless)\b",
    re.I,
)
_CONFIRMATION_EVIDENCE = re.compile(
    r"(?:\b(?:confirmed|verified|observed|reproduced|established|measured|"
    r"root cause is|failure occurs|is not involved|not involved|answer\s+is|"
    r"tests? (?:pass|passes|passed|fail|fails|failed))\b|\banswer\s*:)",
    re.I,
)
_DATABASE_VALUE = re.compile(
    r"\b(?:database|db)(?:\s+(?:engine|backend))?\s+"
    r"(?:(?:must|shall)\s+)?(?:is|be|use|using|stays|remains)\s+"
    r"(?P<value>[A-Za-z][\w.-]*)",
    re.I,
)
_CORRECTION_OPPOSITES = (
    ("enable", "disable"),
    ("enabled", "disabled"),
    ("allow", "forbid"),
    ("allowed", "forbidden"),
    ("include", "exclude"),
    ("included", "excluded"),
)
_CORRECTION_GRAMMAR = {
    "actually",
    "correction",
    "instead",
    "must",
    "rather",
    "requirement",
    "shall",
    "should",
    "than",
}


def _span_identity(item: MemoryItem) -> set[tuple[str, int, int]]:
    return {(span.source_id, span.start, span.end) for span in item.provenance}


def _candidate_retained(
    candidate: MemoryItem,
    retained: Iterable[MemoryItem],
    *,
    work_budget: _CompilationWorkBudget | None = None,
    phase: str = "protected retention verification",
) -> bool:
    for item in retained:
        if work_budget is not None:
            work_budget.consume(1, phase=phase)
        if item.status == MemoryStatus.DISCARDED:
            continue
        if memory_item_covers_candidate(candidate, item):
            return True
    return False


_DEFAULT_CANDIDATE_RETAINED = _candidate_retained
_DEFAULT_MEMORY_ITEM_COVERS_CANDIDATE = memory_item_covers_candidate
_EXACT_MEMORY_ITEM_DESCRIPTORS = tuple(
    (name, MemoryItem.__dict__[name])
    for name in ("kind", "text", "provenance", "status", "exact")
)
_EXACT_PROVENANCE_DESCRIPTORS = tuple(
    (name, ProvenanceSpan.__dict__[name])
    for name in ("source_id", "start", "end")
)
_DEFAULT_MEMORY_ITEM_GETATTRIBUTE = MemoryItem.__getattribute__
_DEFAULT_PROVENANCE_GETATTRIBUTE = ProvenanceSpan.__getattribute__
_DEFAULT_MEMORY_KIND_HASH = MemoryKind.__hash__
_DEFAULT_MEMORY_KIND_EQ = MemoryKind.__eq__
_DEFAULT_MEMORY_KIND_NE = MemoryKind.__ne__
_DEFAULT_MEMORY_STATUS_EQ = MemoryStatus.__eq__


def _exact_protected_retention(
    protected_candidates: list[MemoryItem],
    retained: list[MemoryItem],
) -> list[bool] | None:
    """Index intrinsically exact public verification without changing work.

    The compiler supplies a work budget and deliberately retains its legacy
    scan/callback order.  The independent public verifier normally does not;
    for exact built-in atoms, the wrapper relation is impossible and coverage
    reduces to kind/text equality plus provenance-span containment.
    """

    if (
        type(protected_candidates) is not list
        or type(retained) is not list
        or _candidate_retained is not _DEFAULT_CANDIDATE_RETAINED
        or memory_item_covers_candidate
        is not _DEFAULT_MEMORY_ITEM_COVERS_CANDIDATE
        or any(
            MemoryItem.__dict__.get(name) is not descriptor
            for name, descriptor in _EXACT_MEMORY_ITEM_DESCRIPTORS
        )
        or MemoryItem.__getattribute__
        is not _DEFAULT_MEMORY_ITEM_GETATTRIBUTE
        or ProvenanceSpan.__getattribute__
        is not _DEFAULT_PROVENANCE_GETATTRIBUTE
        or MemoryKind.__hash__ is not _DEFAULT_MEMORY_KIND_HASH
        or MemoryKind.__eq__ is not _DEFAULT_MEMORY_KIND_EQ
        or MemoryKind.__ne__ is not _DEFAULT_MEMORY_KIND_NE
        or MemoryStatus.__eq__ is not _DEFAULT_MEMORY_STATUS_EQ
        or any(
            ProvenanceSpan.__dict__.get(name) is not descriptor
            for name, descriptor in _EXACT_PROVENANCE_DESCRIPTORS
        )
    ):
        return None

    candidates_by_key: dict[
        tuple[MemoryKind, str],
        list[tuple[int, frozenset[tuple[str, int, int]]]],
    ] = {}
    candidate_snapshots: list[
        tuple[
            MemoryItem,
            MemoryKind,
            str,
            bool,
            tuple[tuple[str, int, int], ...],
        ]
    ] = []
    for index, candidate in enumerate(protected_candidates):
        if (
            type(candidate) is not MemoryItem
            or candidate.exact is not True
            or type(candidate.kind) is not MemoryKind
            or type(candidate.text) is not str
            or type(candidate.provenance) is not list
            or any(
                type(span) is not ProvenanceSpan
                or type(span.source_id) is not str
                or type(span.start) is not int
                or type(span.end) is not int
                for span in candidate.provenance
            )
        ):
            return None
        spans = frozenset(
            (span.source_id, span.start, span.end)
            for span in candidate.provenance
        )
        candidate_snapshots.append(
            (
                candidate,
                candidate.kind,
                candidate.text,
                candidate.exact,
                tuple(
                    (span.source_id, span.start, span.end)
                    for span in candidate.provenance
                ),
            )
        )
        candidates_by_key.setdefault(
            (candidate.kind, candidate.text.strip()), []
        ).append((index, spans))

    found = [False] * len(protected_candidates)
    remaining = len(found)
    retained_snapshots: list[
        tuple[
            MemoryItem,
            MemoryKind,
            str,
            MemoryStatus,
            tuple[tuple[str, int, int], ...],
        ]
    ] = []
    for item in retained:
        if (
            type(item) is not MemoryItem
            or type(item.kind) is not MemoryKind
            or type(item.text) is not str
            or type(item.provenance) is not list
            or type(item.status) is not MemoryStatus
            or any(
                type(span) is not ProvenanceSpan
                or type(span.source_id) is not str
                or type(span.start) is not int
                or type(span.end) is not int
                for span in item.provenance
            )
        ):
            return None
        retained_snapshots.append(
            (
                item,
                item.kind,
                item.text,
                item.status,
                tuple(
                    (span.source_id, span.start, span.end)
                    for span in item.provenance
                ),
            )
        )
        if item.status == MemoryStatus.DISCARDED:
            continue
        candidates = candidates_by_key.get((item.kind, item.text.strip()))
        if not candidates:
            continue
        item_spans = {
            (span.source_id, span.start, span.end)
            for span in item.provenance
        }
        for candidate_index, candidate_spans in candidates:
            if not found[candidate_index] and candidate_spans <= item_spans:
                found[candidate_index] = True
                remaining -= 1
        if remaining == 0:
            break

    if any(
        candidate.kind is not kind
        or candidate.text != text
        or candidate.exact is not exact
        or type(candidate.provenance) is not list
        or tuple(
            (span.source_id, span.start, span.end)
            for span in candidate.provenance
        )
        != spans
        for candidate, kind, text, exact, spans in candidate_snapshots
    ) or any(
        item.kind is not kind
        or item.text != text
        or item.status is not status
        or type(item.provenance) is not list
        or tuple(
            (span.source_id, span.start, span.end)
            for span in item.provenance
        )
        != spans
        for item, kind, text, status, spans in retained_snapshots
    ):
        return None

    if (
        _candidate_retained is not _DEFAULT_CANDIDATE_RETAINED
        or memory_item_covers_candidate
        is not _DEFAULT_MEMORY_ITEM_COVERS_CANDIDATE
        or any(
            MemoryItem.__dict__.get(name) is not descriptor
            for name, descriptor in _EXACT_MEMORY_ITEM_DESCRIPTORS
        )
        or any(
            ProvenanceSpan.__dict__.get(name) is not descriptor
            for name, descriptor in _EXACT_PROVENANCE_DESCRIPTORS
        )
        or MemoryItem.__getattribute__
        is not _DEFAULT_MEMORY_ITEM_GETATTRIBUTE
        or ProvenanceSpan.__getattribute__
        is not _DEFAULT_PROVENANCE_GETATTRIBUTE
        or MemoryKind.__hash__ is not _DEFAULT_MEMORY_KIND_HASH
        or MemoryKind.__eq__ is not _DEFAULT_MEMORY_KIND_EQ
        or MemoryKind.__ne__ is not _DEFAULT_MEMORY_KIND_NE
        or MemoryStatus.__eq__ is not _DEFAULT_MEMORY_STATUS_EQ
    ):
        return None
    return found


_DEFAULT_EXACT_PROTECTED_RETENTION = _exact_protected_retention


def _unsupported_claim_terms(item: MemoryItem) -> set[str]:
    """Return terms not jointly supported by any one atomic source span."""

    claim = set(_support_tokens(item.text))
    if not claim:
        if any(item.text.strip() in span.quote for span in item.provenance):
            return set()
        return {"<nonword-claim-without-literal-support>"}
    missing_by_span = []
    for span in item.provenance:
        evidence = set(_support_tokens(span.quote))
        missing = claim - evidence
        if not missing:
            return set()
        missing_by_span.append(missing)
    return min(missing_by_span, key=lambda missing: (len(missing), sorted(missing)), default=claim)


def _support_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for raw_token in _SUPPORT_TOKEN.findall(text):
        token = raw_token.casefold().strip("._-")
        if token and token not in _SUPPORT_STOPWORDS:
            tokens.append(token)
    return tokens


def _ordered_supports(item: MemoryItem) -> bool:
    claim = _support_tokens(item.text)
    if not claim:
        return any(item.text.strip() in span.quote for span in item.provenance)
    for span in item.provenance:
        position = 0
        for token in _support_tokens(span.quote):
            if token == claim[position]:
                position += 1
                if position == len(claim):
                    return True
    return False


def _source_can_assert_fact(
    source: SourceRecord,
    *,
    untrusted_historical_roles: bool = False,
) -> bool:
    if untrusted_historical_roles and source_is_untrusted_historical(source):
        return False
    return (
        source.role.casefold() in _NON_TOOL_ROLES
        or source.metadata.get("trusted_for_state") is True
    )


def _span_has_confirmation(source: SourceRecord, span_start: int, span_end: int) -> bool:
    line_start = source.content.rfind("\n", 0, span_start) + 1
    line_end = source.content.find("\n", span_end)
    if line_end < 0:
        line_end = len(source.content)
    line = source.content[line_start:line_end]
    explicit_label = r"\s*(?:confirmed[_ ]?)?facts?\s*:"
    if _CONFIRMATION_EVIDENCE.search(line) or re.match(explicit_label, line, re.I):
        return True
    previous_end = max(0, line_start - 1)
    previous_start = source.content.rfind("\n", 0, previous_end) + 1
    previous_line = source.content[previous_start:previous_end].strip()
    return bool(re.fullmatch(explicit_label, previous_line, re.I))


def _has_atomic_literal_support(
    item: MemoryItem,
    source_map: dict[str, SourceRecord],
) -> bool:
    return any(
        item.text.strip() == span.quote.strip()
        and span.source_id in source_map
        and provenance_span_is_atomic(source_map[span.source_id], span)
        for span in item.provenance
    )


def _valid_resolution_edge(resolution: MemoryItem, question: MemoryItem) -> bool:
    if resolution.kind != MemoryKind.CONFIRMED_FACT:
        return False
    if question.kind != MemoryKind.UNRESOLVED:
        return False
    if resolution.source_sequence <= question.source_sequence:
        return False
    if not _POSITIVE_CONFIRMATION.search(resolution.text):
        return False
    resolution_roles = {
        role.strip().casefold()
        for role in str(resolution.metadata.get("source_role", "")).split(",")
        if role.strip()
    }
    if not resolution_roles or not resolution_roles <= _NON_TOOL_ROLES:
        return False
    resolution_tokens = set(_support_tokens(resolution.text))
    question_tokens = set(_support_tokens(question.text))
    overlap = resolution_tokens & question_tokens
    if len(overlap) < 2:
        return False
    score = len(overlap) / max(1, min(len(resolution_tokens), len(question_tokens)))
    return score >= 0.55


def _valid_correction_derived(
    item: MemoryItem,
    items: list[MemoryItem],
    *,
    work_budget: _CompilationWorkBudget | None = None,
) -> bool:
    if item.kind == MemoryKind.USER_CORRECTION:
        return False
    item_spans = _span_identity(item)
    for candidate in items:
        if work_budget is not None:
            work_budget.consume(
                1,
                phase="correction-derived verification",
            )
        if (
            candidate.kind == MemoryKind.USER_CORRECTION
            and candidate.source_sequence == item.source_sequence
            and item_spans <= _span_identity(candidate)
            and (
                _CORRECTION_MARKER.search(candidate.text)
                or "explicit-correction-label" in candidate.tags
            )
        ):
            return True
    return False


def _structured_labeled_correction(correction: MemoryItem, prior: MemoryItem) -> bool:
    """Independently validate one-token-overlap typed replacements."""

    correction_tokens = set(_support_tokens(correction.text)) - _CORRECTION_GRAMMAR
    prior_tokens = set(_support_tokens(prior.text)) - _CORRECTION_GRAMMAR
    correction_db = _DATABASE_VALUE.search(correction.text)
    prior_db = _DATABASE_VALUE.search(prior.text)
    if correction_db and prior_db:
        correction_value = correction_db.group("value").casefold().strip(".,;:")
        prior_value = prior_db.group("value").casefold().strip(".,;:")
        correction_base = correction_tokens - {
            correction_value,
            "database",
            "db",
            "engine",
            "backend",
        }
        prior_base = prior_tokens - {
            prior_value,
            "database",
            "db",
            "engine",
            "backend",
        }
        return correction_value != prior_value and correction_base == prior_base
    for first, second in _CORRECTION_OPPOSITES:
        opposed = (first in correction_tokens and second in prior_tokens) or (
            second in correction_tokens and first in prior_tokens
        )
        correction_base = correction_tokens - {first, second}
        prior_base = prior_tokens - {first, second}
        if opposed and correction_base and correction_base == prior_base:
            return True
    return False


def _valid_internal_conflict(
    item: MemoryItem,
    item_map: dict[str, MemoryItem],
) -> bool:
    if "detected-conflict" not in item.tags:
        return False
    if item.kind != MemoryKind.UNRESOLVED or len(item.conflicts_with) != 2:
        return False
    left = item_map.get(item.conflicts_with[0])
    right = item_map.get(item.conflicts_with[1])
    if left is None or right is None:
        return False
    if left.status != MemoryStatus.CONFLICTING or right.status != MemoryStatus.CONFLICTING:
        return False
    if right.id not in left.conflicts_with or left.id not in right.conflicts_with:
        return False
    expected = {
        f'Unresolved source conflict: "{left.text}" versus "{right.text}"',
        f'Unresolved source conflict: "{right.text}" versus "{left.text}"',
    }
    cited = _span_identity(item)
    required = _span_identity(left) | _span_identity(right)
    return item.text in expected and required <= cited


def verify_memory(
    *,
    sources: list[SourceRecord],
    items: list[MemoryItem],
    selected_item_ids: list[str],
    protected_candidates: list[MemoryItem],
    recovered_items: int,
    budget_overflow: int = 0,
    compression_target_met: bool = True,
    initial_issues: Iterable[VerificationIssue] = (),
    work_budget: _CompilationWorkBudget | None = None,
    untrusted_historical_roles: bool = False,
) -> VerificationReport:
    """Verify structural loss-resistance independently of extraction."""

    if not isinstance(untrusted_historical_roles, bool):
        raise TypeError("untrusted_historical_roles must be a boolean")
    issues = list(initial_issues)
    source_map = {source.id: source for source in sources}
    item_map = {item.id: item for item in items}
    selected_ids = set(selected_item_ids)
    retained = list(items)
    provenance_valid = 0
    provenance_total = 0

    if len(item_map) != len(items):
        issues.append(
            VerificationIssue(
                code="duplicate_item_id",
                severity=IssueSeverity.ERROR,
                message="The typed ledger contains duplicate item ids.",
            )
        )
    if len(selected_ids) != len(selected_item_ids):
        issues.append(
            VerificationIssue(
                code="duplicate_selected_item_id",
                severity=IssueSeverity.ERROR,
                message="The active selection contains duplicate item ids.",
            )
        )
    for missing_id in sorted(selected_ids - set(item_map)):
        issues.append(
            VerificationIssue(
                code="selected_item_missing",
                severity=IssueSeverity.ERROR,
                message="A selected id is absent from the typed ledger.",
                item_id=missing_id,
            )
        )

    for item in items:
        if item.id in selected_ids and item.status == MemoryStatus.SUPERSEDED:
            issues.append(
                VerificationIssue(
                    code="selected_superseded_item",
                    severity=IssueSeverity.ERROR,
                    message=(
                        "Superseded state is audit history and cannot enter a "
                        "verified execution prompt."
                    ),
                    item_id=item.id,
                )
            )
        if item.protected and item.status == MemoryStatus.DISCARDED:
            issues.append(
                VerificationIssue(
                    code="protected_item_discarded",
                    severity=IssueSeverity.ERROR,
                    message="Protected task state cannot be marked discarded.",
                    item_id=item.id,
                )
            )
        if not item.provenance:
            issues.append(
                VerificationIssue(
                    code="claim_without_provenance",
                    severity=IssueSeverity.ERROR,
                    message="Memory claim has no source span.",
                    item_id=item.id,
                )
            )
        for span in item.provenance:
            provenance_total += 1
            if span.validates(source_map):
                provenance_valid += 1
            else:
                issues.append(
                    VerificationIssue(
                        code="invalid_provenance",
                        severity=IssueSeverity.ERROR,
                        message="Source span, quote, or hash does not match the immutable source.",
                        item_id=item.id,
                        source_id=span.source_id,
                    )
                )
        cited_sources = [
            source_map[span.source_id]
            for span in item.provenance
            if span.source_id in source_map
        ]
        expected_roles = {source.role.casefold() for source in cited_sources}
        claimed_role_value = item.metadata.get("source_role")
        claimed_roles = (
            {
                role.strip().casefold()
                for role in claimed_role_value.split(",")
                if role.strip()
            }
            if isinstance(claimed_role_value, str)
            else set()
        )
        if claimed_roles != expected_roles:
            issues.append(
                VerificationIssue(
                    code="source_role_metadata_mismatch",
                    severity=IssueSeverity.ERROR,
                    message="Rendered source roles do not match the cited immutable sources.",
                    item_id=item.id,
                )
            )
        claimed_sequence = item.metadata.get("source_sequence")
        expected_sequence = max(
            (source.sequence for source in cited_sources),
            default=-1,
        )
        if (
            isinstance(claimed_sequence, bool)
            or not isinstance(claimed_sequence, int)
            or claimed_sequence != expected_sequence
        ):
            issues.append(
                VerificationIssue(
                    code="source_sequence_metadata_mismatch",
                    severity=IssueSeverity.ERROR,
                    message="Item sequence metadata does not match its cited sources.",
                    item_id=item.id,
                )
            )
        for target_id in item.supersedes:
            target = item_map.get(target_id)
            if target is None or target_id == item.id:
                issues.append(
                    VerificationIssue(
                        code="invalid_supersession_edge",
                        severity=IssueSeverity.ERROR,
                        message="Supersession edge is missing its target or is self-referential.",
                        item_id=item.id,
                    )
                )
            elif (
                target.status != MemoryStatus.SUPERSEDED
                or item.source_sequence <= target.source_sequence
            ):
                issues.append(
                    VerificationIssue(
                        code="invalid_supersession_order",
                        severity=IssueSeverity.ERROR,
                        message="Supersession must point backward to superseded state.",
                        item_id=item.id,
                    )
                )
        if item.status == MemoryStatus.CONFLICTING:
            valid_targets = [item_map.get(target_id) for target_id in item.conflicts_with]
            if not valid_targets or any(
                target is None
                or target.status != MemoryStatus.CONFLICTING
                or item.id not in target.conflicts_with
                for target in valid_targets
            ):
                issues.append(
                    VerificationIssue(
                        code="invalid_conflict_edge",
                        severity=IssueSeverity.ERROR,
                        message="Conflicting state must have a symmetric conflict edge.",
                        item_id=item.id,
                    )
                )
        if item.kind in _AUTHORITY_GATED_KINDS:
            unauthorized = [
                span.source_id
                for span in item.provenance
                if span.source_id not in source_map
                or source_map[span.source_id].role.casefold()
                not in _AUTHORITATIVE_COMMITMENT_ROLES
            ]
            if unauthorized:
                issues.append(
                    VerificationIssue(
                        code="unauthorized_commitment_source",
                        severity=IssueSeverity.ERROR,
                        message=(
                            f"{item.kind.value} cites a non-authoritative source role: "
                            + ", ".join(sorted(set(unauthorized)))
                        ),
                        item_id=item.id,
                    )
                )
        if item.kind in _NON_TOOL_KINDS:
            unauthorized = [
                span.source_id
                for span in item.provenance
                if span.source_id not in source_map
                or source_map[span.source_id].role.casefold() not in _NON_TOOL_ROLES
                or (
                    untrusted_historical_roles
                    and source_is_untrusted_historical(source_map[span.source_id])
                )
            ]
            if unauthorized:
                issues.append(
                    VerificationIssue(
                        code="unauthorized_state_source",
                        severity=IssueSeverity.ERROR,
                        message=(
                            f"{item.kind.value} cites tool output as authoritative state: "
                            + ", ".join(sorted(set(unauthorized)))
                        ),
                        item_id=item.id,
                    )
                )
        if item.kind == MemoryKind.CONFIRMED_FACT:
            unauthorized_facts = [
                span.source_id
                for span in item.provenance
                if span.source_id not in source_map
                or not _source_can_assert_fact(
                    source_map[span.source_id],
                    untrusted_historical_roles=untrusted_historical_roles,
                )
            ]
            if unauthorized_facts:
                issues.append(
                    VerificationIssue(
                        code="unauthorized_fact_source",
                        severity=IssueSeverity.ERROR,
                        message=(
                            "confirmed_fact cites an untrusted state source: "
                            + ", ".join(sorted(set(unauthorized_facts)))
                        ),
                        item_id=item.id,
                    )
                )
        must_be_exact = item.exact or item.kind in _INTRINSICALLY_EXACT_KINDS
        if item.kind in _INTRINSICALLY_EXACT_KINDS and not item.exact:
            issues.append(
                VerificationIssue(
                    code="intrinsic_exactness_disabled",
                    severity=IssueSeverity.ERROR,
                    message=f"{item.kind.value} cannot disable exact literal verification.",
                    item_id=item.id,
                )
            )
        if must_be_exact:
            mismatched = [
                span for span in item.provenance if item.text.strip() != span.quote.strip()
            ]
            if mismatched:
                issues.append(
                    VerificationIssue(
                        code="exact_literal_changed",
                        severity=IssueSeverity.ERROR,
                        message="An exact item differs from its source literal.",
                        item_id=item.id,
                        source_id=mismatched[0].source_id,
                    )
                )
        if item.kind == MemoryKind.CONFIRMED_FACT:
            quote = " ".join(span.quote for span in item.provenance)
            if _UNCERTAINTY.search(quote):
                issues.append(
                    VerificationIssue(
                        code="unresolved_promoted_to_fact",
                        severity=IssueSeverity.ERROR,
                        message="Uncertain source text was emitted as a confirmed fact.",
                        item_id=item.id,
                    )
                )
            unsupported_fact_spans = [
                span
                for span in item.provenance
                if span.source_id not in source_map
                or not _span_has_confirmation(
                    source_map[span.source_id],
                    span.start,
                    span.end,
                )
            ]
            if unsupported_fact_spans:
                issues.append(
                    VerificationIssue(
                        code="fact_without_confirmation_evidence",
                        severity=IssueSeverity.ERROR,
                        message="confirmed_fact lacks explicit confirmation evidence.",
                        item_id=item.id,
                    )
                )
        valid_internal_conflict = _valid_internal_conflict(item, item_map)
        if "detected-conflict" in item.tags and not valid_internal_conflict:
            issues.append(
                VerificationIssue(
                    code="invalid_internal_conflict",
                    severity=IssueSeverity.ERROR,
                    message="Reserved detected-conflict tag lacks a valid conflict graph.",
                    item_id=item.id,
                )
            )
        if (
            not valid_internal_conflict
            and item.kind not in _INTRINSICALLY_EXACT_KINDS
            and not _has_atomic_literal_support(item, source_map)
        ):
            issues.append(
                VerificationIssue(
                    code="claim_not_atomic_source_literal",
                    severity=IssueSeverity.ERROR,
                    message="Claim must equal a complete atomic source span.",
                    item_id=item.id,
                )
            )
        correction_state_tags = {"correction-derived", "current-value"} & set(item.tags)
        if correction_state_tags and (
            correction_state_tags != {"correction-derived", "current-value"}
            or not _valid_correction_derived(
                item,
                items,
                work_budget=work_budget,
            )
        ):
            issues.append(
                VerificationIssue(
                    code="invalid_correction_derived_state",
                    severity=IssueSeverity.ERROR,
                    message="Correction-derived state lacks its same-source correction edge.",
                    item_id=item.id,
                )
            )
        if "resolves-protected" in item.tags:
            resolved_questions = [
                item_map[target_id]
                for target_id in item.supersedes
                if target_id in item_map
            ]
            if not resolved_questions or any(
                not _valid_resolution_edge(item, question)
                for question in resolved_questions
            ):
                issues.append(
                    VerificationIssue(
                        code="invalid_protected_resolution",
                        severity=IssueSeverity.ERROR,
                        message="Protected resolution tag lacks a supported question-answer edge.",
                        item_id=item.id,
                    )
                )
        if not valid_internal_conflict:
            unsupported = _unsupported_claim_terms(item)
            if unsupported:
                issues.append(
                    VerificationIssue(
                        code="unsupported_claim_terms",
                        severity=IssueSeverity.ERROR,
                        message=(
                            "Claim contains terms absent from every cited source span: "
                            + ", ".join(sorted(unsupported))
                        ),
                        item_id=item.id,
                    )
                )
            if not _ordered_supports(item):
                issues.append(
                    VerificationIssue(
                        code="claim_order_not_supported",
                        severity=IssueSeverity.ERROR,
                        message="Claim token order is not supported by its cited source spans.",
                        item_id=item.id,
                    )
                )
            evidence_polarities = {
                bool(_NEGATION.search(span.quote)) for span in item.provenance
            }
            if len(evidence_polarities) > 1:
                issues.append(
                    VerificationIssue(
                        code="mixed_evidence_polarity",
                        severity=IssueSeverity.ERROR,
                        message="Claim cites source spans with incompatible polarity.",
                        item_id=item.id,
                    )
                )
            elif evidence_polarities:
                evidence_negated = next(iter(evidence_polarities))
                claim_negated = bool(_NEGATION.search(item.text))
                if evidence_negated != claim_negated:
                    issues.append(
                        VerificationIssue(
                            code="claim_polarity_changed",
                            severity=IssueSeverity.ERROR,
                            message="Claim changes the polarity of its cited source span.",
                            item_id=item.id,
                        )
                    )

    protected_by_span = list(protected_candidates)
    for item in items:
        if item.status != MemoryStatus.SUPERSEDED:
            continue
        superseders: list[MemoryItem] = []
        for candidate in items:
            if work_budget is not None:
                work_budget.consume(
                    1,
                    phase="supersession-edge verification",
                )
            if (
                item.id in candidate.supersedes
                and candidate.status
                in {MemoryStatus.ACTIVE, MemoryStatus.CONFLICTING}
            ):
                superseders.append(candidate)
        valid_correction = False
        valid_resolution = False
        for candidate in superseders:
            if work_budget is not None:
                work_budget.consume(
                    1,
                    phase="supersession-evidence verification",
                )
            if (
                not valid_correction
                and candidate.kind == MemoryKind.USER_CORRECTION
                and _candidate_retained(
                    candidate,
                    protected_by_span,
                    work_budget=work_budget,
                    phase="correction retention verification",
                )
                and candidate.source_sequence > item.source_sequence
                and (
                    _CORRECTION_MARKER.search(candidate.text)
                    or "explicit-correction-label" in candidate.tags
                )
                and (
                    len(
                        set(_support_tokens(candidate.text))
                        & set(_support_tokens(item.text))
                    )
                    >= 2
                    or (
                        "explicit-correction-label" in candidate.tags
                        and _structured_labeled_correction(
                            candidate,
                            item,
                        )
                    )
                    or (
                        _REVOCATION.search(candidate.text)
                        and bool(
                            set(_support_tokens(candidate.text))
                            & set(_support_tokens(item.text))
                        )
                    )
                )
            ):
                valid_correction = True
            if (
                not valid_resolution
                and "resolves-protected" in candidate.tags
                and _valid_resolution_edge(candidate, item)
            ):
                valid_resolution = True
        if not valid_correction and not valid_resolution:
            issues.append(
                VerificationIssue(
                    code="orphaned_superseded_item",
                    severity=IssueSeverity.ERROR,
                    message="Superseded state has no supported correction or resolution edge.",
                    item_id=item.id,
                )
            )

    exact_retention = (
        _DEFAULT_EXACT_PROTECTED_RETENTION(protected_candidates, retained)
        if (
            work_budget is None
            and _exact_protected_retention
            is _DEFAULT_EXACT_PROTECTED_RETENTION
        )
        else None
    )
    protected_retained = 0
    for candidate_index, candidate in enumerate(protected_candidates):
        candidate_is_retained = (
            exact_retention[candidate_index]
            if exact_retention is not None
            else _candidate_retained(
                candidate,
                retained,
                work_budget=work_budget,
            )
        )
        if candidate_is_retained:
            protected_retained += 1
        else:
            issues.append(
                VerificationIssue(
                    code="protected_commitment_missing",
                    severity=IssueSeverity.ERROR,
                    message=(
                        f"Protected {candidate.kind.value} was not retained "
                        "in the typed ledger."
                    ),
                    source_id=candidate.provenance[0].source_id,
                )
            )

    for item in items:
        if (
            item.protected
            and item.status not in {MemoryStatus.SUPERSEDED, MemoryStatus.DISCARDED}
            and item.id not in selected_ids
        ):
            issues.append(
                VerificationIssue(
                    code="active_protected_item_not_selected",
                    severity=IssueSeverity.ERROR,
                    message="An active protected item was removed from active context.",
                    item_id=item.id,
                )
            )

    if budget_overflow:
        issues.append(
            VerificationIssue(
                code="loss_resistant_budget_overflow",
                severity=IssueSeverity.WARNING,
                message=(
                    f"Loss-resistant active context exceeds the requested budget "
                    f"by {budget_overflow} estimated tokens; protected state was not dropped."
                ),
            )
        )
    if not compression_target_met:
        issues.append(
            VerificationIssue(
                code="compression_target_not_met",
                severity=IssueSeverity.WARNING,
                message=(
                    "The requested compression ratio is impossible without "
                    "dropping retained state."
                ),
            )
        )

    return VerificationReport(
        passed=not any(issue.severity == IssueSeverity.ERROR for issue in issues),
        issues=issues,
        protected_candidates=len(protected_candidates),
        protected_retained=protected_retained,
        provenance_valid=provenance_valid,
        provenance_total=provenance_total,
        recovered_items=recovered_items,
    )
