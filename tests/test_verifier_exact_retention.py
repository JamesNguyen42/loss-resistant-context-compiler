from __future__ import annotations

import pytest

import context_compiler.verifier as verifier
from context_compiler import RuleBasedExtractor, SourceRecord
from context_compiler.limits import _CompilationWorkBudget


def _fixture(count: int = 200) -> tuple[list[SourceRecord], list[object]]:
    sources: list[SourceRecord] = []
    items: list[object] = []
    extractor = RuleBasedExtractor()
    for index in range(count):
        source = SourceRecord.create(
            id=f"source-{index}",
            sequence=index,
            role="tool",
            content=f"exact error: E_{index:04d} at index {index:04d}",
        )
        sources.append(source)
        items.extend(extractor.extract([source]).items)
    return sources, items


def _verify(
    sources: list[SourceRecord],
    items: list[object],
    protected: list[object],
    *,
    work_budget: _CompilationWorkBudget | None = None,
) -> object:
    return verifier.verify_memory(
        sources=sources,
        items=items,
        selected_item_ids=[item.id for item in items],
        protected_candidates=protected,
        recovered_items=0,
        work_budget=work_budget,
    )


def test_exact_retention_matches_forced_legacy() -> None:
    sources, items = _fixture()
    protected = items[::5]
    fast = _verify(sources, items, protected).to_dict()
    original = verifier._DEFAULT_CANDIDATE_RETAINED
    verifier._DEFAULT_CANDIDATE_RETAINED = object()
    try:
        legacy = _verify(sources, items, protected).to_dict()
    finally:
        verifier._DEFAULT_CANDIDATE_RETAINED = original
    assert fast == legacy


def test_work_budget_always_uses_legacy_scan() -> None:
    sources, items = _fixture(10)
    protected = items[::2]
    budget = _CompilationWorkBudget(1)
    with pytest.raises(
        ValueError,
        match="during protected retention verification",
    ):
        _verify(sources, items, protected, work_budget=budget)
    assert budget.used == 1


def test_replaced_coverage_helper_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources, items = _fixture(10)
    protected = items[::2]
    calls: list[str] = []
    original = verifier.memory_item_covers_candidate

    def replacement(candidate: object, retained: object) -> bool:
        calls.append("coverage")
        return original(candidate, retained)

    monkeypatch.setattr(
        verifier,
        "memory_item_covers_candidate",
        replacement,
    )
    report = _verify(sources, items, protected)
    assert report.passed
    assert calls


def test_replaced_memory_kind_ne_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources, items = _fixture(1)
    monkeypatch.setattr(
        verifier.MemoryKind,
        "__ne__",
        lambda _self, _other: True,
    )

    report = _verify(sources, items, items)

    assert not report.passed
    assert [issue.code for issue in report.issues].count(
        "protected_commitment_missing"
    ) == 1


def test_missing_exact_candidate_matches_legacy() -> None:
    sources, items = _fixture(20)
    protected = items[::4]
    retained = items[1:]
    report = verifier.verify_memory(
        sources=sources,
        items=retained,
        selected_item_ids=[item.id for item in retained],
        protected_candidates=protected,
        recovered_items=0,
    )
    assert not report.passed
    assert sum(
        issue.code == "protected_commitment_missing"
        for issue in report.issues
    ) == 1
