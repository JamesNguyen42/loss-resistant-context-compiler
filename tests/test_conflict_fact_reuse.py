from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

import context_compiler.resolver as resolver_module
from context_compiler.limits import (
    CompilationLimitError,
    CompilationLimits,
    _CompilationWorkBudget,
)
from context_compiler.models import (
    MemoryItem,
    MemoryKind,
    ProvenanceSpan,
    SourceRecord,
)
from context_compiler.resolver import mark_conflicts


def _item(index: int, text: str, kind: MemoryKind) -> MemoryItem:
    source = SourceRecord.create(
        id=f"conflict-source-{index:04d}",
        sequence=index,
        role="user",
        content=text,
    )
    return MemoryItem(
        id=f"conflict-item-{index:04d}",
        kind=kind,
        text=text,
        provenance=[ProvenanceSpan.from_source(source, 0, len(text))],
        priority=80,
        metadata={"source_role": "user", "source_sequence": index},
    )


def _clone(items: list[MemoryItem]) -> list[MemoryItem]:
    return [MemoryItem.from_dict(item.to_dict()) for item in items]


def _resolve(
    items: list[MemoryItem],
    *,
    maximum_work: int = 2_000_000,
) -> tuple[list[dict[str, object]], int]:
    work_budget = _CompilationWorkBudget(maximum_work)
    resolved = mark_conflicts(
        items,
        limits=CompilationLimits(),
        work_budget=work_budget,
    )
    return [item.to_dict() for item in resolved], work_budget.used


class _MutatingBudget(_CompilationWorkBudget):
    def __init__(self, maximum: int, target: MemoryItem) -> None:
        super().__init__(maximum)
        self._target = target
        self._mutated = False

    def consume(self, amount: int = 1, *, phase: str) -> None:
        if not self._mutated:
            self._target.text = "constraint: tracing must be disabled in production"
            self._mutated = True
        super().consume(amount, phase=phase)


class ConflictFactReuseTests(unittest.TestCase):
    def test_dense_exact_items_match_the_observable_legacy_path(self) -> None:
        templates = (
            (MemoryKind.CONSTRAINT, "constraint: preserve marker {index} exactly"),
            (MemoryKind.CONFIRMED_FACT, "confirmed: worker {index} passed"),
            (MemoryKind.DECISION, "decision: use queue shard {index}"),
            (MemoryKind.CONSTRAINT, "constraint: tracing is enabled in scope {index}"),
        )
        items = [
            _item(index, template.format(index=index), kind)
            for index in range(192)
            for kind, template in (templates[index % len(templates)],)
        ]
        items.extend(
            (
                _item(1000, "constraint: database must be MySQL", MemoryKind.CONSTRAINT),
                _item(
                    1001,
                    "constraint: database must be PostgreSQL",
                    MemoryKind.CONSTRAINT,
                ),
                _item(
                    1002,
                    "constraint: tracing must be enabled in production",
                    MemoryKind.CONSTRAINT,
                ),
                _item(
                    1003,
                    "constraint: tracing must be disabled in production",
                    MemoryKind.CONSTRAINT,
                ),
                _item(
                    1004,
                    "constraint: timeout must be exactly 10 seconds",
                    MemoryKind.CONSTRAINT,
                ),
                _item(
                    1005,
                    "constraint: timeout must be exactly 20 seconds",
                    MemoryKind.CONSTRAINT,
                ),
            )
        )

        optimized, optimized_work = _resolve(_clone(items))
        original = resolver_module._proposition_shape
        with patch.object(
            resolver_module,
            "_proposition_shape",
            wraps=original,
        ):
            legacy, legacy_work = _resolve(_clone(items))

        self.assertEqual(optimized, legacy)
        self.assertEqual(optimized_work, legacy_work)

    def test_replaced_tokenizer_forces_the_legacy_path(self) -> None:
        items = [
            _item(
                index,
                f"constraint: preserve independent marker {index}",
                MemoryKind.CONSTRAINT,
            )
            for index in range(64)
        ]
        original = resolver_module.significant_tokens
        with patch.object(
            resolver_module,
            "significant_tokens",
            wraps=original,
        ) as observed:
            _resolve(items)

        self.assertGreater(observed.call_count, len(items))

    def test_work_limit_failure_and_accounting_match_legacy(self) -> None:
        items = [
            _item(
                index,
                f"constraint: preserve marker {index}",
                MemoryKind.CONSTRAINT,
            )
            for index in range(8)
        ]
        failures: list[tuple[str, int]] = []
        for legacy in (False, True):
            work_budget = _CompilationWorkBudget(5)
            original = resolver_module._proposition_shape
            context = (
                patch.object(
                    resolver_module,
                    "_proposition_shape",
                    wraps=original,
                )
                if legacy
                else patch.object(
                    resolver_module,
                    "_proposition_shape",
                    original,
                )
            )
            with context, self.assertRaises(CompilationLimitError) as captured:
                mark_conflicts(
                    _clone(items),
                    limits=CompilationLimits(),
                    work_budget=work_budget,
                )
            failures.append((str(captured.exception), work_budget.used))

        self.assertEqual(failures[0], failures[1])
        self.assertIn("during conflict detection", failures[0][0])

    def test_custom_budget_callbacks_retain_the_legacy_access_path(self) -> None:
        left = _item(
            0,
            "constraint: tracing must be enabled in production",
            MemoryKind.CONSTRAINT,
        )
        right = _item(
            1,
            "constraint: tracing must be enabled in production",
            MemoryKind.CONSTRAINT,
        )
        items = [left, right]
        work_budget = _MutatingBudget(100, right)

        resolved = mark_conflicts(
            items,
            limits=CompilationLimits(),
            work_budget=work_budget,
        )

        constraints = [
            item for item in resolved if item.kind is MemoryKind.CONSTRAINT
        ]
        self.assertTrue(all(item.conflicts_with for item in constraints))
        self.assertFalse(
            resolver_module._can_reuse_conflict_facts(
                items,
                limits=CompilationLimits(),
                work_budget=work_budget,
            )
        )

    def test_replaced_helper_forces_the_legacy_path(self) -> None:
        items = [
            _item(
                0,
                "constraint: tracing must be enabled in production",
                MemoryKind.CONSTRAINT,
            ),
            _item(
                1,
                "constraint: tracing must be disabled in production",
                MemoryKind.CONSTRAINT,
            ),
        ]
        original = resolver_module._structured_conflict
        with patch.object(
            resolver_module,
            "_structured_conflict",
            wraps=original,
        ) as observed:
            resolved, _work = _resolve(items)

        self.assertGreater(observed.call_count, 0)
        self.assertTrue(
            any(item["kind"] == MemoryKind.UNRESOLVED.value for item in resolved)
        )

    def test_zero_work_limit_precedes_a_replaced_database_callback(self) -> None:
        items = [
            _item(
                0,
                "constraint: database must be MySQL",
                MemoryKind.CONSTRAINT,
            ),
            _item(
                1,
                "constraint: database must be PostgreSQL",
                MemoryKind.CONSTRAINT,
            ),
        ]
        calls = 0

        def raising_database(_text: str) -> str | None:
            nonlocal calls
            calls += 1
            raise RuntimeError("database callback must not beat work admission")

        work_budget = _CompilationWorkBudget(0)
        with (
            patch.object(
                resolver_module,
                "_database_value",
                raising_database,
            ),
            self.assertRaises(CompilationLimitError) as captured,
        ):
            mark_conflicts(
                items,
                limits=CompilationLimits(),
                work_budget=work_budget,
            )

        self.assertEqual(calls, 0)
        self.assertEqual(work_budget.used, 0)
        self.assertIn("during conflict detection", str(captured.exception))

    def test_replaced_new_helper_cannot_change_conflict_output(self) -> None:
        items = [
            _item(
                0,
                "constraint: preserve independent alpha marker",
                MemoryKind.CONSTRAINT,
            ),
            _item(
                1,
                "constraint: preserve independent beta marker",
                MemoryKind.CONSTRAINT,
            ),
        ]
        with patch.object(
            resolver_module,
            "_facts_have_structured_conflict",
            return_value=True,
        ) as replaced:
            resolved, _work = _resolve(items)

        self.assertEqual(replaced.call_count, 0)
        self.assertFalse(
            any(item["kind"] == MemoryKind.UNRESOLVED.value for item in resolved)
        )

    def test_mutated_dependency_alias_forces_legacy_evaluation(self) -> None:
        items = [
            _item(
                0,
                "constraint: tracing must be enabled in production",
                MemoryKind.CONSTRAINT,
            ),
            _item(
                1,
                "constraint: tracing must be disabled in staging",
                MemoryKind.CONSTRAINT,
            ),
        ]
        scope_alias = resolver_module._SCOPE_VALUES
        original = scope_alias["production"]
        try:
            scope_alias["production"] = "staging"
            work_budget = _CompilationWorkBudget(100)
            self.assertFalse(
                resolver_module._can_reuse_conflict_facts(
                    items,
                    limits=CompilationLimits(),
                    work_budget=work_budget,
                )
            )
            resolved, work = _resolve(_clone(items))
            original_shape = resolver_module._proposition_shape
            with patch.object(
                resolver_module,
                "_proposition_shape",
                wraps=original_shape,
            ):
                legacy, legacy_work = _resolve(_clone(items))
        finally:
            scope_alias["production"] = original

        self.assertEqual(resolved, legacy)
        self.assertEqual(work, legacy_work)

    def test_duplicate_mutable_item_aliases_never_enter_the_fact_cache(self) -> None:
        item = _item(
            0,
            "constraint: preserve the shared mutable item",
            MemoryKind.CONSTRAINT,
        )
        items = [item, item]
        work_budget = _CompilationWorkBudget(100)

        self.assertFalse(
            resolver_module._can_reuse_conflict_facts(
                items,
                limits=CompilationLimits(),
                work_budget=work_budget,
            )
        )

    def test_concurrent_helper_replacement_remains_on_legacy_path(self) -> None:
        items = [
            _item(
                0,
                "constraint: preserve independent alpha marker",
                MemoryKind.CONSTRAINT,
            ),
            _item(
                1,
                "constraint: preserve independent beta marker",
                MemoryKind.CONSTRAINT,
            ),
        ]
        installed = threading.Event()
        release = threading.Event()
        calls = 0

        def replacement(
            _left: resolver_module._ConflictFacts,
            _right: resolver_module._ConflictFacts,
            *,
            dependencies: resolver_module._ConflictDependencies,
        ) -> bool:
            del dependencies
            nonlocal calls
            calls += 1
            return True

        def replace_during_call() -> None:
            with patch.object(
                resolver_module,
                "_facts_have_structured_conflict",
                replacement,
            ):
                installed.set()
                release.wait(timeout=5)

        worker = threading.Thread(target=replace_during_call)
        worker.start()
        self.assertTrue(installed.wait(timeout=5))
        try:
            resolved, _work = _resolve(items)
        finally:
            release.set()
            worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        self.assertEqual(calls, 0)
        self.assertFalse(
            any(item["kind"] == MemoryKind.UNRESOLVED.value for item in resolved)
        )

    def test_replaced_work_callback_matches_legacy_timing_and_accounting(self) -> None:
        base = [
            _item(
                0,
                "constraint: tracing must be enabled in production",
                MemoryKind.CONSTRAINT,
            ),
            _item(
                1,
                "constraint: tracing must be enabled in production",
                MemoryKind.CONSTRAINT,
            ),
        ]
        original_consume = _CompilationWorkBudget.consume

        def observe(
            force_legacy: bool,
        ) -> tuple[list[dict[str, object]], int, int]:
            items = _clone(base)
            mutation_target = items[1]
            callbacks = 0

            def mutating_consume(
                budget: _CompilationWorkBudget,
                amount: int = 1,
                *,
                phase: str,
            ) -> None:
                nonlocal callbacks
                callbacks += 1
                if callbacks == 1:
                    mutation_target.text = (
                        "constraint: tracing must be disabled in production"
                    )
                original_consume(budget, amount, phase=phase)

            shape_context = (
                patch.object(
                    resolver_module,
                    "_proposition_shape",
                    wraps=resolver_module._proposition_shape,
                )
                if force_legacy
                else patch.object(
                    resolver_module,
                    "_proposition_shape",
                    resolver_module._proposition_shape,
                )
            )
            work_budget = _CompilationWorkBudget(100)
            with (
                patch.object(
                    _CompilationWorkBudget,
                    "consume",
                    mutating_consume,
                ),
                shape_context,
            ):
                resolved = mark_conflicts(
                    items,
                    limits=CompilationLimits(),
                    work_budget=work_budget,
                )
            return (
                [item.to_dict() for item in resolved],
                work_budget.used,
                callbacks,
            )

        observations = [observe(False), observe(True)]
        self.assertEqual(observations[0], observations[1])
        self.assertEqual(observations[0][1:], (1, 1))
        self.assertTrue(
            any(
                item["kind"] == MemoryKind.UNRESOLVED.value
                for item in observations[0][0]
            )
        )

    def test_post_admission_constructor_replacement_cannot_redirect_facts(self) -> None:
        items = [
            _item(
                0,
                "constraint: database must be MySQL",
                MemoryKind.CONSTRAINT,
            ),
            _item(
                1,
                "constraint: database must be PostgreSQL",
                MemoryKind.CONSTRAINT,
            ),
        ]
        expected = _resolve(_clone(items))
        original_gate = resolver_module._can_reuse_conflict_facts
        original_type = resolver_module._DEFAULT_CONFLICT_FACTS_TYPE

        class ReplacementFacts:
            def __init__(self, *args, **kwargs) -> None:
                del args, kwargs
                raise AssertionError("replacement facts constructor must not run")

        def admit_then_replace(*args, **kwargs) -> bool:
            admitted = original_gate(*args, **kwargs)
            self.assertTrue(admitted)
            resolver_module._DEFAULT_CONFLICT_FACTS_TYPE = ReplacementFacts
            return admitted

        try:
            with patch.object(
                resolver_module,
                "_can_reuse_conflict_facts",
                side_effect=admit_then_replace,
            ):
                actual = _resolve(_clone(items))
        finally:
            resolver_module._DEFAULT_CONFLICT_FACTS_TYPE = original_type

        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
