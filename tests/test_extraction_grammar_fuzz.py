from __future__ import annotations

import itertools

import pytest

from context_compiler import (
    ContextCompiler,
    MemoryKind,
    MemoryStatus,
    SourceRecord,
)


def _source(
    sequence: int,
    content: str,
    *,
    role: str = "user",
) -> SourceRecord:
    return SourceRecord.create(
        id=f"grammar-{sequence}",
        sequence=sequence,
        role=role,
        content=content,
    )


def _matching_items(memory, kind: MemoryKind, text: str):
    return [
        item
        for item in memory.items
        if item.kind == kind and item.text == text
    ]


def _assert_exact_source_span(
    source: SourceRecord,
    item,
    expected: str,
) -> None:
    assert len(item.provenance) == 1
    span = item.provenance[0]
    assert span.source_id == source.id
    assert span.start == source.content.index(expected)
    assert span.end == span.start + len(expected)
    assert span.quote == expected
    assert span.validates({source.id: source})


_LABELED_VALUES = (
    ("GOAL", MemoryKind.GOAL, "Repair the authentication timeout"),
    ("goals", MemoryKind.GOAL, "Ship the deterministic release"),
    ("Constraint", MemoryKind.CONSTRAINT, "must preserve the public API"),
    ("requirements", MemoryKind.CONSTRAINT, "do not remove the audit log"),
    (
        "unresolved_questions",
        MemoryKind.UNRESOLVED,
        "Whether clock skew affects expiration",
    ),
    (
        "exact_errors",
        MemoryKind.EXACT_ERROR,
        "ValueError: refresh token missing",
    ),
    (
        "exact references",
        MemoryKind.EXACT_REFERENCE,
        "src/auth/token.py:42",
    ),
    ("DECISION", MemoryKind.DECISION, "Use the bounded retry queue"),
    (
        "confirmed_facts",
        MemoryKind.CONFIRMED_FACT,
        "Verified refresh completed successfully",
    ),
    (
        "failed approaches",
        MemoryKind.DISCARDED_ATTEMPT,
        "Restarting the worker did not help",
    ),
    ("progress", MemoryKind.PROGRESS, "Implemented the retry guard"),
    ("context", MemoryKind.CONTEXT, "Deployment platform is Linux"),
)
_BULLETS = ("- ", "* ", "+ ", "1. ", "2) ")


@pytest.mark.parametrize(
    ("label", "kind", "value", "bullet"),
    [
        pytest.param(label, kind, value, bullet, id=f"{label}-{bullet.strip()}")
        for (label, kind, value), bullet in itertools.product(
            _LABELED_VALUES,
            _BULLETS,
        )
    ],
)
def test_label_and_bullet_grammar_preserves_typed_literal(
    label: str,
    kind: MemoryKind,
    value: str,
    bullet: str,
) -> None:
    source = _source(
        0,
        f"\t{label}\t :\n  {bullet}{value}  ",
    )

    memory = ContextCompiler().compile([source])
    matching = _matching_items(memory, kind, value)

    assert memory.verification.passed
    assert len(matching) == 1
    _assert_exact_source_span(source, matching[0], value)
    if matching[0].protected:
        assert matching[0].id in memory.selected_item_ids


_LIMIT_SUBJECTS = (
    "request timeout",
    "retry count",
    "payload size",
    "cache TTL",
)
_LIMIT_FORMS = (
    "{subject} must not exceed {number} {unit}.",
    "{subject} shall not exceed {number} {unit}.",
    "Never allow {subject} above {number} {unit}.",
    "{subject} is limited to at most {number} {unit}.",
)
_LIMIT_VALUES = (
    ("0", "retries"),
    ("1.5", "seconds"),
    ("30", "ms"),
    ("64", "MiB"),
)


@pytest.mark.parametrize(
    ("subject", "form", "number", "unit"),
    [
        pytest.param(
            subject,
            form,
            number,
            unit,
            id=(
                f"limit-{subject_index}-{form_index}-{value_index}-"
                f"{subject.replace(' ', '-')}"
            ),
        )
        for subject_index, subject in enumerate(_LIMIT_SUBJECTS)
        for form_index, form in enumerate(_LIMIT_FORMS)
        for value_index, (number, unit) in enumerate(_LIMIT_VALUES)
    ],
)
def test_negated_numeric_unit_constraint_grammar_is_not_weakened(
    subject: str,
    form: str,
    number: str,
    unit: str,
) -> None:
    value = form.format(
        subject=subject,
        number=number,
        unit=unit,
    )
    source = _source(0, f"constraint: {value}")

    memory = ContextCompiler().compile([source])
    matching = _matching_items(memory, MemoryKind.CONSTRAINT, value)

    assert memory.verification.passed
    assert len(matching) == 1
    assert number in matching[0].text
    assert unit in matching[0].text
    _assert_exact_source_span(source, matching[0], value)
    assert not any(
        item.kind == MemoryKind.CONFIRMED_FACT for item in memory.items
    )


_CONJUNCTION_CLAUSES = (
    (
        "The public API must remain stable",
        "the audit log must remain enabled.",
    ),
    (
        "Do not remove tracing",
        "do not weaken authentication.",
    ),
    (
        "Cache TTL shall not exceed 30 seconds",
        "retry count shall not exceed 3 attempts.",
    ),
    (
        "Preserve Unicode source IDs",
        "preserve exact byte digests.",
    ),
)
_CONJUNCTIONS = (" and ", " AND ", "  AnD  ", "\tand\t")


@pytest.mark.parametrize(
    ("left", "conjunction", "right"),
    [
        pytest.param(
            left,
            conjunction,
            right,
            id=f"conjunction-{clause_index}-{conjunction_index}",
        )
        for clause_index, (left, right) in enumerate(_CONJUNCTION_CLAUSES)
        for conjunction_index, conjunction in enumerate(_CONJUNCTIONS)
    ],
)
def test_conjunction_grammar_keeps_independent_constraint_atoms(
    left: str,
    conjunction: str,
    right: str,
) -> None:
    source = _source(0, f"constraint: {left}{conjunction}{right}")

    memory = ContextCompiler().compile([source])
    constraints = [
        item
        for item in memory.items
        if item.kind == MemoryKind.CONSTRAINT
    ]
    constraints_by_text = {item.text: item for item in constraints}

    assert memory.verification.passed
    assert set(constraints_by_text) == {left, right}
    _assert_exact_source_span(source, constraints_by_text[left], left)
    _assert_exact_source_span(source, constraints_by_text[right], right)
    assert all(
        item.id in memory.selected_item_ids for item in constraints
    )


def _reference_cases() -> list[object]:
    cases = []
    for index, component in enumerate(
        ("auth", "cache", "worker", "api", "δοκιμή")
    ):
        line = 10 + index
        end = line + 7
        values = (
            f"src/{component}/token_{index}.py lines {line}-{end}",
            f"src/{component}/token_{index}.py#L{line}-L{end}",
            f"/workspace/src/{component}/token_{index}.py:{line}-{end}",
            rf"C:\Repo\src\{component}\token_{index}.py:{line}-{end}",
            rf"D:\Repo {index}\src\{component} token.py:{line}-{end}",
            (
                f"tests/test_{component}.py::"
                f"TestGrammar::test_case[param-{index}]"
            ),
        )
        cases.extend(
            pytest.param(
                f"Inspect {value} before editing.",
                value,
                id=f"reference-{index}-{variant}",
            )
            for variant, value in enumerate(values)
        )
    return cases


@pytest.mark.parametrize(("content", "expected"), _reference_cases())
def test_path_locator_grammar_preserves_complete_exact_reference(
    content: str,
    expected: str,
) -> None:
    source = _source(0, content, role="assistant")

    memory = ContextCompiler().compile([source])
    matching = _matching_items(memory, MemoryKind.EXACT_REFERENCE, expected)

    assert memory.verification.passed
    assert len(matching) == 1
    assert matching[0].exact
    assert matching[0].id in memory.selected_item_ids
    _assert_exact_source_span(source, matching[0], expected)


_DIAGNOSTIC_FORMS = (
    "E   AssertionError: expected {left} ms, got {right} ms",
    "Expected: {left} MiB; Received: {right} MiB",
    "TS2322: Type 'string' is not assignable to type 'number' ({left})",
    "error[E0308]: expected u{left}, found i{right}",
    "ValueError: retry count {left} exceeded limit {right}",
    "exit code {left} after worker attempt {right}",
    "{left} tests failed in {right}.25s",
)


@pytest.mark.parametrize(
    ("diagnostic",),
    [
        pytest.param(
            form.format(left=left, right=right),
            id=f"diagnostic-{form_index}-{value_index}",
        )
        for form_index, form in enumerate(_DIAGNOSTIC_FORMS)
        for value_index, (left, right) in enumerate(
            ((1, 2), (17, 16), (64, 128), (137, 3))
        )
    ],
)
def test_diagnostic_grammar_preserves_numbers_units_and_full_literal(
    diagnostic: str,
) -> None:
    source = _source(0, diagnostic, role="tool")

    memory = ContextCompiler().compile([source])
    matching = _matching_items(
        memory,
        MemoryKind.EXACT_ERROR,
        diagnostic,
    )

    assert memory.verification.passed
    assert len(matching) == 1
    assert matching[0].exact
    assert matching[0].id in memory.selected_item_ids
    _assert_exact_source_span(source, matching[0], diagnostic)


_CORRECTION_FORMS = (
    (
        "Actually, worker timeout must be {new} seconds "
        "instead of {old} seconds."
    ),
    (
        "Correction: worker timeout must be {new} seconds "
        "rather than {old} seconds."
    ),
    (
        "To clarify, worker timeout changed from {old} seconds "
        "to {new} seconds and must remain fixed."
    ),
    "I meant worker timeout must be {new} seconds, not {old} seconds.",
)
_CORRECTION_VALUES = (
    (10, 20),
    (15, 30),
    (30, 45),
    (45, 60),
    (60, 90),
    (90, 120),
)


@pytest.mark.parametrize(
    ("form", "old", "new"),
    [
        pytest.param(
            form,
            old,
            new,
            id=f"correction-{form_index}-{old}-to-{new}",
        )
        for form_index, form in enumerate(_CORRECTION_FORMS)
        for old, new in _CORRECTION_VALUES
    ],
)
def test_correction_grammar_supersedes_exactly_one_prior_constraint(
    form: str,
    old: int,
    new: int,
) -> None:
    original = _source(
        0,
        f"constraint: Worker timeout must be {old} seconds.",
    )
    correction_text = form.format(old=old, new=new)
    correction = _source(1, correction_text)

    memory = ContextCompiler().compile([original, correction])
    original_constraints = [
        item
        for item in memory.items
        if item.kind == MemoryKind.CONSTRAINT
        and item.source_sequence == 0
    ]
    corrections = [
        item
        for item in memory.items
        if item.kind == MemoryKind.USER_CORRECTION
        and item.source_sequence == 1
    ]
    replacements = [
        item
        for item in memory.items
        if item.kind == MemoryKind.CONSTRAINT
        and item.source_sequence == 1
        and "current-value" in item.tags
    ]

    assert memory.verification.passed
    assert len(original_constraints) == 1
    assert original_constraints[0].status == MemoryStatus.SUPERSEDED
    assert original_constraints[0].id not in memory.selected_item_ids
    assert len(corrections) == 1
    assert corrections[0].supersedes == [original_constraints[0].id]
    assert len(replacements) == 1
    assert replacements[0].status == MemoryStatus.ACTIVE
    assert replacements[0].supersedes == [original_constraints[0].id]
    assert replacements[0].id in memory.selected_item_ids
    expected_correction = correction_text.removeprefix("Correction: ")
    assert corrections[0].text == expected_correction
    assert replacements[0].text == expected_correction
    _assert_exact_source_span(
        correction,
        corrections[0],
        expected_correction,
    )
    _assert_exact_source_span(
        correction,
        replacements[0],
        expected_correction,
    )
